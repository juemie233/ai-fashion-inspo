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


def _fmt_utc(dt) -> str | None:
    """将 naive UTC datetime 格式化为带 Z 后缀的 ISO 字符串。"""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _model_comparison(db: AsyncSession) -> list[dict]:
    """按模型聚合的成功率对比（仅标签分析，排除质量审核日志与垃圾桶素材）。"""
    result = await db.execute(
        select(
            AIAnalysisLog.model_name,
            func.count().label("total"),
            func.sum(case((AIAnalysisLog.error.is_(None), 1), else_=0)).label("success"),
        )
        .where(analysis_log_filter())
        .group_by(AIAnalysisLog.model_name)
        .order_by(func.count().desc())
    )
    return [
        {
            "model_name": row[0],
            "total": row[1],
            "success": row[2] or 0,
            "success_rate": round((row[2] or 0) / row[1] * 100, 1) if row[1] else 0,
        }
        for row in result.all()
    ]


def _classify_error(error: str) -> str:
    """按关键词归类失败原因，供错误分布统计使用。"""
    if not error:
        return "未知"
    low = error.lower()
    if "timeout" in low or "超时" in error:
        return "超时"
    if "connect" in low or "connection" in low or "refused" in low or "连接" in error:
        return "连接失败"
    if "http" in low or "status" in low:
        return "HTTP 错误"
    if "parse" in low or "json" in low or "解析" in error or "格式" in error:
        return "解析失败"
    if "context" in low or "token" in low or "截断" in error:
        return "上下文/截断"
    return "其他"


async def _error_distribution(db: AsyncSession) -> list[dict]:
    """失败日志的错误原因分布（按关键词归类后聚合）。"""
    result = await db.execute(
        select(AIAnalysisLog.error)
        .where(analysis_log_filter(), AIAnalysisLog.error.isnot(None))
    )
    counter: dict[str, int] = {}
    for (error,) in result.all():
        category = _classify_error(error or "")
        counter[category] = counter.get(category, 0) + 1
    return [
        {"category": category, "count": count}
        for category, count in sorted(counter.items(), key=lambda x: -x[1])
    ]


async def _failed_items(db: AsyncSession, limit: int = 20) -> list[dict]:
    """最近失败的素材列表（供前端直达跳转），每个素材取最新一条失败日志。"""
    latest_sub = (
        select(AIAnalysisLog.inspiration_id, func.max(AIAnalysisLog.id).label("max_id"))
        .where(analysis_log_filter(), AIAnalysisLog.error.isnot(None))
        .group_by(AIAnalysisLog.inspiration_id)
        .subquery()
    )
    result = await db.execute(
        select(
            AIAnalysisLog.inspiration_id,
            AIAnalysisLog.model_name,
            AIAnalysisLog.error,
            AIAnalysisLog.created_at,
            Inspiration.thumbnail_path,
        )
        .join(latest_sub, AIAnalysisLog.id == latest_sub.c.max_id)
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(Inspiration.deleted_at.is_(None))
        .order_by(AIAnalysisLog.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "inspiration_id": row[0],
            "model_name": row[1],
            "error": row[2],
            "created_at": _fmt_utc(row[3]),
            "thumbnail_path": row[4],
        }
        for row in result.all()
    ]


async def collect_quality_dashboard(db: AsyncSession) -> dict:
    """汇总分析质量仪表盘数据（原 routers/ai_dashboard.py 的 quality_dashboard 逻辑）。"""
    return {
        "daily_trends": await _daily_trends(db),
        "overview": await _overview(db),
        "problem_items": await _problem_items(db),
        "model_comparison": await _model_comparison(db),
        "error_distribution": await _error_distribution(db),
        "failed_items": await _failed_items(db),
    }
