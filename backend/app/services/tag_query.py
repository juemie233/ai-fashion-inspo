"""标签查询与统计分析：分组列表、统计、重复对、排行、共现网络与趋势。

依赖 tag_crud 的 _similarity / _alive_tag_links_subquery 等底层工具，
不反向依赖任何其它标签模块。
"""

import asyncio
import itertools
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.tag import Tag, InspirationTag
from app.services.tag_crud import _alive_tag_links_subquery, _similarity


async def get_all_tags_grouped(db: AsyncSession) -> dict[str, list[dict]]:
    """获取所有标签，按类别分组，并包含使用次数统计。

    组内排序优先级：置顶 → 自定义 sort_order → 使用次数（降序）。
    """
    result = await db.execute(
        select(
            Tag,
            func.count(Inspiration.id).label("usage_count"),
        )
        .outerjoin(InspirationTag, Tag.id == InspirationTag.tag_id)
        # 使用次数仅统计未删除素材：垃圾桶素材的关联不计入
        .outerjoin(
            Inspiration,
            and_(
                InspirationTag.inspiration_id == Inspiration.id,
                Inspiration.deleted_at.is_(None),
            ),
        )
        .group_by(Tag.id)
        .order_by(
            Tag.category,
            Tag.pinned.desc(),
            Tag.sort_order.asc(),
            func.count(Inspiration.id).desc(),
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


async def get_tag_stats(db: AsyncSession) -> dict:
    """统计标签数据：总数、按来源、按类别、未使用数、总关联数。"""
    # 总数
    total_result = await db.execute(select(func.count()).select_from(Tag))
    total = total_result.scalar() or 0

    # 按来源统计
    source_result = await db.execute(select(Tag.source, func.count()).group_by(Tag.source))
    by_source = {row[0]: row[1] for row in source_result}

    # 按类别统计
    cat_result = await db.execute(
        select(Tag.category, func.count()).group_by(Tag.category).order_by(func.count().desc())
    )
    by_category = {row[0]: row[1] for row in cat_result}

    # 未使用标签数（口径与 usage_count 一致：无任何未删除素材关联，含只关联垃圾桶素材的标签）
    unused_result = await db.execute(
        select(func.count()).select_from(Tag).where(Tag.id.notin_(_alive_tag_links_subquery()))
    )
    unused = unused_result.scalar() or 0

    # 总关联数（仅统计未删除素材的关联，与使用次数口径一致）
    link_result = await db.execute(
        select(func.count())
        .select_from(InspirationTag)
        .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
        .where(Inspiration.deleted_at.is_(None))
    )
    total_links = link_result.scalar() or 0

    return {
        "total": total,
        "unused": unused,
        "total_links": total_links,
        "by_source": by_source,
        "by_category": by_category,
    }


async def find_duplicate_tag_pairs(db: AsyncSession, threshold: float = 0.75) -> tuple[list[dict], int]:
    """扫描所有标签，返回名称相似度达到阈值的标签对列表及总数。

    优化 1：按类别分组，跨类别不比对
    优化 2：同类别内首字不同直接跳过
    优化 3：相似度计算放入线程池避免阻塞事件循环
    """
    result = await db.execute(select(Tag).order_by(Tag.category, Tag.name))
    all_tags = result.scalars().all()

    # 按类别分组
    by_category: dict[str, list[Tag]] = {}
    for tag in all_tags:
        by_category.setdefault(tag.category, []).append(tag)

    def _compute_pairs() -> list[dict]:
        pairs = []
        for _category, cat_tags in by_category.items():
            if len(cat_tags) < 2:
                continue
            for i in range(len(cat_tags)):
                name_a = cat_tags[i].name
                if not name_a:
                    continue
                for j in range(i + 1, len(cat_tags)):
                    name_b = cat_tags[j].name
                    if not name_b:
                        continue
                    # 首字不同直接跳过（中文重复几乎首字一定相同）
                    if name_a[0] != name_b[0]:
                        continue
                    sim = _similarity(name_a, name_b)
                    if sim >= threshold and sim < 1.0:
                        pairs.append(
                            {
                                "tag_a": {
                                    "id": cat_tags[i].id,
                                    "name": cat_tags[i].name,
                                    "category": cat_tags[i].category,
                                },
                                "tag_b": {
                                    "id": cat_tags[j].id,
                                    "name": cat_tags[j].name,
                                    "category": cat_tags[j].category,
                                },
                                "similarity": round(sim, 2),
                            }
                        )
        pairs.sort(key=lambda p: p["similarity"], reverse=True)
        return pairs

    pairs = await asyncio.to_thread(_compute_pairs)
    return pairs, len(pairs)


async def export_tags(db: AsyncSession) -> list[dict]:
    """导出所有标签为列表（含类别、来源、使用次数）。"""
    grouped = await get_all_tags_grouped(db)
    export_data = []
    for _category, tags in grouped.items():
        for t in tags:
            export_data.append(
                {
                    "name": t["name"],
                    "category": t["category"],
                    "source": t.get("source", "seed"),
                    "usage_count": t["usage_count"],
                }
            )
    return export_data


async def get_cooccurrence_network(
    db: AsyncSession,
    limit: int,
    min_count: int,
    category: str | None = None,
) -> dict:
    """返回使用次数 top-N 标签之间的共现网络（节点 + 加权边）。

    参数:
        limit: 节点数上限（按使用次数取 top-N）
        min_count: 边的最小共现次数（过滤弱边）
        category: 可选，仅统计指定类别标签（网络图分析的类别子图）
    """
    # 取使用次数最多的 top-N 标签作为网络节点（仅统计未删除素材）
    top_query = (
        select(
            Tag.id,
            Tag.name,
            Tag.category,
            func.count(InspirationTag.inspiration_id).label("cnt"),
        )
        .join(InspirationTag, Tag.id == InspirationTag.tag_id)
        .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
        .where(Inspiration.deleted_at.is_(None))
    )
    if category:
        top_query = top_query.where(Tag.category == category)
    top_result = await db.execute(
        top_query.group_by(Tag.id).order_by(func.count(InspirationTag.inspiration_id).desc()).limit(limit)
    )
    tags = [(r[0], r[1], r[2], r[3]) for r in top_result.all()]
    tag_ids = [t[0] for t in tags]

    if not tag_ids:
        return {"nodes": [], "edges": []}

    # 一次性查出这些标签的所有素材关联，在内存中统计共现（同样排除垃圾桶素材）
    links_result = await db.execute(
        select(InspirationTag.inspiration_id, InspirationTag.tag_id)
        .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
        .where(
            InspirationTag.tag_id.in_(tag_ids),
            Inspiration.deleted_at.is_(None),
        )
    )
    insp_map: dict[str, set[int]] = defaultdict(set)
    for insp_id, tag_id in links_result.all():
        insp_map[insp_id].add(tag_id)

    pair_count: dict[tuple[int, int], int] = defaultdict(int)
    for tag_set in insp_map.values():
        for a, b in itertools.combinations(sorted(tag_set), 2):
            pair_count[(a, b)] += 1

    nodes = [{"id": t[0], "name": t[1], "category": t[2], "usage_count": t[3]} for t in tags]
    edges = [{"source": a, "target": b, "weight": w} for (a, b), w in pair_count.items() if w >= min_count]
    return {"nodes": nodes, "edges": edges}


async def get_tag_tree_children(
    db: AsyncSession, parent_id: int | None, page: int = 1, size: int = 200
) -> dict:
    """获取层级树某一层的节点（懒加载），parent_id=None 表示根节点。

    返回:
        {"items": [{id, name, category, parent_id, usage_count, has_children}],
         "total", "parent_id"}
    """
    query = (
        select(
            Tag,
            func.count(Inspiration.id).label("usage_count"),
        )
        .outerjoin(InspirationTag, Tag.id == InspirationTag.tag_id)
        .outerjoin(
            Inspiration,
            and_(
                InspirationTag.inspiration_id == Inspiration.id,
                Inspiration.deleted_at.is_(None),
            ),
        )
        .where(Tag.parent_id.is_(None) if parent_id is None else Tag.parent_id == parent_id)
        .group_by(Tag.id)
        .order_by(Tag.pinned.desc(), Tag.sort_order.asc(), Tag.id.asc())
    )
    total_result = await db.execute(
        select(func.count())
        .select_from(Tag)
        .where(Tag.parent_id.is_(None) if parent_id is None else Tag.parent_id == parent_id)
    )
    total = total_result.scalar() or 0
    result = await db.execute(query.offset((page - 1) * size).limit(size))
    rows = result.all()

    # 本层节点是否还有子节点（一次性查出所有非空 parent_id）
    parent_ids = set(
        (await db.execute(select(Tag.parent_id).where(Tag.parent_id.isnot(None)))).scalars().all()
    )
    return {
        "items": [
            {
                "id": row[0].id,
                "name": row[0].name,
                "category": row[0].category,
                "parent_id": row[0].parent_id,
                "usage_count": row[1],
                "has_children": row[0].id in parent_ids,
            }
            for row in rows
        ],
        "total": total,
        "parent_id": parent_id,
    }


async def get_top_tags(db: AsyncSession, limit: int) -> list[dict]:
    """返回使用次数最多的标签排行（仅统计未删除素材）。"""
    result = await db.execute(
        select(
            Tag.id,
            Tag.name,
            Tag.category,
            func.count(InspirationTag.inspiration_id).label("cnt"),
        )
        .join(InspirationTag, Tag.id == InspirationTag.tag_id)
        .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
        .where(Inspiration.deleted_at.is_(None))
        .group_by(Tag.id)
        .order_by(func.count(InspirationTag.inspiration_id).desc())
        .limit(limit)
    )
    return [{"id": r[0], "name": r[1], "category": r[2], "usage_count": r[3]} for r in result.all()]


async def get_tag_trend(db: AsyncSession, tag_id: int, granularity: str) -> dict | None:
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
        .where(
            InspirationTag.tag_id == tag_id,
            Inspiration.deleted_at.is_(None),
        )
        .group_by("bucket")
        .order_by("bucket")
    )
    return {
        "tag": {"id": tag.id, "name": tag.name},
        "granularity": granularity,
        "trend": [{"bucket": r[0], "count": r[1]} for r in result.all()],
    }


async def get_tag_cooccurrence(db: AsyncSession, tag_id: int, limit: int = 8) -> dict:
    """某标签「常一起出现的标签」（按共现素材数降序）。

    口径与 usage_count 一致：只统计**未删除**素材的关联，垃圾桶素材不计入，否则
    「被删素材里一起出现过」会把不相干的标签顶上来。

    Args:
        db: 数据库会话。
        tag_id: 目标标签 id。
        limit: 返回多少个关联标签。

    Returns:
        ``{"tag": {"id","name","category"}, "materials": 该标签的素材数,
        "items": [{"id","name","category","count"}]}``；标签不存在时 ``items`` 为空。
    """
    tag = await db.get(Tag, tag_id)
    if tag is None:
        return {"tag": None, "materials": 0, "items": []}

    # 该标签命中的素材（未删除）
    alive_ids = (
        select(InspirationTag.inspiration_id)
        .join(Inspiration, Inspiration.id == InspirationTag.inspiration_id)
        .where(InspirationTag.tag_id == tag_id, Inspiration.deleted_at.is_(None))
    )
    rows = (
        await db.execute(
            select(
                Tag.id,
                Tag.name,
                Tag.category,
                func.count().label("cnt"),
            )
            .join(InspirationTag, InspirationTag.tag_id == Tag.id)
            .where(
                InspirationTag.inspiration_id.in_(alive_ids),
                Tag.id != tag_id,
            )
            .group_by(Tag.id)
            .order_by(func.count().desc(), Tag.id.asc())
            .limit(limit)
        )
    ).all()
    materials = (await db.execute(select(func.count()).select_from(alive_ids.subquery()))).scalar() or 0
    return {
        "tag": {"id": tag.id, "name": tag.name, "category": tag.category},
        "materials": materials,
        "items": [
            {"id": tid, "name": name, "category": category, "count": cnt} for tid, name, category, cnt in rows
        ],
    }


async def list_watch_tags(db: AsyncSession, days: int = 7, max_usage: int = 1, limit: int = 200) -> dict:
    """AI 新建的「还没用起来」的标签（新标签观察名单）。

    为什么要这份名单：标签库 22,813 个里 99.9% 是 AI 打标自动建的，且 70% 只被用过
    1~2 次——真正的膨胀源是「AI 一打一个新标签」。这份名单把**最近 N 天新建、且用量
    仍 ≤ max_usage** 的 AI 标签捞出来，让人工决定「留（确认是有用的词）」还是「删」。

    Args:
        db: 数据库会话。
        days: 只看最近多少天新建的。
        max_usage: 用量上限（≤ 该值的才算「还没用起来」）。
        limit: 最多返回多少条。

    Returns:
        ``{"days","max_usage","cutoff","total","items":[{"id","name","category",
        "created_at","usage_count"}]}``。
    """
    from app.utils.time import utcnow

    cutoff = utcnow() - timedelta(days=days)
    usage_subq = (
        select(
            InspirationTag.tag_id.label("tag_id"),
            func.count().label("cnt"),
        )
        .join(Inspiration, Inspiration.id == InspirationTag.inspiration_id)
        .where(Inspiration.deleted_at.is_(None))
        .group_by(InspirationTag.tag_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                Tag.id,
                Tag.name,
                Tag.category,
                Tag.created_at,
                func.coalesce(usage_subq.c.cnt, 0).label("cnt"),
            )
            .outerjoin(usage_subq, usage_subq.c.tag_id == Tag.id)
            .where(
                Tag.source == "ai_generated",
                Tag.created_at >= cutoff,
                func.coalesce(usage_subq.c.cnt, 0) <= max_usage,
            )
            .order_by(Tag.created_at.desc(), Tag.id.desc())
            .limit(limit)
        )
    ).all()
    return {
        "days": days,
        "max_usage": max_usage,
        "cutoff": cutoff,
        "total": len(rows),
        "items": [
            {
                "id": tid,
                "name": name,
                "category": category,
                "created_at": created_at,
                "usage_count": cnt,
            }
            for tid, name, category, created_at, cnt in rows
        ],
    }


async def keep_watch_tags(db: AsyncSession, tag_ids: list[int]) -> int:
    """把观察名单里的标签标记为「人工确认保留」（source 改为 manual）。

    为什么用 source 而不是新字段：来源筛选（seed / ai_generated / manual）本来就是
    「这个标签怎么来的」，人工确认过就等同于人工维护的词，写 manual 语义准确且不需要
    迁移表结构；同时它自然从观察名单（只查 ai_generated）消失。

    Args:
        db: 数据库会话。
        tag_ids: 要确认保留的标签 id。

    Returns:
        实际改动条数（已经是 manual 的不计）。
    """
    if not tag_ids:
        return 0
    result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids), Tag.source == "ai_generated"))
    tags = result.scalars().all()
    for tag in tags:
        tag.source = "manual"
    await db.commit()
    return len(tags)
