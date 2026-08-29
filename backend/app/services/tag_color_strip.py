"""单品标签颜色剥离治理：item_type 标签名中的颜色前缀剥离 + 撞名合并。

背景：AI 历史打标曾把颜色写进单品名，形成「颜色×品类×细节」的笛卡尔积
（如「黑色尖头细跟高跟鞋」1937 次关联 vs「白色尖头细跟高跟鞋」241 次），
标签体系碎片化。本服务提供两段式治理：

- ``dry_run_color_strip``：只统计与预览（不写库），返回将被重命名/合并的
  标签数、预计重指向的关联行数、预计新增的颜色关联行数与「旧名→新名」样例；
- ``apply_color_strip``：单事务执行——剥离颜色前缀重命名；剥离后为空名跳过；
  新名与已有标签撞名时合并（关联行重指向、同素材双关联去重）；重命名/合并
  涉及的素材补建对应颜色标签的关联（去重，source=ai_generated）。

颜色前缀词表来自 color 类别标签名（长词优先匹配，如「米白色」先于「白色」），
与 AI 打标保存器（ai_tag_saver）把 items[].color 归入 color 类别的口径一致。
"""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import InspirationTag, Tag, TagAlias
from app.services.tag_history_service import (
    new_batch_id,
    record_history,
    snapshot_tags,
)

logger = logging.getLogger(__name__)

# 补建颜色关联使用的来源与置信度（与 ai_tag_saver 的颜色落库口径一致）
_COLOR_LINK_SOURCE = "ai_generated"
_COLOR_LINK_CONFIDENCE = 0.85


async def build_color_prefixes(db: AsyncSession) -> list[str]:
    """从 color 类别标签名构建颜色前缀词表。

    按长度降序排列，保证「米白色」优先于「白色」被匹配（最长前缀优先）。
    """
    result = await db.execute(select(Tag.name).where(Tag.category == "color"))
    names = {name for (name,) in result.all() if name}
    return sorted(names, key=len, reverse=True)


def _strip_color_prefix(name: str, prefixes: list[str]) -> tuple[str, str | None]:
    """剥离标签名开头的颜色前缀，返回 (新名, 命中的颜色词)。

    新名为空（标签名本身就是颜色词）或无前缀命中时返回 (原名, None)。
    """
    for prefix in prefixes:
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix):], prefix
    return name, None


async def _build_plans(
    db: AsyncSession, tags: list[Tag], prefixes: list[str], category: str
) -> tuple[list[dict], int, int]:
    """计算全部标签的颜色剥离计划（含批内撞名解析）。

    计划项: {"tag", "old", "new", "color", "action": "rename"|"merge"|"skip",
             "target_id"}。
    撞名解析与 batch_edit 语义一致：先到先得（先改者占用新名），
    后到且目标已存在者转为合并；目标标签与参与标签不同类别时跳过
    （避免把单品标签合并进 body_part 等跨类别标签）；新名与既有别名同名时
    也跳过（别名会干扰别名归一化链路）。
    返回 (计划列表, 别名冲突跳过数, 跨类别冲突跳过数)。
    """
    all_tags = list((await db.execute(select(Tag))).scalars().all())
    tag_by_id = {t.id: t for t in all_tags}
    taken = {t.name: t.id for t in all_tags}  # 当前名 → 归属标签 id（随执行推进更新）
    alias_names = set(
        (await db.execute(select(TagAlias.alias))).scalars().all()
    )

    plans: list[dict] = []
    alias_skipped = 0
    cross_category_skipped = 0
    for tag in tags:
        new_name, color = _strip_color_prefix(tag.name, prefixes)
        if color is None or not new_name.strip():
            plans.append({"tag": tag, "old": tag.name, "new": None, "color": None,
                          "action": "skip", "target_id": None})
            continue
        owner_id = taken.get(new_name)
        if owner_id is not None and owner_id != tag.id:
            owner_tag = tag_by_id[owner_id]
            if owner_tag.category != category:
                # 新名被其它类别的标签占用：跳过，避免跨类别合并
                cross_category_skipped += 1
                plans.append({"tag": tag, "old": tag.name, "new": None,
                              "color": None, "action": "skip",
                              "target_id": None})
                continue
            # 新名已被占用 → 合并到该目标
            plans.append({"tag": tag, "old": tag.name, "new": new_name,
                          "color": color, "action": "merge",
                          "target_id": owner_id})
            taken.pop(tag.name, None)
            continue
        if new_name in alias_names:
            # 新名与既有标签别名同名：跳过，避免别名归一化歧义
            alias_skipped += 1
            plans.append({"tag": tag, "old": tag.name, "new": None, "color": None,
                          "action": "skip", "target_id": None})
            continue
        plans.append({"tag": tag, "old": tag.name, "new": new_name, "color": color,
                      "action": "rename", "target_id": None})
        taken.pop(tag.name, None)
        taken[new_name] = tag.id
    return plans, alias_skipped, cross_category_skipped


async def _fetch_link_map(
    db: AsyncSession, tag_ids: list[int]
) -> dict[int, list[str]]:
    """批量取标签的素材关联，返回 {tag_id: [inspiration_id]}。"""
    link_map: dict[int, list[str]] = {tid: [] for tid in tag_ids}
    if not tag_ids:
        return link_map
    result = await db.execute(
        select(InspirationTag.tag_id, InspirationTag.inspiration_id).where(
            InspirationTag.tag_id.in_(tag_ids)
        )
    )
    for tid, iid in result.all():
        link_map.setdefault(tid, []).append(iid)
    return link_map


async def _count_existing_color_links(
    db: AsyncSession, color_tag_ids: list[int], inspiration_ids: list[str]
) -> set[tuple[str, int]]:
    """查询素材集合上已存在的颜色关联，返回 {(inspiration_id, color_tag_id)}。"""
    if not color_tag_ids or not inspiration_ids:
        return set()
    result = await db.execute(
        select(InspirationTag.inspiration_id, InspirationTag.tag_id).where(
            InspirationTag.tag_id.in_(color_tag_ids),
            InspirationTag.inspiration_id.in_(inspiration_ids),
        )
    )
    return {(iid, tid) for iid, tid in result.all()}


def _summarize(plans: list[dict], alias_skipped: int,
               cross_category_skipped: int) -> dict:
    """计划 → 统计摘要（dry-run 与 apply 共用）。"""
    renamed = sum(1 for p in plans if p["action"] == "rename")
    merged = sum(1 for p in plans if p["action"] == "merge")
    skipped = sum(1 for p in plans if p["action"] == "skip")
    return {
        "renamed": renamed,
        "merged": merged,
        "skipped": skipped,
        "alias_skipped": alias_skipped,
        "cross_category_skipped": cross_category_skipped,
    }


async def dry_run_color_strip(
    db: AsyncSession, category: str = "item_type", limit: int = 0
) -> dict:
    """颜色剥离 dry-run：返回统计与样例，不写库。

    参数:
        category: 参与治理的标签类别（默认 item_type）
        limit: 只处理前 N 个标签（0 表示全部）

    返回:
        {"category", "total_scanned", "renamed", "merged", "skipped",
         "alias_skipped", "links_repointed", "color_links_added",
         "samples": [{"old", "new", "action"}]}
    """
    prefixes = await build_color_prefixes(db)
    query = select(Tag).where(Tag.category == category).order_by(Tag.id)
    if limit > 0:
        query = query.limit(limit)
    tags = list((await db.execute(query)).scalars().all())
    plans, alias_skipped, cross_skipped = await _build_plans(
        db, tags, prefixes, category
    )

    merge_plans = [p for p in plans if p["action"] == "merge"]
    involved_ids = [p["tag"].id for p in plans if p["action"] != "skip"]
    involved_ids += [p["target_id"] for p in merge_plans]
    link_map = await _fetch_link_map(db, list(set(involved_ids)))

    # 预计重指向的关联行数：仅合并产生重指向（改名保留原标签行，关联不变），
    # 重指向数 = 源标签关联数 - 与目标标签重复的关联数（同素材双关联去重）
    links_repointed = 0
    for p in merge_plans:
        src_links = link_map.get(p["tag"].id, [])
        tgt_links = set(link_map.get(p["target_id"], []))
        links_repointed += sum(1 for iid in src_links if iid not in tgt_links)

    # 预计新增的颜色关联行数：受影响素材 ∖ 已关联对应颜色标签的素材
    color_ids: dict[str, int] = {}
    result = await db.execute(
        select(Tag.id, Tag.name).where(
            Tag.category == "color", Tag.name.in_([p["color"] for p in plans if p["color"]])
        )
    )
    color_ids = {name: tid for tid, name in result.all()}
    affected: list[tuple[str, int]] = []  # (inspiration_id, color_tag_id)
    for p in plans:
        if p["action"] == "skip" or p["color"] not in color_ids:
            continue
        for iid in link_map.get(p["tag"].id, []):
            affected.append((iid, color_ids[p["color"]]))
    existing = await _count_existing_color_links(
        db, list(color_ids.values()), [iid for iid, _ in affected]
    )
    color_links_added = len({(iid, tid) for iid, tid in affected} - existing)

    samples = [
        {"old": p["old"], "new": p["new"], "action": p["action"]}
        for p in plans
        if p["action"] != "skip"
    ][:20]

    return {
        "category": category,
        "total_scanned": len(tags),
        "color_prefixes": len(prefixes),
        **_summarize(plans, alias_skipped, cross_skipped),
        "links_repointed": links_repointed,
        "color_links_added": color_links_added,
        "samples": samples,
    }


async def _merge_into(
    db: AsyncSession, source: Tag, target_id: int
) -> int:
    """把源标签合并进目标标签（单事务版，不提交）：重指向关联、搬迁别名、删源标签。

    返回实际重指向的关联行数（同素材双关联直接删除，不计入）。
    """
    result = await db.execute(
        select(InspirationTag).where(InspirationTag.tag_id == source.id)
    )
    links = result.scalars().all()

    already = set(
        (await db.execute(
            select(InspirationTag.inspiration_id).where(
                InspirationTag.inspiration_id.in_(
                    [link.inspiration_id for link in links] or ["-"]
                ),
                InspirationTag.tag_id == target_id,
            )
        )).scalars().all()
    )

    repointed = 0
    for link in links:
        if link.inspiration_id in already:
            # 同素材已关联目标标签 → 双关联去重，删除源关联
            await db.delete(link)
            continue
        link.tag_id = target_id
        repointed += 1
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # SAVEPOINT 已回滚（并发合并同一素材到同一目标），按已关联处理
            db.expunge(link)
            repointed -= 1

    # 搬迁别名（Tag.aliases 为 delete-orphan 级联，不搬迁会随源标签物理删除），
    # 语义与 tag_crud.merge_tags 一致
    target_alias_set = set(
        (await db.execute(
            select(TagAlias.alias).where(TagAlias.tag_id == target_id)
        )).scalars().all()
    )
    source_aliases = (
        await db.execute(select(TagAlias).where(TagAlias.tag_id == source.id))
    ).scalars().all()
    for alias in source_aliases:
        if alias.alias in target_alias_set:
            await db.delete(alias)
        else:
            alias.tag_id = target_id
            target_alias_set.add(alias.alias)
    await db.flush()

    await db.delete(source)
    await db.flush()
    return repointed


async def _ensure_color_links(
    db: AsyncSession, plans: list[dict], link_map: dict[int, list[str]]
) -> int:
    """为重命名/合并源的关联素材补建颜色标签关联（去重），返回新增关联行数。

    颜色标签不存在时创建（复用 get_or_create_tag 的别名归一化与竞态处理），
    新关联 source=ai_generated、置信度与 ai_tag_saver 的颜色落库口径一致。
    """
    from app.services.tag_crud import get_or_create_tag

    # 按颜色词收集需要补关联的素材集合
    pairs: dict[str, set[str]] = {}
    for p in plans:
        if p["action"] == "skip":
            continue
        pairs.setdefault(p["color"], set()).update(link_map.get(p["tag"].id, []))
    if not pairs:
        return 0

    color_tag_ids: dict[str, int] = {}
    for name in pairs:
        tag = await get_or_create_tag(db, name, "color", _COLOR_LINK_SOURCE)
        color_tag_ids[name] = tag.id

    # 一次查出已存在的颜色关联（避免逐条判重）
    all_ids = [iid for ids in pairs.values() for iid in ids]
    existing = set()
    if all_ids:
        existing = set(
            (await db.execute(
                select(InspirationTag.inspiration_id, InspirationTag.tag_id).where(
                    InspirationTag.tag_id.in_(list(color_tag_ids.values())),
                    InspirationTag.inspiration_id.in_(all_ids),
                )
            )).all()
        )

    added = 0
    for name, ids in pairs.items():
        color_tag_id = color_tag_ids[name]
        for iid in ids:
            if (iid, color_tag_id) in existing:
                continue
            link = InspirationTag(
                inspiration_id=iid,
                tag_id=color_tag_id,
                confidence=_COLOR_LINK_CONFIDENCE,
                source=_COLOR_LINK_SOURCE,
            )
            db.add(link)
            try:
                async with db.begin_nested():
                    await db.flush()
                added += 1
            except IntegrityError:
                # SAVEPOINT 已回滚（并发写入同关联），跳过该条
                db.expunge(link)
    await db.flush()
    return added


async def apply_color_strip(
    db: AsyncSession, category: str = "item_type", limit: int = 0
) -> dict:
    """颜色剥离执行：重命名 / 合并 / 补建颜色关联，全程单事务。

    语义：
    - 剥离后名称为空的标签跳过（不产生空名标签）；
    - 新名与已有标签撞名 → 合并（关联行重指向 + 同素材双关联去重 + 别名搬迁）；
    - 重命名/合并涉及的素材补建对应颜色标签的关联（去重，source=ai_generated）；
    - 写操作历史（rename/merge 两类，共享同一批次）与审计日志。

    返回:
        {"category", "total_scanned", "renamed", "merged", "skipped",
         "alias_skipped", "links_repointed", "color_links_added", "batch_id"}
    """
    prefixes = await build_color_prefixes(db)
    query = select(Tag).where(Tag.category == category).order_by(Tag.id)
    if limit > 0:
        query = query.limit(limit)
    tags = list((await db.execute(query)).scalars().all())
    plans, alias_skipped, cross_skipped = await _build_plans(
        db, tags, prefixes, category
    )

    batch_id = new_batch_id("color-strip")
    rename_plans = [p for p in plans if p["action"] == "rename"]
    merge_plans = [p for p in plans if p["action"] == "merge"]
    if not rename_plans and not merge_plans:
        return {
            "category": category,
            "total_scanned": len(tags),
            **_summarize(plans, alias_skipped, cross_skipped),
            "links_repointed": 0,
            "color_links_added": 0,
            "batch_id": None,
        }

    # 操作前快照（供操作历史与回滚）
    snap_ids = [p["tag"].id for p in rename_plans + merge_plans]
    snap_ids += [p["target_id"] for p in merge_plans]
    before_snap = await snapshot_tags(db, list(set(snap_ids)))

    # 合并前先缓存改名/合并源标签的关联素材（合并源随后被删除，无法事后反查），
    # 用于补建颜色关联与登记文本向量重建
    involved_source_ids = [p["tag"].id for p in rename_plans + merge_plans]
    link_map = await _fetch_link_map(db, involved_source_ids)
    affected_ids = [
        iid for tid in involved_source_ids for iid in link_map.get(tid, [])
    ]

    # 1) 先合并：合并会删除源标签并释放其名称，
    #    避免改名先落库时与尚未删除的合并源旧名触发唯一约束冲突
    links_repointed = 0
    for p in merge_plans:
        links_repointed += await _merge_into(db, p["tag"], p["target_id"])

    # 2) 再改名（合并源已删除，目标名已腾空）
    for p in rename_plans:
        p["tag"].name = p["new"]
    await db.flush()

    # 3) 补建颜色关联（去重，source=ai_generated）
    color_links_added = await _ensure_color_links(db, plans, link_map)

    # 4) 受影响素材的标签集合/名称变了，登记文本向量重建（攒批，随事务落库）
    if affected_ids:
        from app.services.tag_crud import _rebuild_vectors_for_tag_change

        await _rebuild_vectors_for_tag_change(db, list(set(affected_ids)))

    await db.commit()

    # 5) 操作历史（改名 + 合并两类，共享批次，随调用方事务落库）
    if rename_plans:
        rename_before = {p["tag"].id: before_snap[p["tag"].id] for p in rename_plans}
        rename_after = {}
        for p in rename_plans:
            rename_after[p["tag"].id] = {
                **before_snap[p["tag"].id],
                "name": p["new"],
            }
        await record_history(
            db,
            operation="rename",
            before=rename_before,
            after=rename_after,
            batch_id=batch_id,
            meta={"origin": "color_strip", "find": "颜色前缀", "replace": ""},
        )
        await db.commit()
    if merge_plans:
        merge_after: dict[int, dict] = {
            p["target_id"]: before_snap.get(p["target_id"], {}) for p in merge_plans
        }
        for p in merge_plans:
            merge_after[p["tag"].id] = {
                "deleted": True,
                "name": before_snap.get(p["tag"].id, {}).get("name", ""),
            }
        await record_history(
            db,
            operation="merge",
            before=before_snap,
            after=merge_after,
            batch_id=batch_id,
            meta={
                "origin": "color_strip",
                "merged": [
                    {"source_tag_id": p["tag"].id, "target_tag_id": p["target_id"]}
                    for p in merge_plans
                ],
            },
        )
        await db.commit()

    # 6) 审计留痕 + 清除重复扫描缓存（标签数据已变更）
    from app.services.audit_service import record_audit_log
    from app.services.tag_dedupe_cache import clear_all

    await record_audit_log(
        action="color_strip",
        target_type="tags",
        count=len(rename_plans) + len(merge_plans),
        detail=f"颜色剥离（{category}）：重命名 {len(rename_plans)}，合并 {len(merge_plans)}，"
        f"重指向 {links_repointed}，补建颜色关联 {color_links_added}",
    )
    clear_all()

    return {
        "category": category,
        "total_scanned": len(tags),
        **_summarize(plans, alias_skipped, cross_skipped),
        "links_repointed": links_repointed,
        "color_links_added": color_links_added,
        "batch_id": batch_id,
    }
