"""人物详情页的洞察数据：活跃度（新增曲线）、质量构成、风格相近、疑似重复。

为什么单独一个模块：这四项都是「只读聚合」，与人物 CRUD 无关；放在人物路由里会让
``bloggers.py`` 继续膨胀，也会把 4 条互不相关的 SQL 混进同一个函数。

口径统一说明：

- **活跃度/质量构成**只看「未删除素材」，与素材列表、风格画像一致；
- **新增曲线**按素材**入库时间**（``inspirations.created_at``）而不是作品发布时间——
  库里没有作品发布时间字段，而用户要判断的是「这个号还在更新吗」（采集入库时间足够）；
- **风格相近**用标签集合的余弦相似度（共同标签 / 几何均值），只比同类型人物（博对博、
  模特对模特），避免「博主和模特标签天然差很远」的无效推荐。
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration, utcnow
from app.models.tag import InspirationTag

logger = logging.getLogger(__name__)

# 风格相近：共同标签数下限（低于 2 个共同标签的「相似」基本都是巧合）
MIN_SHARED_TAGS = 3
# 相似度下限：余弦 < 0.2 的只在「标签极少」时出现，直接丢掉
MIN_SIMILARITY = 0.2


def _shift_months(dt: datetime, delta: int) -> datetime:
    """把日期按自然月平移（保留日），用于计算连续月份窗口的起点。

    Args:
        dt: 基准时间。
        delta: 平移月数（负数为往前）。

    Returns:
        平移后的时间（月初调用者自行 replace(day=1)）。
    """
    total = dt.year * 12 + (dt.month - 1) + delta
    year, month = divmod(total, 12)
    return dt.replace(year=year, month=month + 1)


async def person_insights(
    db: AsyncSession, link_model, link_id_col, person_id: int, months: int = 12
) -> dict:
    """人物洞察：月度新增、质量构成、首末素材时间、媒体类型构成。

    Args:
        db: 数据库会话。
        link_model: 素材关联模型（InspirationBlogger / InspirationModel）。
        link_id_col: 关联表上的主体外键列。
        person_id: 人物 ID。
        months: 新增曲线回溯月数（默认 12）。

    Returns:
        ``{"monthly": [{"month": "2026-10", "count": 12}], "quality": {...},
        "media": {...}, "first_added": ..., "last_added": ..., "total": n}``；
        无素材时各计数为 0、``monthly`` 为空列表。
    """
    rows = (
        await db.execute(
            select(
                Inspiration.created_at,
                Inspiration.quality_status,
                Inspiration.media_type,
            )
            .join(link_model, link_model.inspiration_id == Inspiration.id)
            .where(link_id_col == person_id, Inspiration.deleted_at.is_(None))
        )
    ).all()

    quality = {"approved": 0, "pending": 0, "rejected": 0, "unreviewed": 0}
    media: dict[str, int] = {}
    buckets: dict[str, int] = {}
    now = utcnow()
    # 窗口为「最近 months 个自然月，含当月」：起点 = 当月往前推 months-1 个月。
    # 注意不能用 timedelta(days=31*(months-1)) 近似——各月 31/30 天不同，实测
    # months=3 会算成 7/8/9 月（当月 10 月被整个丢掉），恰好与注释「含当月」矛盾。
    start_month = _shift_months(now.replace(day=1), -(months - 1))
    first_added = None
    last_added = None

    for created_at, quality_status, media_type in rows:
        media[media_type] = media.get(media_type, 0) + 1
        quality[quality_status or "unreviewed"] = (
            quality.get(quality_status or "unreviewed", 0) + 1
        )
        if created_at:
            if first_added is None or created_at < first_added:
                first_added = created_at
            if last_added is None or created_at > last_added:
                last_added = created_at
            if created_at >= start_month:
                key = created_at.strftime("%Y-%m")
                buckets[key] = buckets.get(key, 0) + 1

    # 补零：前端画柱状图需要连续月份（缺月不补会出现「跳月」的假象）
    monthly: list[dict] = []
    cursor = start_month
    for _ in range(months):
        key = cursor.strftime("%Y-%m")
        monthly.append({"month": key, "count": buckets.get(key, 0)})
        cursor = (cursor.replace(day=28) + timedelta(days=7)).replace(day=1)

    return {
        "total": len(rows),
        "monthly": monthly,
        "quality": quality,
        "media": media,
        "first_added": str(first_added) if first_added else None,
        "last_added": str(last_added) if last_added else None,
    }


async def similar_persons(
    db: AsyncSession,
    person_model,
    link_model,
    link_id_col,
    person_id: int,
    limit: int = 5,
) -> list[dict]:
    """风格相近的人物（按标签集合余弦相似度）。

    实现：先取「我」的标签集合与其它人物的标签数（一次聚合），再在 Python 里算
    余弦——标签是稀疏的小集合（实测人均几十个），Python 比在 SQLite 里写窗口函数
    更快也更好读。只看同类型人物（调用方传对应 model/link_model）。

    Args:
        db: 数据库会话。
        person_model: 人物模型（Blogger / Model）。
        link_model: 素材关联模型。
        link_id_col: 关联表上的主体外键列。
        person_id: 人物 ID。
        limit: 返回条数上限。

    Returns:
        ``[{"id", "name", "avatar_path", "inspiration_count", "shared_tags",
        "similarity"}]``，按相似度降序。
    """
    my_tag_rows = (
        await db.execute(
            select(InspirationTag.tag_id, func.count())
            .join(link_model, link_model.inspiration_id == InspirationTag.inspiration_id)
            .join(Inspiration, Inspiration.id == InspirationTag.inspiration_id)
            .where(link_id_col == person_id, Inspiration.deleted_at.is_(None))
            .group_by(InspirationTag.tag_id)
        )
    ).all()
    my_tags = {row[0]: row[1] for row in my_tag_rows}
    if not my_tags:
        return []

    shared_rows = (
        await db.execute(
            select(
                link_id_col.label("pid"),
                InspirationTag.tag_id,
                func.count().label("n"),
            )
            .join(
                InspirationTag,
                InspirationTag.inspiration_id == link_model.inspiration_id,
            )
            .join(Inspiration, Inspiration.id == InspirationTag.inspiration_id)
            .where(
                Inspiration.deleted_at.is_(None),
                InspirationTag.tag_id.in_(list(my_tags)),
                link_id_col != person_id,
            )
            .group_by(link_id_col, InspirationTag.tag_id)
        )
    ).all()

    shared: dict[int, list[int]] = {}
    for pid, tag_id, _n in shared_rows:
        shared.setdefault(int(pid), []).append(int(tag_id))

    candidates = [
        (pid, len(tags))
        for pid, tags in shared.items()
        if len(tags) >= MIN_SHARED_TAGS
    ]
    if not candidates:
        return []

    # 每个候选人物的标签总数（算余弦的分母）
    counts_rows = (
        await db.execute(
            select(link_id_col, func.count(func.distinct(InspirationTag.tag_id)))
            .join(
                InspirationTag,
                InspirationTag.inspiration_id == link_model.inspiration_id,
            )
            .join(Inspiration, Inspiration.id == InspirationTag.inspiration_id)
            .where(
                Inspiration.deleted_at.is_(None),
                link_id_col.in_([pid for pid, _ in candidates]),
            )
            .group_by(link_id_col)
        )
    ).all()
    counts = {int(pid): int(n) for pid, n in counts_rows}

    scored = []
    for pid, shared_count in candidates:
        other_total = counts.get(pid, 0)
        if not other_total:
            continue
        similarity = shared_count / math.sqrt(len(my_tags) * other_total)
        if similarity >= MIN_SIMILARITY:
            scored.append((pid, shared_count, similarity))
    scored.sort(key=lambda x: (-x[2], -x[1]))
    top = scored[:limit]
    if not top:
        return []

    persons = (
        await db.execute(
            select(
                person_model.id,
                person_model.name,
                person_model.avatar_path,
            ).where(person_model.id.in_([pid for pid, _, _ in top]))
        )
    ).all()
    info = {int(row[0]): row for row in persons}

    counts_all = (
        await db.execute(
            select(link_id_col, func.count())
            .join(Inspiration, Inspiration.id == link_model.inspiration_id)
            .where(
                Inspiration.deleted_at.is_(None),
                link_id_col.in_([pid for pid, _, _ in top]),
            )
            .group_by(link_id_col)
        )
    ).all()
    insp_counts = {int(pid): int(n) for pid, n in counts_all}

    return [
        {
            "id": pid,
            "name": info[pid][1] if pid in info else f"#{pid}",
            "avatar_path": info[pid][2] if pid in info else None,
            "inspiration_count": insp_counts.get(pid, 0),
            "shared_tags": shared_count,
            "similarity": round(similarity, 3),
        }
        for pid, shared_count, similarity in top
    ]
