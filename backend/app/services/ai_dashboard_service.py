"""分析质量仪表盘服务：每日趋势、问题素材与覆盖率聚合。

此前该逻辑（100 行）整体写在 routers/ai_dashboard.py，按「路由薄、
业务在 services」约定下沉到本模块，并按指标维度拆成小函数。
"""

from datetime import timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import AIAnalysisLog, Inspiration, analysis_log_filter
from app.models.tag import InspirationTag
from app.utils.time import utcnow


async def _daily_trends(db: AsyncSession) -> list[dict]:
    """最近 30 天的每日分析统计（总量与成功量）。

    created_at 由 SQLite CURRENT_TIMESTAMP（UTC）生成，截止时间须用 UTC
    计算，避免非 UTC 时区下统计窗口偏移。
    """
    thirty_days_ago = utcnow() - timedelta(days=30)
    result = await db.execute(
        select(
            func.date(AIAnalysisLog.created_at).label("day"),
            func.count().label("total"),
            func.sum(case((AIAnalysisLog.error.is_(None), 1), else_=0)).label("success"),
        )
        .where(analysis_log_filter(), AIAnalysisLog.created_at >= thirty_days_ago)
        .group_by("day")
        .order_by("day")
    )
    return [
        {"day": row[0], "total": row[1], "success": row[2] or 0}
        for row in result.all()
    ]


async def _overview(db: AsyncSession) -> dict:
    """覆盖率概览：素材总数、已分析数、平均标签数与平均耗时（排除垃圾桶）。"""
    total_insp = (await db.execute(
        select(func.count(Inspiration.id)).where(Inspiration.deleted_at.is_(None))
    )).scalar() or 0

    # 已分析素材只统计未删除素材的日志，与 total_insp 口径一致，
    # 否则「已分析数 > 素材总数」导致未分析数为负
    analyzed_ids = (
        select(AIAnalysisLog.inspiration_id)
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(analysis_log_filter(), Inspiration.deleted_at.is_(None))
        .distinct()
    )
    analyzed_count = (await db.execute(
        select(func.count()).select_from(analyzed_ids.subquery())
    )).scalar() or 0

    avg_tags = 0
    if analyzed_count > 0:
        tag_total = (await db.execute(
            select(func.count()).select_from(InspirationTag)
        )).scalar() or 0
        avg_tags = round(tag_total / analyzed_count, 1)

    avg_time = (await db.execute(
        select(func.avg(AIAnalysisLog.processing_time_ms))
        .where(analysis_log_filter(), AIAnalysisLog.error.is_(None))
    )).scalar() or 0

    return {
        "total_inspirations": total_insp,
        "analyzed_count": analyzed_count,
        "unanalyzed_count": max(0, total_insp - analyzed_count),
        "coverage_percent": round(analyzed_count / total_insp * 100, 1) if total_insp > 0 else 0,
        "avg_tags_per_image": avg_tags,
        "avg_time_ms": round(avg_time),
    }


async def _problem_items(db: AsyncSession) -> dict:
    """问题素材：多次失败（≥3 次）数量与零标签输出数量（排除垃圾桶）。"""
    fail_count_sub = (
        select(AIAnalysisLog.inspiration_id, func.count().label("fc"))
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(
            analysis_log_filter(),
            AIAnalysisLog.error.isnot(None),
            Inspiration.deleted_at.is_(None),
        )
        .group_by(AIAnalysisLog.inspiration_id)
        .having(func.count() >= 3)
        .subquery()
    )
    multi_fail = (await db.execute(
        select(func.count()).select_from(fail_count_sub)
    )).scalar() or 0

    zero_tag_result = await db.execute(
        select(func.count())
        .select_from(AIAnalysisLog)
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(
            analysis_log_filter(),
            AIAnalysisLog.error.is_(None),
            Inspiration.deleted_at.is_(None),
            ~AIAnalysisLog.inspiration_id.in_(
                select(InspirationTag.inspiration_id).distinct()
            ),
        )
    )
    zero_tag_count = zero_tag_result.scalar() or 0

    return {
        "multi_fail_count": multi_fail,
        "zero_tag_count": zero_tag_count,
    }


async def collect_quality_dashboard(db: AsyncSession) -> dict:
    """汇总分析质量仪表盘数据（原 routers/ai_dashboard.py 的 quality_dashboard 逻辑）。"""
    return {
        "daily_trends": await _daily_trends(db),
        "overview": await _overview(db),
        "problem_items": await _problem_items(db),
    }
