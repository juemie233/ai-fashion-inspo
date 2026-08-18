"""采集统计与任务结果：看板聚合、结果列表、结果批量移入垃圾桶。"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.scraper import ScraperTask
from app.services.audit_service import record_audit_log
from app.services.file_service import move_to_trash
from app.services.inspiration_service import _mark_trashed, _resolve_trash_reason
from app.services.scraper_seen_service import seal_urls
from app.utils.time import format_utc, utcnow

logger = logging.getLogger(__name__)


async def get_scraper_stats(days: int = 30) -> dict:
    """聚合近 N 天的采集任务统计：总量、成功率、按平台与按日分布。"""
    since = utcnow() - timedelta(days=days)
    _total = func.count(ScraperTask.id)
    _completed = func.sum(case((ScraperTask.status == "completed", 1), else_=0))
    _failed = func.sum(case((ScraperTask.status == "failed", 1), else_=0))
    _found = func.coalesce(func.sum(ScraperTask.items_found), 0)
    _added = func.coalesce(func.sum(ScraperTask.items_added), 0)

    async with async_session() as db:
        overall = (await db.execute(
            select(_total, _completed, _failed, _found, _added)
            .where(ScraperTask.created_at >= since)
        )).one()

        platform_rows = (await db.execute(
            select(ScraperTask.platform, _total, _found, _added, _completed)
            .where(ScraperTask.created_at >= since)
            .group_by(ScraperTask.platform)
        )).all()

        # SQLite date() 直接作用于 UTC 时间戳列，按自然日聚合
        day_rows = (await db.execute(
            select(func.date(ScraperTask.created_at), _total, _added, _failed)
            .where(ScraperTask.created_at >= since)
            .group_by(func.date(ScraperTask.created_at))
            .order_by(func.date(ScraperTask.created_at))
        )).all()

    total = int(overall[0] or 0)
    completed = int(overall[1] or 0)
    failed = int(overall[2] or 0)

    return {
        "days": days,
        "total_tasks": total,
        "completed": completed,
        "failed": failed,
        "success_rate": round(completed / total * 100, 1) if total else 0,
        "total_found": int(overall[3] or 0),
        "total_added": int(overall[4] or 0),
        "by_platform": [
            {
                "platform": p,
                "tasks": int(t or 0),
                "found": int(f or 0),
                "added": int(a or 0),
                "completed": int(c or 0),
            }
            for p, t, f, a, c in platform_rows
        ],
        "by_day": [
            {
                "date": d,
                "tasks": int(t or 0),
                "added": int(a or 0),
                "failed": int(f or 0),
            }
            for d, t, a, f in day_rows
        ],
    }


async def get_task_results(
    db: AsyncSession,
    task_id: int,
    page: int = 1,
    size: int = 50,
) -> dict:
    """获取指定采集任务产出的素材列表（缩略图网格）。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="采集任务未找到")

    # 计数（排除垃圾桶中的软删除素材，与素材库正常列表口径一致）
    count_result = await db.execute(
        select(func.count(Inspiration.id)).where(
            Inspiration.scraper_task_id == task_id,
            Inspiration.deleted_at.is_(None),
        )
    )
    total = count_result.scalar() or 0

    # 分页
    items_result = await db.execute(
        select(Inspiration)
        .where(
            Inspiration.scraper_task_id == task_id,
            Inspiration.deleted_at.is_(None),
        )
        .order_by(Inspiration.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    items = items_result.scalars().all()

    def _fmt(dt) -> str | None:
        return format_utc(dt)

    return {
        "task": {
            "id": task.id,
            "platform": task.platform,
            "status": task.status,
            "config": task.config,
            "items_found": task.items_found,
            "items_added": task.items_added,
            "error": task.error,
            "started_at": _fmt(task.started_at),
            "finished_at": _fmt(task.finished_at),
            "created_at": _fmt(task.created_at),
        },
        "total": total,
        "page": page,
        "size": size,
        "items": [
            {
                "id": i.id,
                "file_path": i.file_path,
                "thumbnail_path": i.thumbnail_path,
                "media_type": i.media_type,
                "source_url": i.source_url,
                "is_favorite": i.is_favorite,
                "created_at": str(i.created_at) if i.created_at else None,
            }
            for i in items
        ],
    }


async def batch_delete_task_results(
    db: AsyncSession,
    task_id: int,
    ids: list[str],
    reason: str | None = None,
) -> dict:
    """批量将采集任务产出的指定素材移入垃圾桶（软删除，可恢复）。

    请求体: {"ids": ["id1", "id2", ...], "reason": "不喜欢"}

    与素材库单条软删除语义一致：标记 deleted_at / trash_reason、文件移入
    storage/trash/、向量保留（负样本学习依赖垃圾桶素材向量）。软删除即写入
    来源 URL 墓碑，采集器后续遇到该 URL 会直接跳过，不再重复采集。
    """
    if not ids:
        raise HTTPException(status_code=400, detail="请提供要删除的素材 ID 列表")

    # 仅处理属于该任务的素材；已在垃圾桶中的计入 skipped，不重复软删除
    result = await db.execute(
        select(Inspiration).where(
            Inspiration.id.in_(ids),
            Inspiration.scraper_task_id == task_id,
        )
    )
    inspirations = result.scalars().all()

    trashed_items: list[Inspiration] = []
    skipped = 0
    for insp in inspirations:
        if insp.deleted_at is not None:
            skipped += 1
            continue
        # 三字段经 _mark_trashed 单点写入：采集结果删除属于「自动移动」来源，
        # 必须携带 trash_source=auto（此前遗漏导致垃圾桶来源显示为手动移入）
        _mark_trashed(insp, _resolve_trash_reason(reason, insp), "auto")
        trashed_items.append(insp)

    # 先提交软删除标记与来源 URL 墓碑（同一事务），提交成功后再移动文件，避免
    # 「文件已移走但事务回滚/失败」导致 DB 仍指向原路径的悬空记录
    if trashed_items:
        await seal_urls(db, [insp.source_url for insp in trashed_items if insp.source_url])
        await db.commit()

    # 移动文件到垃圾桶目录；失败仅记日志不阻断软删除（恢复时按 DB 路径自愈）
    paths_changed = False
    for insp in trashed_items:
        try:
            new_file = move_to_trash(insp.file_path, insp.id)
            if new_file:
                insp.file_path = new_file
                paths_changed = True
        except OSError as e:
            logger.warning(f"移动主文件到垃圾桶失败 {insp.id}: {e}")
        try:
            new_thumb = move_to_trash(insp.thumbnail_path, insp.id, suffix="_thumb")
            if new_thumb:
                insp.thumbnail_path = new_thumb
                paths_changed = True
        except OSError as e:
            logger.warning(f"移动缩略图到垃圾桶失败 {insp.id}: {e}")

    if paths_changed:
        await db.commit()

    # 更新任务计数：只统计未删除素材（与结果列表口径一致）
    remaining_result = await db.execute(
        select(func.count(Inspiration.id)).where(
            Inspiration.scraper_task_id == task_id,
            Inspiration.deleted_at.is_(None),
        )
    )
    remaining = remaining_result.scalar() or 0

    task = await db.get(ScraperTask, task_id)
    if task:
        task.items_added = remaining
        await db.commit()

    # 批量移入垃圾桶纳入审计，便于追溯批量整理动作
    if trashed_items:
        detail = f"采集任务 {task_id} 结果移入垃圾桶"
        if skipped:
            detail += f"，跳过 {skipped} 个（已在垃圾桶）"
        await record_audit_log(
            action="batch_trash",
            count=len(trashed_items),
            detail=detail,
        )

    return {
        "trashed_count": len(trashed_items),
        "skipped": skipped,
        "remaining": remaining,
    }
