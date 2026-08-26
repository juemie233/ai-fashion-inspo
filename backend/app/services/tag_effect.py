"""标签使用效果分析：热度升降榜 / 组合排行 / 覆盖度 / 来源分布。

全部为同步聚合查询（SQL 层一次性算好，内存只做排序/拼装），
数据量级（数千标签 × 数万素材）下毫秒级完成，无需异步任务。
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services.task_runners.common import _chunked

# 组合统计的单素材活跃标签数上限：超过则跳过该素材（防组合爆炸）
_MAX_TAGS_PER_INSPIRATION = 50


async def get_trending_tags(
    db: AsyncSession, days: int = 30, top: int = 20
) -> dict:
    """热度升降榜：对比最近 days 天与前一 days 天的素材关联数。

    一次 SQL 按 (tag_id, 窗口) 聚合近 2×days 天内的关联（素材 created_at
    分桶，仅统计未删除素材），内存算 delta 后取上升/下降 Top-N。
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    prev_cutoff = now - timedelta(days=2 * days)

    in_cur_window = Inspiration.created_at >= cutoff
    result = await db.execute(
        select(
            InspirationTag.tag_id,
            func.sum(case((in_cur_window, 1), else_=0)).label("current"),
            func.sum(case((~in_cur_window, 1), else_=0)).label("previous"),
        )
        .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
        .where(
            Inspiration.deleted_at.is_(None),
            Inspiration.created_at >= prev_cutoff,
        )
        .group_by(InspirationTag.tag_id)
    )
    rows = result.all()

    # 标签名映射（批量取）
    tag_ids = [r[0] for r in rows]
    name_map: dict[int, str] = {}
    for chunk in _chunked(tag_ids):
        names = await db.execute(select(Tag.id, Tag.name).where(Tag.id.in_(chunk)))
        name_map.update({tid: name for tid, name in names.all()})

    stats = [
        {
            "id": tid,
            "name": name_map.get(tid, f"#{tid}"),
            "current": cur,
            "previous": prev,
            "delta": cur - prev,
        }
        for tid, cur, prev in rows
    ]
    stats.sort(key=lambda x: (-x["delta"], -x["current"]))
    rising = stats[:top]
    falling = sorted(stats, key=lambda x: (x["delta"], -x["current"]))[:top]
    return {"days": days, "rising": rising, "falling": falling}


async def get_tag_combinations(
    db: AsyncSession, limit: int = 20, min_count: int = 2
) -> dict:
    """标签组合排行：活跃标签子集（使用次数 top 200）内两两共现计数。

    返回按共现次数降序的组合对（{tags: [名A, 名B], count}）。

    性能护栏：单素材关联的活跃标签数超过 ``_MAX_TAGS_PER_INSPIRATION`` 时跳过
    该素材的组合统计（组合数是标签数的平方，病态素材会拖垮接口；正常 AI 打标
    一图仅数个到十几个标签，远低于阈值）。
    """
    top_result = await db.execute(
        select(Tag.id, func.count(InspirationTag.inspiration_id).label("cnt"))
        .join(InspirationTag, Tag.id == InspirationTag.tag_id)
        .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
        .where(Inspiration.deleted_at.is_(None))
        .group_by(Tag.id)
        .order_by(func.count(InspirationTag.inspiration_id).desc())
        .limit(200)
    )
    active_ids = [r[0] for r in top_result.all()]
    if len(active_ids) < 2:
        return {"pairs": [], "total": 0}

    # 取活跃标签的全部关联，内存统计共现
    insp_map: dict[str, set[int]] = {}
    for chunk in _chunked(active_ids):
        links = await db.execute(
            select(InspirationTag.inspiration_id, InspirationTag.tag_id)
            .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
            .where(
                InspirationTag.tag_id.in_(chunk),
                Inspiration.deleted_at.is_(None),
            )
        )
        for insp_id, tag_id in links.all():
            insp_map.setdefault(insp_id, set()).add(tag_id)

    pair_count: dict[tuple[int, int], int] = {}
    for tag_set in insp_map.values():
        if len(tag_set) > _MAX_TAGS_PER_INSPIRATION:
            continue  # 病态素材（一次打了数十个活跃标签）：跳过，防组合爆炸
        sorted_ids = sorted(tag_set)
        for i in range(len(sorted_ids)):
            for j in range(i + 1, len(sorted_ids)):
                key = (sorted_ids[i], sorted_ids[j])
                pair_count[key] = pair_count.get(key, 0) + 1

    ranked = sorted(pair_count.items(), key=lambda x: -x[1])
    filtered = [(pair, w) for pair, w in ranked if w >= min_count]

    name_map: dict[int, str] = {}
    for chunk in _chunked([tid for pair, _ in filtered for tid in pair]):
        names = await db.execute(select(Tag.id, Tag.name).where(Tag.id.in_(chunk)))
        name_map.update({tid: name for tid, name in names.all()})

    pairs = [
        {
            "tags": [name_map.get(pair[0], f"#{pair[0]}"), name_map.get(pair[1], f"#{pair[1]}")],
            "count": w,
        }
        for pair, w in filtered[:limit]
    ]
    return {"pairs": pairs, "total": len(filtered)}


async def get_tag_coverage(db: AsyncSession) -> dict:
    """覆盖度统计：素材带标签比例、单素材平均标签数、按类别覆盖率。"""
    insp_total = (
        await db.execute(
            select(func.count())
            .select_from(Inspiration)
            .where(Inspiration.deleted_at.is_(None))
        )
    ).scalar() or 0

    # 带标签素材数 + 每素材标签数（仅未删除素材）
    tagged = (
        await db.execute(
            select(func.count(func.distinct(InspirationTag.inspiration_id)))
            .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
            .where(Inspiration.deleted_at.is_(None))
        )
    ).scalar() or 0

    avg_result = await db.execute(
        select(
            func.avg(
                select(func.count())
                .select_from(InspirationTag)
                .where(InspirationTag.inspiration_id == Inspiration.id)
                .scalar_subquery()
            )
        ).where(Inspiration.deleted_at.is_(None))
    )
    avg_tags = round(float(avg_result.scalar() or 0.0), 2)

    # 按类别覆盖率：多少比例的素材至少带该类别的一个标签
    by_cat_result = await db.execute(
        select(
            Tag.category,
            func.count(func.distinct(InspirationTag.inspiration_id)),
        )
        .join(Tag, Tag.id == InspirationTag.tag_id)
        .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
        .where(Inspiration.deleted_at.is_(None))
        .group_by(Tag.category)
    )
    by_category = {
        cat: round(cnt / insp_total, 2) if insp_total else 0.0
        for cat, cnt in by_cat_result.all()
    }

    return {
        "inspiration_total": insp_total,
        "with_tags": tagged,
        "tagged_ratio": round(tagged / insp_total, 2) if insp_total else 0.0,
        "avg_tags_per_inspiration": avg_tags,
        "by_category": by_category,
    }


async def get_tag_source_dist(db: AsyncSession) -> dict:
    """标签来源分布：每来源标签数/总使用次数/平均使用次数 + Top 低效 AI 标签。"""
    result = await db.execute(
        select(
            Tag.source,
            func.count(Tag.id),
            func.count(Inspiration.id),
        )
        .outerjoin(InspirationTag, Tag.id == InspirationTag.tag_id)
        .outerjoin(
            Inspiration,
            and_(
                InspirationTag.inspiration_id == Inspiration.id,
                Inspiration.deleted_at.is_(None),
            ),
        )
        .group_by(Tag.source)
    )
    by_source = {}
    for source, tag_count, usage_total in result.all():
        by_source[source] = {
            "tag_count": tag_count,
            "usage_total": usage_total,
            "avg_usage": round(usage_total / tag_count, 1) if tag_count else 0.0,
        }

    # Top 低效标签：AI 生成且使用次数 ≤ 1（疑似打标噪声）
    low_result = await db.execute(
        select(
            Tag.id,
            Tag.name,
            Tag.source,
            func.count(Inspiration.id).label("cnt"),
        )
        .outerjoin(InspirationTag, Tag.id == InspirationTag.tag_id)
        .outerjoin(
            Inspiration,
            and_(
                InspirationTag.inspiration_id == Inspiration.id,
                Inspiration.deleted_at.is_(None),
            ),
        )
        .where(Tag.source == "ai_generated")
        .group_by(Tag.id)
        .having(func.count(Inspiration.id) <= 1)
        .order_by(func.count(Inspiration.id).asc(), Tag.id.asc())
        .limit(10)
    )
    top_low = [
        {"id": r[0], "name": r[1], "source": r[2], "usage_count": r[3]}
        for r in low_result.all()
    ]
    return {"by_source": by_source, "top_low_quality": top_low}
