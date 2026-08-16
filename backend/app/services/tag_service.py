"""标签服务：CRUD、标准化、合并以及预设数据导入。"""

import asyncio
import itertools
import logging
from collections import defaultdict
from difflib import SequenceMatcher
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.tag import Tag, InspirationTag, TagAlias
from app.utils.tag_normalizer import normalize_tag_name_async

logger = logging.getLogger(__name__)


async def _rebuild_vectors_for_tag_change(
    db: AsyncSession, inspiration_ids: list[str]
) -> None:
    """标签变更（合并/删除/重命名/解除关联）后，为受影响素材重建文本向量。

    语义搜索的文本向量基于素材标签名拼接生成，标签变更会使其陈旧；这里把
    受影响素材入队到向量回填任务（由 worker 异步执行），入队失败静默降级，
    不影响标签操作主流程。
    """
    ids = list(dict.fromkeys(inspiration_ids))
    if not ids:
        return
    try:
        from app.services.task_runners.vector_backfill import create_vector_backfill_task

        await create_vector_backfill_task(db, ids)
    except Exception as e:
        logger.warning(f"标签变更后向量重建入队失败（忽略）: {e}")


class TagNotFoundError(Exception):
    """标签或关联对象不存在（路由层转为 404）。"""

    def __init__(self, message: str = "标签未找到"):
        super().__init__(message)
        self.message = message


class TagConflictError(Exception):
    """标签名称或别名冲突（路由层转为 409）。"""

    def __init__(self, message: str):
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


def _similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度（0-1）。"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


async def seed_tags(db: AsyncSession) -> int:
    """导入预设标签（跳过已存在的标签）。返回新增标签数量。"""
    added = 0
    for category, names in SEED_TAGS.items():
        for name in names:
            existing = await db.execute(select(Tag).where(Tag.name == name))
            if not existing.scalar_one_or_none():
                db.add(Tag(name=name, category=category))
                added += 1
    if added:
        await db.flush()
    return added


async def get_all_tags_grouped(db: AsyncSession) -> dict[str, list[dict]]:
    """获取所有标签，按类别分组，并包含使用次数统计。

    组内排序优先级：置顶 → 自定义 sort_order → 使用次数（降序）。
    """
    result = await db.execute(
        select(
            Tag,
            func.count(InspirationTag.inspiration_id).label("usage_count"),
        )
        .outerjoin(InspirationTag, Tag.id == InspirationTag.tag_id)
        .group_by(Tag.id)
        .order_by(
            Tag.category,
            Tag.pinned.desc(),
            Tag.sort_order.asc(),
            func.count(InspirationTag.inspiration_id).desc(),
        )
    )
    grouped: dict[str, list[dict]] = {}
    for row in result:
        tag, count = row[0], row[1]
        tag_dict = {
            "id": tag.id,
            "name": tag.name,
            "category": tag.category,
            "source": tag.source,
            "pinned": tag.pinned,
            "sort_order": tag.sort_order,
            "description": tag.description,
            "created_at": tag.created_at,
            "usage_count": count,
        }
        grouped.setdefault(tag.category, []).append(tag_dict)
    return grouped


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
                # 极端情况：重查仍未找到，再试一次（小概率）
                tag = Tag(name=name, category=category, source=source)
                db.add(tag)
                await db.flush()
    return tag


async def find_similar_tags(
    db: AsyncSession, name: str, threshold: float = 0.75
) -> list[Tag]:
    """查找与给定名称相似的已有标签（用于去重建议）。"""
    result = await db.execute(select(Tag))
    all_tags = result.scalars().all()
    similar = []
    for tag in all_tags:
        sim = _similarity(name, tag.name)
        if sim >= threshold and sim < 1.0:
            similar.append(tag)
    return sorted(similar, key=lambda t: _similarity(name, t.name), reverse=True)


async def merge_tags(db: AsyncSession, source_id: int, target_id: int):
    """将源标签合并到目标标签：重新关联所有素材，删除源标签。"""
    # 查找源标签的所有关联
    result = await db.execute(
        select(InspirationTag).where(InspirationTag.tag_id == source_id)
    )
    links = result.scalars().all()
    # 收集受影响素材：合并会改变这些素材的标签集合，需重建其文本向量
    affected_ids = [link.inspiration_id for link in links]

    for link in links:
        # 检查该素材是否已关联目标标签
        existing = await db.execute(
            select(InspirationTag).where(
                InspirationTag.inspiration_id == link.inspiration_id,
                InspirationTag.tag_id == target_id,
            )
        )
        if existing.scalar_one_or_none():
            # 重复关联 — 删除源标签的关联
            await db.delete(link)
        else:
            # 重定向到目标标签
            link.tag_id = target_id

    # 删除源标签
    source_tag = await db.get(Tag, source_id)
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
                target_alias_set.add(alias.alias)

        # 先刷新，确保别名搬迁（tag_id 重指向）先落库，
        # 否则 delete-orphan 级联在删除源标签时会重新加载仍指向源标签的别名并物理删除。
        await db.flush()

        await db.delete(source_tag)

    await db.flush()

    # 合并后为受影响素材重建文本向量（异步入队，由 worker 执行）
    await _rebuild_vectors_for_tag_change(db, affected_ids)


async def create_tag(db: AsyncSession, name: str, category: str = "free") -> Tag:
    """创建自定义标签（先做别名归一化，再按规范名查重）。

    名称已存在时抛 TagConflictError。

    参数:
        name: 原始输入标签名（归一化前的名称，用于冲突提示文案）
        category: 标签类别
    """
    raw_name = name
    name = (await normalize_tag_name_async(db, name)).strip()
    result = await db.execute(select(Tag).where(Tag.name == name))
    if result.scalar_one_or_none():
        raise TagConflictError(f"标签 '{raw_name}' 已存在")

    tag = Tag(name=name, category=category, source="manual")
    db.add(tag)
    await db.flush()
    await db.refresh(tag)
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
    return tag


async def delete_unused_tags(db: AsyncSession) -> list[Tag]:
    """删除所有使用次数为 0 的标签，返回被删除的标签列表。"""
    used_subquery = select(InspirationTag.tag_id).distinct()
    result = await db.execute(select(Tag).where(Tag.id.notin_(used_subquery)))
    unused = result.scalars().all()

    if not unused:
        return []

    # 先删关联表中的残留记录（防御性清理），再删标签
    unused_ids = [t.id for t in unused]
    await db.execute(
        delete(InspirationTag).where(InspirationTag.tag_id.in_(unused_ids))
    )
    await db.execute(delete(Tag).where(Tag.id.in_(unused_ids)))
    await db.commit()
    return unused


async def batch_delete_tags(db: AsyncSession, tag_ids: list[int]) -> list[Tag]:
    """批量删除标签及其所有关联，返回被删除的标签列表。"""
    result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids)))
    tags = result.scalars().all()
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
    result = await db.execute(
        update(Tag).where(Tag.id.in_(tag_ids)).values(category=category)
    )
    await db.commit()
    return result.rowcount


async def batch_rename_tags(
    db: AsyncSession, tag_ids: list[int], find_str: str, replace_str: str
) -> int:
    """批量重命名标签（查找替换），返回实际更新数。

    预检新名称冲突：与已有标签同名时抛 TagConflictError（不执行任何修改）。
    """
    result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids)))
    tags = result.scalars().all()
    # 预检：新名称是否会与已有标签冲突
    for tag in tags:
        if find_str in tag.name:
            new_name = tag.name.replace(find_str, replace_str)
            if new_name != tag.name:
                conflict = await db.execute(
                    select(Tag.id).where(Tag.name == new_name, Tag.id != tag.id)
                )
                if conflict.scalar_one_or_none():
                    raise TagConflictError(
                        f"重命名冲突: '{tag.name}' → '{new_name}' 与已有标签同名"
                    )
    updated = 0
    renamed_tag_ids: list[int] = []
    for tag in tags:
        if find_str in tag.name:
            tag.name = tag.name.replace(find_str, replace_str)
            renamed_tag_ids.append(tag.id)
            updated += 1
    await db.commit()

    # 重命名后标签名变了，文本向量（基于标签名拼接）需重建，异步入队
    if renamed_tag_ids:
        affected_ids = (
            await db.execute(
                select(InspirationTag.inspiration_id)
                .where(InspirationTag.tag_id.in_(renamed_tag_ids))
                .distinct()
            )
        ).scalars().all()
        await _rebuild_vectors_for_tag_change(db, affected_ids)
    return updated


async def get_tag_stats(db: AsyncSession) -> dict:
    """统计标签数据：总数、按来源、按类别、未使用数、总关联数。"""
    # 总数
    total_result = await db.execute(select(func.count()).select_from(Tag))
    total = total_result.scalar() or 0

    # 按来源统计
    source_result = await db.execute(
        select(Tag.source, func.count()).group_by(Tag.source)
    )
    by_source = {row[0]: row[1] for row in source_result}

    # 按类别统计
    cat_result = await db.execute(
        select(Tag.category, func.count()).group_by(Tag.category).order_by(func.count().desc())
    )
    by_category = {row[0]: row[1] for row in cat_result}

    # 未使用标签数
    used_subquery = select(InspirationTag.tag_id).distinct()
    unused_result = await db.execute(
        select(func.count()).select_from(Tag).where(Tag.id.notin_(used_subquery))
    )
    unused = unused_result.scalar() or 0

    # 总关联数
    link_result = await db.execute(select(func.count()).select_from(InspirationTag))
    total_links = link_result.scalar() or 0

    return {
        "total": total,
        "unused": unused,
        "total_links": total_links,
        "by_source": by_source,
        "by_category": by_category,
    }


async def find_duplicate_tag_pairs(
    db: AsyncSession, threshold: float = 0.75
) -> tuple[list[dict], int]:
    """扫描所有标签，返回名称相似度达到阈值的标签对列表及总数。

    O(n²) 相似度计算放入线程池执行，避免阻塞事件循环。
    """
    result = await db.execute(select(Tag).order_by(Tag.name))
    all_tags = result.scalars().all()

    def _compute_pairs():
        pairs = []
        for i in range(len(all_tags)):
            for j in range(i + 1, len(all_tags)):
                sim = _similarity(all_tags[i].name, all_tags[j].name)
                if sim >= threshold and sim < 1.0:
                    pairs.append({
                        "tag_a": {
                            "id": all_tags[i].id,
                            "name": all_tags[i].name,
                            "category": all_tags[i].category,
                        },
                        "tag_b": {
                            "id": all_tags[j].id,
                            "name": all_tags[j].name,
                            "category": all_tags[j].category,
                        },
                        "similarity": round(sim, 2),
                    })
        pairs.sort(key=lambda p: p["similarity"], reverse=True)
        return pairs

    pairs = await asyncio.to_thread(_compute_pairs)
    return pairs, len(pairs)


async def batch_remove_tag_inspirations(
    db: AsyncSession, tag_id: int, inspiration_ids: list[str]
) -> int:
    """批量解除标签与多个素材的关联，返回解除数量。"""
    result = await db.execute(
        delete(InspirationTag).where(
            InspirationTag.tag_id == tag_id,
            InspirationTag.inspiration_id.in_(inspiration_ids),
        )
    )
    await db.commit()
    # 解除关联后素材标签集合变了，重建其文本向量（异步入队）
    await _rebuild_vectors_for_tag_change(db, list(inspiration_ids))
    return result.rowcount


async def list_tag_inspirations(
    db: AsyncSession, tag_id: int, page: int, size: int, sort: str
) -> dict | None:
    """获取使用指定标签的素材列表（含分页与统计）。标签不存在返回 None。"""
    tag = await db.get(Tag, tag_id)
    if not tag:
        return None

    # 统计总数
    count_result = await db.execute(
        select(func.count()).where(InspirationTag.tag_id == tag_id)
    )
    total = count_result.scalar() or 0

    # 分页获取素材 — 只查需要的列，避免 Inspiration 的 selectin 预加载
    stmt = (
        select(
            Inspiration.id,
            Inspiration.file_path,
            Inspiration.thumbnail_path,
            Inspiration.media_type,
            Inspiration.created_at,
            InspirationTag.confidence,
        )
        .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
        .where(InspirationTag.tag_id == tag_id)
        .order_by(
            InspirationTag.confidence.desc() if sort == "confidence"
            else Inspiration.created_at.asc() if sort == "oldest"
            else Inspiration.created_at.desc()
        )
        .offset((page - 1) * size)
        .limit(size)
    )
    link_result = await db.execute(stmt)
    rows = link_result.all()

    items = [
        {
            "inspiration_id": row[0],
            "file_path": row[1],
            "thumbnail_path": row[2],
            "media_type": row[3],
            "confidence": round(row[5], 2) if row[5] else 0,
            "created_at": str(row[4]) if row[4] else None,
        }
        for row in rows
    ]

    return {
        "tag": {"id": tag.id, "name": tag.name, "category": tag.category},
        "items": items,
        "total": total,
        "page": page,
        "size": size,
    }


async def export_tags(db: AsyncSession) -> list[dict]:
    """导出所有标签为列表（含类别、来源、使用次数）。"""
    grouped = await get_all_tags_grouped(db)
    export_data = []
    for category, tags in grouped.items():
        for t in tags:
            export_data.append({
                "name": t["name"],
                "category": t["category"],
                "source": t.get("source", "seed"),
                "usage_count": t["usage_count"],
            })
    return export_data


async def import_tags(
    db: AsyncSession, items: list[tuple[str, str]]
) -> tuple[int, int]:
    """批量导入标签（跳过已存在的标签），返回 (导入数, 跳过数)。

    参数:
        items: (标签名, 类别) 列表
    """
    imported = 0
    skipped = 0
    for name, category in items:
        existing = await db.execute(select(Tag).where(Tag.name == name.strip()))
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        tag = Tag(name=name.strip(), category=category, source="manual")
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


async def list_aliases(db: AsyncSession) -> list[dict]:
    """获取所有标签别名（含所属标签名）。"""
    result = await db.execute(
        select(TagAlias.id, TagAlias.tag_id, TagAlias.alias, Tag.name)
        .join(Tag, Tag.id == TagAlias.tag_id)
        .order_by(Tag.name, TagAlias.alias)
    )
    return [
        {"id": r[0], "tag_id": r[1], "alias": r[2], "tag_name": r[3]}
        for r in result.all()
    ]


async def create_alias(db: AsyncSession, tag_id: int, alias: str) -> TagAlias:
    """为标签添加别名。

    标签不存在抛 TagNotFoundError；与主标签或已有别名冲突抛 TagConflictError。
    并发创建同名别名时，回滚后重查并返回已存在的别名（路由层原样返回）。
    """
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise TagNotFoundError("标签未找到")

    # 别名不能与任何主标签同名（否则产生歧义）
    existing_tag = await db.execute(select(Tag.id).where(Tag.name == alias))
    if existing_tag.scalar_one_or_none():
        raise TagConflictError(f"别名 '{alias}' 与已有标签同名")

    existing_alias = await db.execute(select(TagAlias).where(TagAlias.alias == alias))
    if existing_alias.scalar_one_or_none():
        raise TagConflictError(f"别名 '{alias}' 已存在")

    obj = TagAlias(tag_id=tag_id, alias=alias)
    db.add(obj)
    try:
        # 用 SAVEPOINT 隔离插入：并发创建同名字别名时，后者触发 IntegrityError，
        # 回滚后重查并返回已存在的别名，避免 500。
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        db.expunge(obj)
        existing = await db.execute(select(TagAlias).where(TagAlias.alias == alias))
        existing_obj = existing.scalar_one_or_none()
        if existing_obj:
            return existing_obj
        raise TagConflictError(f"别名 '{alias}' 已存在")
    await db.refresh(obj)
    return obj


async def delete_alias(db: AsyncSession, alias_id: int) -> bool:
    """删除标签别名，返回是否删除成功。"""
    obj = await db.get(TagAlias, alias_id)
    if not obj:
        return False
    await db.delete(obj)
    await db.commit()
    return True


async def get_cooccurrence_network(
    db: AsyncSession, limit: int, min_count: int
) -> dict:
    """返回使用次数 top-N 标签之间的共现网络（节点 + 加权边）。"""
    # 取使用次数最多的 top-N 标签作为网络节点
    top_result = await db.execute(
        select(
            Tag.id,
            Tag.name,
            Tag.category,
            func.count(InspirationTag.inspiration_id).label("cnt"),
        )
        .join(InspirationTag, Tag.id == InspirationTag.tag_id)
        .group_by(Tag.id)
        .order_by(func.count(InspirationTag.inspiration_id).desc())
        .limit(limit)
    )
    tags = [(r[0], r[1], r[2], r[3]) for r in top_result.all()]
    tag_ids = [t[0] for t in tags]

    if not tag_ids:
        return {"nodes": [], "edges": []}

    # 一次性查出这些标签的所有素材关联，在内存中统计共现
    links_result = await db.execute(
        select(InspirationTag.inspiration_id, InspirationTag.tag_id).where(
            InspirationTag.tag_id.in_(tag_ids)
        )
    )
    insp_map: dict[str, set[int]] = defaultdict(set)
    for insp_id, tag_id in links_result.all():
        insp_map[insp_id].add(tag_id)

    pair_count: dict[tuple[int, int], int] = defaultdict(int)
    for tag_set in insp_map.values():
        for a, b in itertools.combinations(sorted(tag_set), 2):
            pair_count[(a, b)] += 1

    nodes = [
        {"id": t[0], "name": t[1], "category": t[2], "usage_count": t[3]}
        for t in tags
    ]
    edges = [
        {"source": a, "target": b, "weight": w}
        for (a, b), w in pair_count.items()
        if w >= min_count
    ]
    return {"nodes": nodes, "edges": edges}


async def get_top_tags(db: AsyncSession, limit: int) -> list[dict]:
    """返回使用次数最多的标签排行。"""
    result = await db.execute(
        select(
            Tag.id,
            Tag.name,
            Tag.category,
            func.count(InspirationTag.inspiration_id).label("cnt"),
        )
        .join(InspirationTag, Tag.id == InspirationTag.tag_id)
        .group_by(Tag.id)
        .order_by(func.count(InspirationTag.inspiration_id).desc())
        .limit(limit)
    )
    return [
        {"id": r[0], "name": r[1], "category": r[2], "usage_count": r[3]}
        for r in result.all()
    ]


async def get_tag_trend(
    db: AsyncSession, tag_id: int, granularity: str
) -> dict | None:
    """获取标签的使用趋势（按素材创建时间分桶统计）。标签不存在返回 None。"""
    tag = await db.get(Tag, tag_id)
    if not tag:
        return None

    fmt = {"month": "%Y-%m", "week": "%Y-W%W", "day": "%Y-%m-%d"}[granularity]
    result = await db.execute(
        select(
            func.strftime(fmt, Inspiration.created_at).label("bucket"),
            func.count().label("cnt"),
        )
        .join(InspirationTag, InspirationTag.inspiration_id == Inspiration.id)
        .where(InspirationTag.tag_id == tag_id)
        .group_by("bucket")
        .order_by("bucket")
    )
    return {
        "tag": {"id": tag.id, "name": tag.name},
        "granularity": granularity,
        "trend": [{"bucket": r[0], "count": r[1]} for r in result.all()],
    }
