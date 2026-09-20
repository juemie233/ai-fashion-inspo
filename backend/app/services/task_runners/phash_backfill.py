"""感知哈希缓存补齐任务（phash_backfill）：后台把全库缺失的 phash 一次补齐。

为什么需要它（而不是让扫描接口自己补完）：大库全量补算要数十分钟（实测 11,568 张
f2 抖音原图、单张 142 ms ≈ 27 分钟），压在一次 HTTP 请求里必然被前端/反代判超时。
扫描接口因此只做「限时补算」（`BACKFILL_TIME_BUDGET_SECONDS`），把剩下的一次性补齐
交给 worker：任务队列没有请求超时约束，进度与取消都走既有任务中心那套 UX。

补齐后近似重复扫描才是「纯内存分组」——这是扫描页那句「万级素材秒级返回」的前提。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration, NOT_DELETED
from app.models.task import TaskQueue
from app.services.near_duplicate_service import (
    _count_missing_phash,
    backfill_phash_cache,
)
from app.services.task_runners.common import utcnow

logger = logging.getLogger(__name__)

"""任务结果里记录的「算不出哈希」素材数上限（只记数量与样例，避免结果行过大）。"""
_UNHASHABLE_SAMPLE_MAX = 20


async def create_phash_backfill_task(db: AsyncSession) -> dict:
    """创建哈希缓存补齐任务（幂等）。

    幂等约定与其它后台补算一致：
    - 缓存已完整 → ``{"task_id": None, "total": 0, "reused": False}``；
    - 已有 pending/running 的同类任务 → 不重复入队（避免两个 worker 同时算同一批、
      白烧 CPU），返回该任务的 id 与待补数，``reused=True``。

    Returns:
        ``{"task_id": 任务 id 或 None, "total": 待补张数, "reused": 是否复用进行中任务}``
    """
    missing = await _count_missing_phash(db)
    if missing == 0:
        return {"task_id": None, "total": 0, "reused": False}

    running = (
        await db.execute(
            select(TaskQueue.id, TaskQueue.total).where(
                TaskQueue.type == "phash_backfill",
                TaskQueue.status.in_(("pending", "running")),
            )
        )
    ).first()
    if running is not None:
        return {"task_id": int(running[0]), "total": int(running[1] or missing), "reused": True}

    task = TaskQueue(
        type="phash_backfill",
        status="pending",
        progress=0,
        total=missing,
        done=0,
        result={"missing": missing},
        max_retries=1,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    logger.info(f"已创建哈希缓存补齐任务: #{task.id}，待补 {missing} 张")
    return {"task_id": task.id, "total": missing, "reused": False}


async def execute_phash_backfill(db: AsyncSession, task: TaskQueue) -> None:
    """执行哈希缓存补齐：分批补算 → 每批写回进度 → 支持取消。

    进度语义：``done/total`` = 已算出的哈希数 / 任务创建时的缺失数。任务运行期间
    新增的素材（或又被清空 phash 的素材）不在本次 total 内——本轮结束后若仍有缺口，
    再创建一次即可（`create_phash_backfill_task` 会读到最新缺口）。
    """
    missing_at_start = int((task.result or {}).get("missing") or task.total or 0)
    total = max(1, missing_at_start)

    computed_total = 0
    cancelled = False

    async def _on_batch(computed: int, remaining: int) -> None:
        """每批回调：写进度并检查取消（不与任务表争同一个会话的写事务）。"""
        nonlocal computed_total
        computed_total = computed
        task.done = min(computed, missing_at_start)
        task.progress = min(99, round(task.done / total * 100))
        task.result = {
            **(task.result or {}),
            "missing": remaining,
            "computed": computed,
        }
        task.updated_at = utcnow()
        await db.commit()

    # 后台任务没有请求超时约束，这里给足预算：单批 300 张 × 142 ms ≈ 43 秒，
    # 远小于 worker 的 90 秒心跳租约（心跳由 worker 每 10 秒刷新，故长跑安全）
    budget_seconds = max(60.0, missing_at_start * 0.2)
    result = await backfill_phash_cache(
        db, budget_seconds=budget_seconds, on_batch=_on_batch
    )

    if await _is_cancelled(db, task):
        cancelled = True

    missing_now = await _count_missing_phash(db)
    unhashable_sample = await _unhashable_sample(db, result["unhashable"])

    task.result = {
        **(task.result or {}),
        "computed": result["computed"],
        "missing": missing_now,
        "complete": missing_now == 0,
        "unhashable": result["unhashable"],
        "unhashable_sample": unhashable_sample,
    }
    task.done = min(result["computed"], missing_at_start)
    task.progress = 100 if missing_now == 0 else min(99, round(task.done / total * 100))
    if cancelled:
        task.status = "cancelled"  # worker 见 status != running 不会覆盖为 success
    task.updated_at = utcnow()
    await db.commit()

    if missing_now == 0:
        logger.info(f"哈希缓存补齐任务结束: #{task.id}，共补 {result['computed']} 张，缓存已完整")
    elif result["computed"] == 0 and result["unhashable"] >= missing_now:
        # 剩下的行都算不出哈希（文件缺失/损坏）：再跑也没有进展，如实记录而不是假装成功
        logger.warning(
            f"哈希缓存补齐任务结束: #{task.id}，本轮未补上任何哈希，"
            f"仍有 {missing_now} 张无法计算（文件缺失或格式不支持）"
        )
    else:
        logger.info(
            f"哈希缓存补齐任务结束: #{task.id}，补 {result['computed']} 张，"
            f"仍缺 {missing_now} 张（其中算不出哈希 {result['unhashable']} 张）"
        )


async def _is_cancelled(db: AsyncSession, task: TaskQueue) -> bool:
    """检查任务是否被外部置为 cancelled。"""
    status = (
        await db.execute(select(TaskQueue.status).where(TaskQueue.id == task.id))
    ).scalar()
    return (status or "running") == "cancelled"


async def _unhashable_sample(db: AsyncSession, unhashable_count: int) -> list[dict]:
    """列出算不出哈希的素材样例（供结果面板展示与排查）；没有则返回空列表。"""
    if unhashable_count <= 0:
        return []
    rows = (
        await db.execute(
            select(Inspiration.id, Inspiration.file_path)
            .where(
                NOT_DELETED,
                Inspiration.media_type == "image",
                Inspiration.phash.is_(None),
            )
            .limit(_UNHASHABLE_SAMPLE_MAX)
        )
    ).all()
    return [{"id": rid, "file_path": fpath} for rid, fpath in rows]
