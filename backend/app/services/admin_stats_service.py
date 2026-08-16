"""素材管理统计服务：概览仪表盘聚合与存储目录扫描。

此前 admin_stats 的 10+ 条聚合查询全部写在 routers/admin.py（152 行函数），
按「路由薄、业务在 services」的约定整体下沉到本模块，并拆分为
若干 ≤60 行的小函数，便于单独阅读与测试。
"""

import asyncio

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    NOT_DELETED,
    analysis_log_filter,
    latest_analysis_log_subquery,
)
from app.models.scraper import ScraperSeenURL
from app.models.tag import Tag, InspirationTag

# 素材媒体目录：完整性检查/存储统计只扫描这些目录，排除 lancedb（向量库）、
# logs（日志）、cookies、debug 等非素材数据，否则会把向量库内部文件误判为
# 「孤立文件」。垃圾桶（trash）同样不在此列：软删除文件已移入 trash/。
INSP_MEDIA_DIRS = ("images", "thumbnails", "videos")


def scan_storage_files() -> dict[str, int]:
    """扫描素材媒体目录中的实际文件，返回 {相对路径: 字节数}。"""
    files: dict[str, int] = {}
    storage_root = settings.storage_root
    if not storage_root.exists():
        return files
    for dir_name in INSP_MEDIA_DIRS:
        dir_path = storage_root / dir_name
        if not dir_path.exists():
            continue
        for fpath in dir_path.rglob("*"):
            if fpath.is_file():
                # 计算相对于 storage 的路径
                try:
                    rel = fpath.relative_to(storage_root).as_posix()
                except ValueError:
                    rel = str(fpath)
                files[rel] = fpath.stat().st_size
    return files


def _compute_storage_sizes(storage_files: dict[str, int]) -> dict[str, int]:
    """按目录聚合存储占用：总大小 / 缩略图 / 图片。"""
    return {
        "total_size_bytes": sum(storage_files.values()),
        "thumbnail_size_bytes": sum(
            s for p, s in storage_files.items() if "thumbnails" in p
        ),
        "images_size_bytes": sum(
            s for p, s in storage_files.items() if p.startswith("images")
        ),
    }


async def _query_base_counts(db: AsyncSession) -> dict[str, int]:
    """基础计数：素材总数、无标签数、收藏数、标签总数、墓碑数（均排除垃圾桶）。"""
    total_count = (await db.execute(
        select(func.count(Inspiration.id)).where(NOT_DELETED)
    )).scalar() or 0

    untagged_count = (await db.execute(
        select(func.count(Inspiration.id))
        .outerjoin(InspirationTag, Inspiration.id == InspirationTag.inspiration_id)
        .where(InspirationTag.inspiration_id.is_(None), NOT_DELETED)
    )).scalar() or 0

    favorite_count = (await db.execute(
        select(func.count(Inspiration.id))
        .where(Inspiration.is_favorite.is_(True), NOT_DELETED)
    )).scalar() or 0

    total_tags = (await db.execute(select(func.count(Tag.id)))).scalar() or 0

    tombstone_count = (await db.execute(
        select(func.count(ScraperSeenURL.source_url))
    )).scalar() or 0

    return {
        "total_count": total_count,
        "untagged_count": untagged_count,
        "favorite_count": favorite_count,
        "total_tags": total_tags,
        "tombstone_count": tombstone_count,
    }


async def _query_analysis_counts(db: AsyncSession) -> dict[str, int]:
    """分析状态计数：已分析（done）/ 失败（error）/ 未分析（pending）。"""
    non_deleted_ids = select(Inspiration.id).where(NOT_DELETED)

    analyzed_ids_subq = (
        select(AIAnalysisLog.inspiration_id)
        .where(
            analysis_log_filter(),
            AIAnalysisLog.inspiration_id.isnot(None),
            AIAnalysisLog.inspiration_id.in_(non_deleted_ids),
        )
        .distinct()
    ).subquery()

    # 「失败」按最新一条标签分析日志判定（latest 语义），
    # 避免「历史失败但最新已成功」的素材既计入已分析又被扣为失败
    latest_log_sub = latest_analysis_log_subquery()
    failed_ids_subq = (
        select(AIAnalysisLog.inspiration_id)
        .join(
            latest_log_sub,
            (AIAnalysisLog.inspiration_id == latest_log_sub.c.inspiration_id)
            & (AIAnalysisLog.id == latest_log_sub.c.max_id),
        )
        .where(
            AIAnalysisLog.error.isnot(None),
            AIAnalysisLog.error != "",
            AIAnalysisLog.inspiration_id.in_(non_deleted_ids),
        )
        .distinct()
    ).subquery()

    analyzed_count = (await db.execute(
        select(func.count()).select_from(analyzed_ids_subq)
    )).scalar() or 0

    error_count = (await db.execute(
        select(func.count()).select_from(failed_ids_subq)
    )).scalar() or 0

    total_count = (await db.execute(
        select(func.count(Inspiration.id)).where(NOT_DELETED)
    )).scalar() or 0

    return {
        "analyzed_count": analyzed_count,
        "error_count": error_count,
        "pending_count": total_count - analyzed_count,
    }


async def _query_distributions(db: AsyncSession) -> dict[str, list]:
    """分布统计：按来源类型 / 媒体类型 / 月份（最近 12 个月）。"""
    source_stats = (await db.execute(
        select(Inspiration.source_type, func.count(Inspiration.id))
        .where(NOT_DELETED)
        .group_by(Inspiration.source_type)
    )).all()

    media_stats = (await db.execute(
        select(Inspiration.media_type, func.count(Inspiration.id))
        .where(NOT_DELETED)
        .group_by(Inspiration.media_type)
    )).all()

    month_stats = (await db.execute(
        select(
            func.strftime("%Y-%m", Inspiration.created_at).label("month"),
            func.count(Inspiration.id).label("count"),
        )
        .where(Inspiration.created_at.isnot(None), NOT_DELETED)
        .group_by("month")
        .order_by(text("month DESC"))
        .limit(12)
    )).all()

    return {
        "by_source_type": [
            {"source_type": s[0] or "unknown", "count": s[1]} for s in source_stats
        ],
        "by_media_type": [
            {"media_type": s[0] or "unknown", "count": s[1]} for s in media_stats
        ],
        "by_month": [{"month": s[0], "count": s[1]} for s in month_stats],
    }


async def collect_stats(db: AsyncSession) -> dict:
    """汇总素材总览仪表盘数据（原 routers/admin.py 的 admin_stats 逻辑）。"""
    base = await _query_base_counts(db)
    analysis = await _query_analysis_counts(db)
    distributions = await _query_distributions(db)
    sizes = _compute_storage_sizes(await asyncio.to_thread(scan_storage_files))

    return {
        **base,
        **sizes,
        "analysis_failed_count": analysis["error_count"],
        "by_analysis_status": [
            {"status": "done", "count": analysis["analyzed_count"] - analysis["error_count"], "label": "已分析"},
            {"status": "error", "count": analysis["error_count"], "label": "分析失败"},
            {"status": "pending", "count": analysis["pending_count"], "label": "未分析"},
        ],
        **distributions,
    }
