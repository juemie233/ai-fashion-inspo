"""标签 CRUD：创建、更新、删除、合并、批量操作与预设标签导入。

标签域的**底层**模块：只依赖数据模型与工具函数，供 tag_alias / tag_inspirations /
tag_query 复用，自身不依赖任何业务服务模块。
"""

import asyncio
import logging

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.tag import Tag, InspirationTag, TagAlias
from app.services.audit_service import record_audit_log
from app.services.tag_history_service import (
    new_batch_id,
    record_history,
    snapshot_tag,
    snapshot_tags,
)
from app.utils.tag_normalizer import normalize_tag_name_async

logger = logging.getLogger(__name__)


class TagNotFoundError(Exception):
    """标签或关联对象不存在（路由层转为 404）。"""

    def __init__(self, message: str = "标签未找到") -> None:
        super().__init__(message)
        self.message = message


class TagConflictError(Exception):
    """标签名称或别名冲突（路由层转为 409）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# 预设标签体系（按类别组织）
SEED_TAGS: dict[str, list[str]] = {
    "style": [
        "JK制服", "汉服", "Lolita", "Y2K", "CleanFit", "法式", "日系",
        "韩系", "学院风", "Gorpcore", "街头", "新中式", "复古", "极简",
        "美式复古", "英伦风", "波西米亚", "运动风", "甜美风", "暗黑风",
    ],
    "item_type": [
        "百褶裙", "过膝袜", "水手服", "西装外套", "阔腿裤", "马丁靴",
        "贝雷帽", "白衬衫", "卫衣", "牛仔裤", "半身裙", "连衣裙",
        "针织衫", "风衣", "羽绒服", "T恤", "背心", "短裤", "高跟鞋",
        "运动鞋", "乐福鞋", "玛丽珍鞋", "帆布鞋", "包包", "腰带", "围巾",
    ],
    "color": [
        "白色", "黑色", "灰色", "米色", "棕色", "海军蓝", "酒红",
        "粉色", "红色", "橙色", "黄色", "绿色", "蓝色", "紫色",
        "卡其色", "牛仔蓝", "格纹", "条纹", "碎花", "豹纹",
    ],
    "body_part": [
        "过膝", "露腰", "高腰", "V领", "圆领", "高领", "一字肩",
        "七分袖", "长袖", "短袖", "无袖", "拖地", "九分", "七分",
        "及膝", "迷你", "中长款", "长款", "短款",
    ],
    "fit": [
        "宽松", "修身", "Oversized", "直筒", "紧身", "A字", "H型",
        "X型", "喇叭", "锥形", "阔腿",
    ],
    "attribute": [
        "露脸", "不露脸", "全身", "半身", "坐姿", "站姿",
        "对镜自拍", "他拍", "叠穿", "单穿", "街拍", "棚拍",
    ],
}


async def _rebuild_vectors_for_tag_change(
    db: AsyncSession, inspiration_ids: list[str]
) -> None:
    """标签变更（合并/删除/重命名/解除关联）后，为受影响素材重建文本向量。

    语义搜索的文本向量基于素材标签名拼接生成，标签变更会使其陈旧；这里把
    受影响素材登记到向量回填攒批队列（累计达到阈值后由 worker 统一创建批量
    任务执行，不再每素材一个任务），登记失败静默降级，不影响标签操作主流程。
    """
    ids = list(dict.fromkeys(inspiration_ids))
    if not ids:
        return
    try:
        from app.services.task_runners.vector_backfill import enqueue_vector_backfills

        await enqueue_vector_backfills(db, ids)
    except Exception as e:
        logger.warning(f"标签变更后向量重建登记失败（忽略）: {e}")


def _similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度（0-1），复用 tag_normalizer 的统一实现。"""
    from app.utils.tag_normalizer import string_similarity

    return string_similarity(a, b)


def _alive_tag_links_subquery():
    """返回「关联了未删除素材」的标签 id 子查询。

    与使用次数（usage_count）口径完全一致：仅统计未删除素材的关联，
    垃圾桶素材与孤儿关联（素材行已不存在）不计入。
    未使用标签 = 不在此子查询中的标签（无任何可见素材引用）。
    """
    return (
        select(InspirationTag.tag_id)
        .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
        .where(Inspiration.deleted_at.is_(None))
        .distinct()
    )


async def seed_tags(db: AsyncSession) -> int:
    """导入预设标签（仅空库首次初始化时执行）。返回新增标签数量。

    历史问题：本函数每次后端启动都被调用（跳过已存在名称）——用户删除
    某个预设标签（如「Lolita」）后，下次启动会被当作「不存在」重新创建，
    导致已删除的标签反复恢复（标签管理页「未使用标签」反复出现）。

    修复：仅当标签表为空（首次初始化/全新库）时导入预设；
    只要库里已有任何标签（用户使用中的正常状态），不再补 seed——
    用户删除的预设标签保持删除状态，不会被重建。
    """
    count = (await db.execute(select(func.count(Tag.id)))).scalar() or 0
    if count > 0:
        return 0

    all_names = [name for names in SEED_TAGS.values() for name in names]
    existing = await db.execute(select(Tag.name).where(Tag.name.in_(all_names)))
    existing_names = set(existing.scalars().all())
    added = 0
    for category, names in SEED_TAGS.items():
        for name in names:
            if name in existing_names:
                continue
            db.add(Tag(name=name, category=category))
            added += 1
    if added:
        await db.flush()
    return added


async def get_or_create_tag(
    db: AsyncSession, name: str, category: str = "free", source: str = "manual"
) -> Tag:
    """按名称查找已有标签，不存在则创建新标签。

    处理并发竞态：两任务同时创建同一标签时，先 flush 的一方成功，
    后 flush 的一方触发 IntegrityError。捕获后回滚当前事务并重新查询。

    创建前先做别名归一化（DB 别名 → 硬编码同义词），使「纯白」自动落到「白色」。
    """
    name = await normalize_tag_name_async(db, name.strip())
    result = await db.execute(select(Tag).where(Tag.name == name))
    tag = result.scalar_one_or_none()
    if not tag:
        tag = Tag(name=name, category=category, source=source)
        db.add(tag)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # SAVEPOINT 已回滚（不影响同事务其它已 flush 的标签），移除失败对象后重查
            db.expunge(tag)
            logger.debug(f"并发创建标签冲突: {name!r}，回退查询")
            result = await db.execute(select(Tag).where(Tag.name == name))
            tag = result.scalar_one_or_none()
            if not tag:
                # 极端情况：重查仍未找到，再试一次（小概率）。同样用 SAVEPOINT
                # 隔离，避免再次并发冲突直接 500
                tag = Tag(name=name, category=category, source=source)
                db.add(tag)
                try:
                    async with db.begin_nested():
                        await db.flush()
                except IntegrityError:
                    db.expunge(tag)
                    result = await db.execute(select(Tag).where(Tag.name == name))
                    tag = result.scalar_one_or_none()
                    if not tag:
                        raise
    return tag


async def find_similar_tags(
    db: AsyncSession, name: str, threshold: float = 0.75
) -> list[Tag]:
    """查找与给定名称相似的已有标签（用于去重建议）。

    全库两两比较是 O(n²) 同步计算，放入线程池执行避免阻塞事件循环
    （与 find_duplicate_tag_pairs 一致）。
    """
    result = await db.execute(select(Tag))
    all_tags = result.scalars().all()

    def _compute_similar() -> list[Tag]:
        similar = []
        for tag in all_tags:
            sim = _similarity(name, tag.name)
            if sim >= threshold and sim < 1.0:
                similar.append(tag)
        return sorted(similar, key=lambda t: _similarity(name, t.name), reverse=True)

    return await asyncio.to_thread(_compute_similar)


async def _collect_links(db: AsyncSession, tag_ids: list[int]) -> list[dict]:
    """收集标签的全部素材关联明细（回滚重建关联用）。

    删除标签会级联物理删除 inspiration_tags 行；回滚时仅重建 Tag 行是不够的，
    需要把删除前的关联（素材/置信度/来源）留底在历史 meta 中，回滚一并恢复。
    """
    ids = [t for t in tag_ids if t]
    if not ids:
        return []
    result = await db.execute(
        select(
            InspirationTag.inspiration_id,
            InspirationTag.tag_id,
            InspirationTag.confidence,
            InspirationTag.source,
        ).where(InspirationTag.tag_id.in_(ids))
    )
    return [
        {
            "inspiration_id": r[0],
            "tag_id": r[1],
            "confidence": r[2],
            "source": r[3],
        }
        for r in result.all()
    ]


async def merge_tags(
    db: AsyncSession,
    source_id: int,
    target_id: int,
    batch_id: str | None = None,
) -> None:
    """将源标签合并到目标标签：重新关联所有素材，删除源标签。

    参数:
        batch_id: 操作历史批次 ID；不传时每次合并独立成批（聚类 apply 传共享批次）。
    """
    # 查找源标签的所有关联
    result = await db.execute(
        select(InspirationTag).where(InspirationTag.tag_id == source_id)
    )
    links = result.scalars().all()
    # 收集受影响素材：合并会改变这些素材的标签集合，需重建其文本向量
    affected_ids = [link.inspiration_id for link in links]

    # 一次性查出已关联目标标签的素材集合，避免逐条 N+1 查询
    existing_result = await db.execute(
        select(InspirationTag.inspiration_id).where(
            InspirationTag.inspiration_id.in_(affected_ids),
            InspirationTag.tag_id == target_id,
        )
    )
    already_linked = set(existing_result.scalars().all())

    # 合并前快照 + 关联明细（供操作历史与回滚精确恢复）
    before_snap = await snapshot_tags(db, [source_id, target_id])
    merged_link_ids = [
        link.inspiration_id for link in links if link.inspiration_id not in already_linked
    ]
    duplicate_link_ids = [
        link.inspiration_id for link in links if link.inspiration_id in already_linked
    ]

    for link in links:
        if link.inspiration_id in already_linked:
            # 重复关联 — 删除源标签的关联
            await db.delete(link)
        else:
            # 重定向到目标标签。用 SAVEPOINT 隔离提交：并发合并同一素材到
            # 同一目标标签时，后到者触发唯一约束，仅回滚该条而非整个事务
            link.tag_id = target_id
            try:
                async with db.begin_nested():
                    await db.flush()
            except IntegrityError:
                # SAVEPOINT 已回滚，重查后按「已关联」处理（跳过本条）
                db.expunge(link)
                existing_link = await db.execute(
                    select(InspirationTag.id).where(
                        InspirationTag.inspiration_id == link.inspiration_id,
                        InspirationTag.tag_id == target_id,
                    )
                )
                if existing_link.scalar_one_or_none() is None:
                    raise

    # 删除源标签
    source_tag = await db.get(Tag, source_id)
    moved_alias_ids: list[int] = []
    if source_tag:
        # 注意：Tag.aliases 关系声明了 cascade="all, delete-orphan"，
        # 直接 delete 源标签会物理删除其全部别名（数据丢失），
        # 因此必须先手动把源标签的别名搬迁到目标标签（与目标已有别名去重）。
        target_aliases = (
            await db.execute(select(TagAlias.alias).where(TagAlias.tag_id == target_id))
        ).scalars().all()
        target_alias_set = set(target_aliases)

        source_aliases = (
            await db.execute(select(TagAlias).where(TagAlias.tag_id == source_id))
        ).scalars().all()
        for alias in source_aliases:
            if alias.alias in target_alias_set:
                # 目标标签已存在同名字别名，删除该条，避免唯一约束冲突
                await db.delete(alias)
            else:
                # 重指向目标标签，随源标签删除而保留（避免级联物理删除）
                alias.tag_id = target_id
                moved_alias_ids.append(alias.id)
                target_alias_set.add(alias.alias)

        # 先刷新，确保别名搬迁（tag_id 重指向）先落库，
        # 否则 delete-orphan 级联在删除源标签时会重新加载仍指向源标签的别名并物理删除。
        await db.flush()

        await db.delete(source_tag)

    await db.flush()

    # 合并后为受影响素材重建文本向量（异步入队，由 worker 执行）
    await _rebuild_vectors_for_tag_change(db, affected_ids)

    # 先提交主事务再写审计：audit 用独立会话写库，若在未提交事务内调用，
    # 独立会话会被本事务持有的 SQLite 写锁阻塞到 busy_timeout（30s）→ 请求超时。
    # 与 batch_delete_tags / delete_unused_tags 的「先提交、后留痕」模式保持一致。
    await db.commit()

    # 记录审计：合并标签属破坏性批量操作（删除源标签、重定向关联），留痕便于追溯
    await record_audit_log(
        action="merge_tags",
        target_type="tags",
        count=len(affected_ids),
        detail=f"标签 {source_id} 合并到 {target_id}",
    )

    # 清除重复扫描缓存（标签数据已变更）
    from app.services.tag_dedupe_cache import clear_all
    clear_all()

    # 记录操作历史（merge 已内部提交，此处独立提交 history 行）
    after_snap = await snapshot_tags(db, [target_id])
    after_snap[source_id] = {
        "deleted": True,
        "name": before_snap.get(source_id, {}).get("name", ""),
    }
    await record_history(
        db,
        operation="merge",
        before=before_snap,
        after=after_snap,
        batch_id=batch_id or new_batch_id("merge"),
        meta={
            "source_tag_id": source_id,
            "target_tag_id": target_id,
            "merged_link_ids": merged_link_ids,
            "duplicate_link_ids": duplicate_link_ids,
            "moved_alias_ids": moved_alias_ids,
        },
    )
    await db.commit()


async def create_tag(db: AsyncSession, name: str, category: str = "free") -> Tag:
    """创建自定义标签（先做别名归一化，再按规范名查重）。

    名称已存在时抛 TagConflictError；与既有标签别名同名也抛 TagConflictError
    （否则别名归一化优先命中别名，新建的主标签永远不被 AI 打标命中，成为死标签）；
    strip 后为空名直接拒绝。

    参数:
        name: 原始输入标签名（归一化前的名称，用于冲突提示文案）
        category: 标签类别
    """
    raw_name = name
    name = (await normalize_tag_name_async(db, name)).strip()
    if not name:
        raise HTTPException(status_code=400, detail="标签名称不能为空")
    result = await db.execute(select(Tag).where(Tag.name == name))
    if result.scalar_one_or_none():
        raise TagConflictError(f"标签 '{raw_name}' 已存在")
    alias_conflict = await db.execute(select(TagAlias).where(TagAlias.alias == name))
    if alias_conflict.scalar_one_or_none():
        raise TagConflictError(f"标签名 '{raw_name}' 已作为其它标签的别名使用")

    tag = Tag(name=name, category=category, source="manual")
    db.add(tag)
    await db.flush()
    await db.refresh(tag)
    # 记录操作历史（随调用方事务一并提交）
    await record_history(
        db, operation="create", before={}, after={tag.id: await snapshot_tag(db, tag.id)}
    )
    return tag


async def update_tag(
    db: AsyncSession,
    tag_id: int,
    name: str | None = None,
    category: str | None = None,
    pinned: bool | None = None,
    sort_order: int | None = None,
    description: str | None = None,
) -> Tag:
    """更新标签字段并返回更新后的标签。

    标签不存在抛 TagNotFoundError；改名与已有主标签或别名冲突抛 TagConflictError。

    参数:
        db: 数据库会话
        tag_id: 标签 ID
        name: 新名称（先归一化，再查主标签/别名冲突）
        category: 新类别
        pinned: 是否置顶
        sort_order: 自定义排序权重
        description: 备注
    """
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise TagNotFoundError("标签未找到")

    # 操作前快照（供操作历史与回滚冲突检测）
    before_snap = await snapshot_tag(db, tag_id) or {}

    renamed = False
    if name is not None:
        new_name = (await normalize_tag_name_async(db, name)).strip()
        conflict = await db.execute(
            select(Tag).where(Tag.name == new_name, Tag.id != tag_id)
        )
        if conflict.scalar_one_or_none():
            raise TagConflictError(f"标签 '{name}' 已存在")
        alias_conflict = await db.execute(
            select(TagAlias).where(TagAlias.alias == new_name)
        )
        if alias_conflict.scalar_one_or_none():
            raise TagConflictError(f"标签名 '{name}' 已作为其它标签的别名使用")
        renamed = new_name != tag.name
        tag.name = new_name

    if category is not None:
        tag.category = category
    if pinned is not None:
        tag.pinned = pinned
    if sort_order is not None:
        tag.sort_order = sort_order
    if description is not None:
        tag.description = description

    await db.flush()
    await db.refresh(tag)

    # 单条改名后标签名变了：文本向量（基于标签名拼接生成）需重建，
    # 与 batch_rename_tags 的语义保持一致。登记到攒批队列由 worker 执行。
    # （update_tag 只 flush 未提交，登记行与改名随调用方事务一并落库）
    if name is not None and renamed:
        affected_ids = (
            await db.execute(
                select(InspirationTag.inspiration_id)
                .where(InspirationTag.tag_id == tag_id)
                .distinct()
            )
        ).scalars().all()
        await _rebuild_vectors_for_tag_change(db, affected_ids)

    # 记录操作历史（随调用方事务一并提交；未发生实际变更则不记录）
    after_snap = await snapshot_tag(db, tag_id) or {}
    if after_snap != before_snap:
        if renamed:
            op = "rename"
        elif category is not None and before_snap.get("category") != category:
            op = "category_change"
        else:
            op = "update"
        await record_history(
            db,
            operation=op,
            before={tag_id: before_snap},
            after={tag_id: after_snap},
            meta={"request": {"name": name, "category": category, "pinned": pinned,
                              "sort_order": sort_order, "description": description}}
            if op == "update"
            else None,
        )

    return tag


async def delete_unused_tags(db: AsyncSession) -> list[Tag]:
    """删除所有「未使用」标签，返回被删除的标签列表。

    未使用口径与使用次数一致：没有任何未删除素材关联的标签
    （含只关联垃圾桶素材、仅残留孤儿关联的标签），连同其残留关联一并清理。
    """
    result = await db.execute(
        select(Tag).where(Tag.id.notin_(_alive_tag_links_subquery()))
    )
    unused = result.scalars().all()

    if not unused:
        return []

    # 删除前收集受影响素材（含只关联垃圾桶素材的标签——恢复后其文本向量
    # 含已删标签名，需一并登记重建，与 batch_delete_tags 语义一致）
    unused_ids = [t.id for t in unused]
    affected_ids = (
        await db.execute(
            select(InspirationTag.inspiration_id)
            .where(InspirationTag.tag_id.in_(unused_ids))
            .distinct()
        )
    ).scalars().all()

    # 删除前快照（供操作历史与回滚重建标签）
    before_snap = await snapshot_tags(db, unused_ids)

    # 删除前收集关联明细（供回滚重建素材-标签关联：Tag.inspirations 级联
    # delete-orphan 会物理删除关联行，若不在 meta 里留底，回滚后关联永久丢失）
    deleted_links = await _collect_links(db, unused_ids)

    # 先删关联表中的残留记录（防御性清理），再删标签
    await db.execute(
        delete(InspirationTag).where(InspirationTag.tag_id.in_(unused_ids))
    )
    await db.execute(delete(Tag).where(Tag.id.in_(unused_ids)))
    await db.commit()

    # 受影响素材的标签集合变了，重建其文本向量（异步入队，显式提交登记行）
    await _rebuild_vectors_for_tag_change(db, affected_ids)
    await db.commit()

    # 记录审计：批量删除未使用标签属破坏性批量操作，留痕便于追溯
    await record_audit_log(
        action="delete_unused_tags",
        target_type="tags",
        count=len(unused),
        detail=f"删除未使用标签 {[t.name for t in unused]}",
    )
    # 清除重复扫描缓存（标签数据已变更）
    from app.services.tag_dedupe_cache import clear_all
    clear_all()
    # 记录操作历史（删除为破坏性操作，写快照支持回滚重建）
    after_snap = {
        tid: {"deleted": True, "name": before_snap[tid]["name"]} for tid in before_snap
    }
    await record_history(
        db,
        operation="delete",
        before=before_snap,
        after=after_snap,
        batch_id=new_batch_id("delete"),
        meta={"deleted_links": deleted_links},
    )
    await db.commit()
    return unused


async def batch_delete_tags(db: AsyncSession, tag_ids: list[int]) -> list[Tag]:
    """批量删除标签及其所有关联，返回被删除的标签列表。"""
    result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids)))
    tags = result.scalars().all()
    # 删除前快照（供操作历史与回滚重建标签）
    before_snap = await snapshot_tags(db, [t.id for t in tags])
    # 删除前收集关联明细（供回滚重建素材-标签关联，避免级联删除后永久丢失）
    deleted_links = await _collect_links(db, [t.id for t in tags])
    # 删除前收集受影响素材（被删标签的关联），删除后用于重建文本向量
    affected_ids = (
        await db.execute(
            select(InspirationTag.inspiration_id)
            .where(InspirationTag.tag_id.in_(tag_ids))
            .distinct()
        )
    ).scalars().all()
    for tag in tags:
        await db.delete(tag)
    await db.flush()
    # 删除标签后素材标签集合变了，重建其文本向量（异步入队）
    await _rebuild_vectors_for_tag_change(db, affected_ids)
    await db.commit()
    # 记录审计：批量删除标签属破坏性批量操作，留痕便于追溯
    await record_audit_log(
        action="batch_delete_tags",
        target_type="tags",
        count=len(tags),
        detail=f"删除标签 {[t.name for t in tags]}",
    )
    # 清除重复扫描缓存（标签数据已变更）
    from app.services.tag_dedupe_cache import clear_all
    clear_all()
    # 记录操作历史（删除为破坏性操作，写快照支持回滚重建）
    after_snap = {
        tid: {"deleted": True, "name": before_snap[tid]["name"]} for tid in before_snap
    }
    await record_history(
        db,
        operation="delete",
        before=before_snap,
        after=after_snap,
        batch_id=new_batch_id("delete"),
        meta={"deleted_links": deleted_links},
    )
    await db.commit()
    return tags


async def merge_tag_pair(
    db: AsyncSession, source_tag_id: int, target_tag_id: int
) -> tuple[str, str]:
    """合并前校验源/目标标签存在并执行合并，返回 (源标签名, 目标标签名)。

    源或目标标签不存在时抛 TagNotFoundError。
    """
    source = await db.get(Tag, source_tag_id)
    target = await db.get(Tag, target_tag_id)
    if not source:
        raise TagNotFoundError(f"源标签 {source_tag_id} 未找到")
    if not target:
        raise TagNotFoundError(f"目标标签 {target_tag_id} 未找到")

    await merge_tags(db, source_tag_id, target_tag_id)
    return source.name, target.name


async def batch_change_category(
    db: AsyncSession, tag_ids: list[int], category: str
) -> int:
    """批量修改标签类别，返回受影响行数。"""
    result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids)))
    tags = result.scalars().all()
    # 只记录类别实际发生变化的标签（操作历史与回滚）
    changed_ids = [t.id for t in tags if t.category != category]
    before_snap = await snapshot_tags(db, changed_ids)

    result = await db.execute(
        update(Tag).where(Tag.id.in_(tag_ids)).values(category=category)
    )
    await db.commit()

    # 记录操作历史（随批次分组）
    if before_snap:
        after_snap = {tid: {**snap, "category": category} for tid, snap in before_snap.items()}
        await record_history(
            db,
            operation="category_change",
            before=before_snap,
            after=after_snap,
            batch_id=new_batch_id("cat"),
        )
        await db.commit()
    return result.rowcount


async def batch_rename_tags(
    db: AsyncSession, tag_ids: list[int], find_str: str, replace_str: str
) -> int:
    """批量重命名标签（查找替换），返回实际更新数。

    预检新名称冲突（不执行任何修改）：
    - 改名结果为空名 → 400（禁止空名标签）
    - 批内标签改名后互相同名（如 A="ff"、B="f"，find="f" replace=""）→ 409
    - 与 DB 中其它标签同名 → 409
    - 与任一标签别名同名 → 409
    """
    result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids)))
    tags = result.scalars().all()
    # 操作前快照（供操作历史与回滚）
    before_snap = await snapshot_tags(db, [t.id for t in tags])

    # 计算批内全部改名结果（批内新名冲突必须整体预检，不能逐条查 DB）
    rename_map: dict[int, str] = {}
    for tag in tags:
        if find_str in tag.name:
            new_name = tag.name.replace(find_str, replace_str)
            if not new_name.strip():
                raise HTTPException(status_code=400, detail="重命名结果为空名，禁止创建空名标签")
            if new_name != tag.name:
                rename_map[tag.id] = new_name

    # 批内互相同名：两个不同标签改名后撞名
    seen_names: dict[str, int] = {}
    for tid, new_name in rename_map.items():
        if new_name in seen_names:
            raise TagConflictError(
                f"重命名冲突: 标签 '{seen_names[new_name]}' 与 '{tid}' 改名后同为 '{new_name}'"
            )
        seen_names[new_name] = tid

    if rename_map:
        # 与 DB 中其它标签同名（排除本批改名对象）
        conflict = await db.execute(
            select(Tag.id).where(
                Tag.name.in_(rename_map.values()),
                Tag.id.notin_(rename_map.keys()),
            )
        )
        if conflict.scalars().first():
            raise TagConflictError("重命名冲突: 新名称与已有标签同名")
        # 与标签别名同名（别名会与主标签名产生歧义）
        alias_conflict = await db.execute(
            select(TagAlias.alias).where(TagAlias.alias.in_(rename_map.values()))
        )
        if alias_conflict.scalars().first():
            raise TagConflictError("重命名冲突: 新名称与已有标签别名同名")

    updated = 0
    renamed_tag_ids: list[int] = []
    for tag in tags:
        if tag.id in rename_map:
            tag.name = rename_map[tag.id]
            renamed_tag_ids.append(tag.id)
            updated += 1
    await db.commit()

    # 重命名后标签名变了，文本向量（基于标签名拼接）需重建，异步入队
    # （enqueue 不内部提交，此处登记行需显式提交；素材变更已在上方 commit）
    if renamed_tag_ids:
        affected_ids = (
            await db.execute(
                select(InspirationTag.inspiration_id)
                .where(InspirationTag.tag_id.in_(renamed_tag_ids))
                .distinct()
            )
        ).scalars().all()
        await _rebuild_vectors_for_tag_change(db, affected_ids)
        await db.commit()
    # 清除重复扫描缓存（标签名称已变更）
    if renamed_tag_ids:
        from app.services.tag_dedupe_cache import clear_all
        clear_all()
    # 记录操作历史（批量查找替换重命名，随批次分组）
    if rename_map:
        after_snap = {
            tid: {**before_snap[tid], "name": rename_map[tid]} for tid in rename_map
        }
        await record_history(
            db,
            operation="rename",
            before={tid: before_snap[tid] for tid in rename_map},
            after=after_snap,
            batch_id=new_batch_id("rename"),
            meta={"find": find_str, "replace": replace_str},
        )
        await db.commit()
    return updated


async def import_tags(
    db: AsyncSession, items: list[tuple[str, str]]
) -> tuple[int, int]:
    """批量导入标签（跳过已存在的标签），返回 (导入数, 跳过数)。

    参数:
        items: (标签名, 类别) 列表

    说明:
        - 批内重复名称先去重（避免同批 flush 时撞唯一约束 500）
        - 空名 / 纯空白名称跳过
        - 统一走别名归一化（与 create_tag 一致），避免导入未归一化的脏名
    """
    imported = 0
    skipped = 0
    seen: set[str] = set()
    for raw_name, category in items:
        name = (await normalize_tag_name_async(db, raw_name.strip())).strip()
        if not name:
            skipped += 1
            continue
        if name in seen:  # 批内重复：跳过
            skipped += 1
            continue
        seen.add(name)
        existing = await db.execute(select(Tag).where(Tag.name == name))
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        tag = Tag(name=name, category=category, source="manual")
        db.add(tag)
        imported += 1

    await db.flush()
    return imported, skipped


async def reorder_tags(
    db: AsyncSession, order_map: dict[int, int]
) -> tuple[int, list[int]]:
    """批量更新标签自定义排序权重。

    返回:
        (更新数量, 缺失的标签 ID 列表)——缺失列表为空表示全部成功。
    """
    result = await db.execute(select(Tag).where(Tag.id.in_(order_map.keys())))
    tags = result.scalars().all()
    found_ids = {t.id for t in tags}
    missing_ids = [i for i in order_map if i not in found_ids]
    if missing_ids:
        return 0, missing_ids
    for tag in tags:
        tag.sort_order = order_map[tag.id]
    await db.commit()
    return len(tags), []


async def move_tags(
    db: AsyncSession, moves: list[dict]
) -> tuple[int, list[dict]]:
    """批量移动标签层级（parent_id），含循环检测；写操作历史。

    参数:
        moves: [{"tag_id": int, "parent_id": int | None}]；parent_id=None 表示移到根。

    返回:
        (移动成功数, 错误列表 [{"tag_id", "message"}])
    """
    if not moves:
        return 0, [{"tag_id": None, "message": "未提供移动项"}]

    tag_ids = [m["tag_id"] for m in moves]
    result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids)))
    tag_map = {t.id: t for t in result.scalars().all()}

    # 现有 parent 关系（循环检测用：沿新父节点向上走祖先链）
    rel_result = await db.execute(select(Tag.id, Tag.parent_id))
    parent_map = {tid: pid for tid, pid in rel_result.all()}
    # 批内已计划的 parent 关系（随校验通过的 move 逐步更新）：
    # 批量互移（如 A→B 且 B→A 同批）若各自基于旧快照检测会双双通过、执行后成环，
    # 因此环检测必须沿「含批内计划」的关系图上溯。
    planned_parent = dict(parent_map)

    valid: list[dict] = []
    errors: list[dict] = []
    for m in moves:
        tid = m["tag_id"]
        pid = m.get("parent_id")
        if tid not in tag_map:
            errors.append({"tag_id": tid, "message": "标签不存在"})
            continue
        if pid == tid:
            errors.append({"tag_id": tid, "message": "不能移动到自身下面"})
            continue
        if pid is not None and pid not in parent_map:
            errors.append({"tag_id": tid, "message": f"父标签 {pid} 不存在"})
            continue
        # 循环检测：沿「批内已计划的新父链」上溯，不得出现 tid
        cur = pid
        seen: set[int] = set()
        cyclic = False
        while cur is not None:
            if cur == tid:
                cyclic = True
                break
            if cur in seen:
                break  # 防御既有数据环路
            seen.add(cur)
            cur = planned_parent.get(cur)
        if cyclic:
            errors.append({"tag_id": tid, "message": "不能移动到自己的后代标签下"})
            continue
        valid.append(m)
        # 校验通过即纳入批内计划，后续 move 的环检测基于更新后的关系
        planned_parent[tid] = pid

    if valid:
        before_snap = await snapshot_tags(db, [m["tag_id"] for m in valid])
        for m in valid:
            tag_map[m["tag_id"]].parent_id = m.get("parent_id")
        await db.flush()
        after_snap = await snapshot_tags(db, [m["tag_id"] for m in valid])
        await record_history(
            db,
            operation="move",
            before=before_snap,
            after=after_snap,
            batch_id=new_batch_id("move"),
        )
        await db.commit()

    return len(valid), errors
