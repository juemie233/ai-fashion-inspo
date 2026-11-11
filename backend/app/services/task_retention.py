"""任务 result 保留策略（2026-11-06 数据库审查 P1-2）。

`task_queue.result` 历史累计 82.6MB（最大单条 2.9MB）；任务历史本身需要保留，但
完整结果只在排障时有价值，列表/运行记录都读 `result_summary`。这里按「终态任务超过
N 天清空 result、保留 result_summary」控制体积，完整结果不再无限增长。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.task import TaskQueue
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

TERMINAL_TASK_STATUSES = ("success", "failed", "cancelled")


async def prune_task_results(
    db: AsyncSession,
    *,
    days: int | None = None,
    dry_run: bool = False,
) -> dict:
    """清空过期终态任务的完整 result（保留 result_summary）。

    Args:
        days: 保留天数；缺省取 ``settings.task_result_retention_days``，0=关闭。
        dry_run: True 只统计不写库（供运维脚本先预览）。

    Returns:
        ``{"scanned": N, "pruned": M, "bytes": 释放的业务载荷字节数}``
    """
    keep_days = settings.task_result_retention_days if days is None else days
    if keep_days <= 0:
        return {"scanned": 0, "pruned": 0, "bytes": 0}

    cutoff = utcnow() - timedelta(days=keep_days)
    condition = (
        TaskQueue.status.in_(TERMINAL_TASK_STATUSES),
        TaskQueue.updated_at < cutoff,
        TaskQueue.result.is_not(None),
    )
    row = (
        await db.execute(
            select(
                func.count(TaskQueue.id),
                func.coalesce(func.sum(func.length(TaskQueue.result)), 0),
            ).where(*condition)
        )
    ).one()
    scanned, bytes_freed = int(row[0] or 0), int(row[1] or 0)

    if not dry_run and scanned:
        # 用 Core update 绕过 ORM 的 result_summary 事件：
        # 只清完整结果，摘要必须原样保留。
        await db.execute(update(TaskQueue).where(*condition).values(result=None))
        await db.commit()
        logger.info(
            "[任务结果保留] 清空 %s 条超过 %s 天的任务 result，释放 %.1f MB 载荷",
            scanned,
            keep_days,
            bytes_freed / 1048576,
        )

    return {"scanned": scanned, "pruned": 0 if dry_run else scanned, "bytes": bytes_freed}


async def prune_task_results_in_session(*, dry_run: bool = False) -> dict:
    """独立会话入口：启动时/运维脚本调用。"""
    from app.database import async_session

    async with async_session() as db:
        return await prune_task_results(db, dry_run=dry_run)
