"""批量分析任务：逐张调用 AI 标签分析并维护进度。

本模块包含「批量分析」（batch_analyze）任务的创建与执行逻辑，
由 worker 进程（app/worker.py）通过 TASK_HANDLERS 分发表调度。
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration, analysis_log_filter
from app.models.task import TaskQueue
from app.services.ai_service import analyze_image
from app.services.task_runners.common import (
    PermanentTaskError,
    RecoverableTaskError,
    _ANALYZE_CONCURRENCY,
    _chunked,
    _is_recoverable_error,
    utcnow,
)

logger = logging.getLogger(__name__)


async def create_batch_analyze_task(
    db: AsyncSession, inspiration_ids: list[str], skipped: int = 0
) -> TaskQueue:
    """创建「批量分析」任务记录，返回任务对象（供 API 创建任务后返回 task_id）。

    参数:
        db: 数据库会话
        inspiration_ids: 待分析的素材 ID 列表（已过滤非图片/不存在）
        skipped: 被跳过的素材数量（不存在或非图片）

    返回:
        新建的任务记录
    """
    task = TaskQueue(
        type="batch_analyze",
        status="pending",
        progress=0,
        total=len(inspiration_ids),
        done=0,
        result={"inspiration_ids": inspiration_ids, "skipped": skipped},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _last_analysis_error(db: AsyncSession, inspiration_id: str) -> str | None:
    """读取指定素材最近一次分析日志的错误信息。

    analyze_image 失败时会在独立事务中写入 AIAnalysisLog（含 error），
    此处读取最新一条用于错误分类。
    """
    result = await db.execute(
        select(AIAnalysisLog.error)
        .where(AIAnalysisLog.inspiration_id == inspiration_id)
        .order_by(AIAnalysisLog.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _analyze_one(
    sem: asyncio.Semaphore, inspiration_id: str, file_path: str
) -> tuple[str, bool, str | None]:
    """分析单张图片（使用独立数据库会话），返回 (素材 ID, 是否成功, 失败原因)。

    参数:
        sem: 批内并发信号量
        inspiration_id: 素材 ID
        file_path: 图片相对路径
    """
    async with sem:
        async with async_session() as db:
            success = await analyze_image(db, inspiration_id, file_path)
            if success:
                return inspiration_id, True, None
            error = await _last_analysis_error(db, inspiration_id)
            return inspiration_id, False, error


async def _load_pending_items(
    db: AsyncSession, inspiration_ids: list[str]
) -> tuple[list[tuple[str, str]], int]:
    """加载仍存在的图片素材，并跳过已有成功分析日志的（崩溃恢复幂等）。

    返回 (待分析 (id, file_path) 列表, 已跳过数量)。
    """
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path).where(
            Inspiration.id.in_(inspiration_ids),
            Inspiration.media_type == "image",
        )
    )
    rows = result.all()
    insp_map = {r[0]: r[1] for r in rows}
    items = [(iid, insp_map[iid]) for iid in inspiration_ids if iid in insp_map]

    # 跳过「已有成功标签分析日志」的素材，避免重跑时对前 N 张再次调用 Ollama
    analyzed_ids: set[str] = set()
    candidate_ids = [iid for iid, _ in items]
    for chunk in _chunked(candidate_ids):
        analyzed_result = await db.execute(
            select(AIAnalysisLog.inspiration_id)
            .where(
                analysis_log_filter(),
                AIAnalysisLog.inspiration_id.in_(chunk),
                (AIAnalysisLog.error.is_(None)) | (AIAnalysisLog.error == ""),
            )
            .distinct()
        )
        analyzed_ids.update(r[0] for r in analyzed_result.all())
    already_analyzed = sum(1 for iid in candidate_ids if iid in analyzed_ids)
    items = [(iid, fp) for iid, fp in items if iid not in analyzed_ids]
    return items, already_analyzed


def _raise_if_all_failed(
    task_total: int,
    success_count: int,
    failed_items: list[tuple[str, str | None]],
    recoverable_failed: list[str],
) -> None:
    """整个批次全部失败时按错误类型决定是否自动重试。

    采用宽松判定：只要存在可恢复错误的失败样本，就按可恢复处理（Ollama 瞬时故障时
    不同图片可能报不同错误，若要求「全部可恢复」才会被误判为永久失败、放弃重试）。
    """
    if task_total == 0 or success_count > 0:
        return
    if recoverable_failed:
        sample = failed_items[0][1] or "未知错误"
        raise RecoverableTaskError(
            f"批量分析全部失败（{task_total} 张），存在疑似可恢复的系统性错误：{sample}"
        )
    sample = failed_items[0][1] or "未知错误"
    raise PermanentTaskError(
        f"批量分析全部失败（{task_total} 张），均为永久错误（图片损坏/文件不存在等）：{sample}"
    )


async def execute_batch_analyze(db: AsyncSession, task: TaskQueue) -> None:
    """执行批量分析任务：逐张调用 AI 分析并维护任务进度（由 worker 调用）。

    参数:
        db: 任务生命周期会话（用于更新任务进度与状态）
        task: 任务记录

    抛出:
        RecoverableTaskError: 整个批次全部因可恢复错误失败（如 Ollama 宕机），
            由 worker 安排自动重试
        PermanentTaskError: 整个批次全部因永久错误失败（如图片损坏），直接失败不重试
    """
    payload = task.result or {}
    inspiration_ids = payload.get("inspiration_ids") or []
    if not inspiration_ids:
        # 空任务：无素材可分析，直接标记完成
        task.total = 0
        task.done = 0
        task.progress = 100
        task.error = None
        await db.commit()
        return

    # 加载待分析素材（执行期间可能被删除，仅保留仍存在的图片素材）
    items, already_analyzed = await _load_pending_items(db, inspiration_ids)

    task.total = len(items)
    task.done = 0
    task.progress = 0
    task.error = None
    await db.commit()

    # 批内并发信号量：任务内部对多张图片的分析保持原有并发（不改成单张串行）
    sem = asyncio.Semaphore(_ANALYZE_CONCURRENCY)

    success_count = 0
    failed_items: list[tuple[str, str | None]] = []
    recoverable_failed: list[str] = []

    for start in range(0, len(items), _ANALYZE_CONCURRENCY):
        chunk = items[start:start + _ANALYZE_CONCURRENCY]
        results = await asyncio.gather(
            *(_analyze_one(sem, iid, fp) for iid, fp in chunk)
        )
        for iid, ok, err in results:
            if ok:
                success_count += 1
            else:
                failed_items.append((iid, err))
                if _is_recoverable_error(err or ""):
                    recoverable_failed.append(iid)

        task.done = min(start + len(chunk), task.total)
        task.progress = round(task.done / task.total * 100) if task.total else 100
        task.updated_at = utcnow()
        await db.commit()
        logger.info(
            f"批量分析进度: #{task.id} {task.progress}% ({task.done}/{task.total})"
        )

    _raise_if_all_failed(task.total, success_count, failed_items, recoverable_failed)

    # 正常完成（部分图片失败也视为任务成功，失败详情写入结果）
    task.result = {
        "inspiration_ids": inspiration_ids,
        "skipped": payload.get("skipped", 0),
        "already_analyzed": already_analyzed,
        "success_count": success_count,
        "failed_count": len(failed_items),
        "failed_ids": [iid for iid, _ in failed_items],
        "failed_errors": {iid: err for iid, err in failed_items if err},
    }
    task.done = task.total
    task.progress = 100
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"批量分析任务执行完毕: #{task.id} 成功 {success_count}，失败 {len(failed_items)}"
    )
