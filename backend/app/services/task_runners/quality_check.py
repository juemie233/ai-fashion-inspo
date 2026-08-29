"""质量审核任务：逐张调用轻量 AI 审核并维护进度。

本模块包含「质量审核」（quality_check）任务的创建与执行逻辑，
由 worker 进程（app/worker.py）通过 TASK_HANDLERS 分发表调度。
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.inspiration import AIAnalysisLog, AIQualityReview, Inspiration
from app.models.task import TaskQueue
from app.services.ai_service import check_image_quality
from app.services.task_runners.common import (
    PermanentTaskError,
    RecoverableTaskError,
    _analyze_concurrency,
    _broadcast_task_event,
    _is_recoverable_error,
    utcnow,
)
from app.services.vector import store as vector_store

logger = logging.getLogger(__name__)


async def create_quality_check_task(
    db: AsyncSession, inspiration_ids: list[str], skipped: int = 0, random: bool = False
) -> TaskQueue:
    """创建「质量审核」任务记录，返回任务对象（供 API 创建任务后返回 task_id）。

    参数:
        db: 数据库会话
        inspiration_ids: 待审核的素材 ID 列表（API 层已按条件过滤）
        skipped: 被跳过的素材数量（保留字段）
        random: 是否为随机复审——True 时执行阶段不限制 pending，且会覆盖已审查素材的判定

    返回:
        新建的任务记录
    """
    task = TaskQueue(
        type="quality_check",
        status="pending",
        progress=0,
        total=len(inspiration_ids),
        done=0,
        result={"inspiration_ids": inspiration_ids, "skipped": skipped, "random": random},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _quality_check_one(
    sem: asyncio.Semaphore,
    inspiration_id: str,
    file_path: str,
    prefilter_vector: list[float] | None = None,
    force: bool = False,
) -> tuple[str, str, str, bool]:
    """审核单张图片（独立数据库会话），返回 (素材 ID, 状态, 原因, 是否疑似 AI)。

    状态为 approved/rejected/pending（审核失败保持 pending）。

    参数:
        force: 为 True 时覆盖写入已审查素材（随机复审场景）
        prefilter_vector: 预取的图像向量，供负样本初筛器复用（避免逐条全表扫描）
    """
    async with sem:
        async with async_session() as db:
            status, reason, ai_generated = await check_image_quality(
                db, inspiration_id, file_path, force=force, prefilter_vector=prefilter_vector
            )
            # 写入质量审核日志（失败时记录原因，供前端排查）
            log_entry = AIAnalysisLog(
                inspiration_id=inspiration_id,
                model_name=settings.ollama_vision_model,
                log_type="quality_check",
                model_version=settings.ollama_vision_model,
                error=reason if status == "pending" else None,
            )
            db.add(log_entry)
            await db.flush()
            # 结构化审核结果：按日志记录单次判定，与素材当前状态（quality_status）解耦
            db.add(
                AIQualityReview(
                    log_id=log_entry.id,
                    result=status,
                    reason=reason,
                    reviewed_at=utcnow(),
                )
            )
            await db.commit()
            return inspiration_id, status, reason, ai_generated


async def execute_quality_check(db: AsyncSession, task: TaskQueue) -> None:
    """执行质量审核任务：逐张调用轻量审核并维护进度（由 worker 调用）。

    质量审核是「尽力而为」的：单张失败保持 pending，不触发整个任务重试。
    但整批全部失败时（如 Ollama 未启动、请求被拒），任务不能冒充「完成」——
    抛出任务级异常交由 worker 处理：可恢复错误自动重试，永久错误标记失败，
    避免出现「显示完成 N/N 实际一张都没审」的假成功。数据库层异常由 worker 兜底。
    """
    payload = task.result or {}
    inspiration_ids = payload.get("inspiration_ids") or []
    is_random = payload.get("random", False)
    if not inspiration_ids:
        task.total = 0
        task.done = 0
        task.progress = 100
        await db.commit()
        return

    # 加载待审核的图片素材（执行期间可能已被人工翻案）：
    # 随机复审不限制状态，覆盖重审已审查素材；普通审核仅保留仍 pending 的
    stmt = select(Inspiration.id, Inspiration.file_path).where(
        Inspiration.id.in_(inspiration_ids),
        Inspiration.media_type == "image",
    )
    if not is_random:
        stmt = stmt.where(Inspiration.quality_status == "pending")
    result = await db.execute(stmt)
    rows = result.all()
    insp_map = {r[0]: r[1] for r in rows}
    items = [(iid, insp_map[iid]) for iid in inspiration_ids if iid in insp_map]

    # 一次性批量预取全部图像向量供初筛器复用：逐条 get_vector 每次都会全表加载
    # LanceDB 表（O(N²)），批量读取只加载一次（与训练侧 get_vectors_batch 同理）
    vec_map: dict[str, list[float]] = {}
    if vector_store.is_lancedb_available():
        vec_map = await vector_store.get_vectors_batch("image", [iid for iid, _ in items])

    task.total = len(items)
    task.done = 0
    task.progress = 0
    task.error = None
    await db.commit()

    concurrency = _analyze_concurrency()
    sem = asyncio.Semaphore(concurrency)
    approved = 0
    rejected = 0
    pending = 0
    failed = 0  # 审核失败保持 pending 的张数（reason 非空，与「无法判定」同计数但单列）
    ai_generated = 0
    first_error: str | None = None  # 第一条失败原因，供任务级报错

    for start in range(0, len(items), concurrency):
        chunk = items[start:start + concurrency]
        results = await asyncio.gather(
            *(
                _quality_check_one(
                    sem, iid, fp, prefilter_vector=vec_map.get(iid), force=is_random
                )
                for iid, fp in chunk
            )
        )
        for _iid, status, _reason, _ai in results:
            if status == "approved":
                approved += 1
            elif status == "rejected":
                rejected += 1
            else:
                pending += 1
                if _reason:
                    failed += 1
                    if first_error is None:
                        first_error = _reason
            if _ai:
                ai_generated += 1

        task.done = min(start + len(chunk), task.total)
        task.progress = round(task.done / task.total * 100) if task.total else 100
        task.updated_at = utcnow()
        await db.commit()
        await _broadcast_task_event(task, "progress")
        logger.info(
            f"质量审核进度: #{task.id} {task.progress}% ({task.done}/{task.total})"
        )

    task.result = {
        "inspiration_ids": inspiration_ids,
        "skipped": payload.get("skipped", 0),
        "approved": approved,
        "rejected": rejected,
        "pending": pending,
        "failed": failed,
        "ai_generated": ai_generated,
    }
    # 统计结果先落库：即使下面判定失败抛出任务级异常，失败详情也能在任务记录中查到
    await db.commit()

    # 整批全部审核失败：不能标记成功。抛出任务级异常交由 worker 处理——
    # 可恢复错误（Ollama 未启动/超时/服务异常）自动重试，永久错误（请求被拒等）标记失败。
    # 判定在写「完成态」之前：异常抛出时任务仍为 running，由 worker 统一改写状态，
    # 避免「先 commit 完成态再抛异常」在进程崩溃时残留假完成。
    if task.total and failed == task.total:
        detail = f"质量审核全部失败（{failed}/{task.total}）"
        if first_error:
            detail += f"：{first_error}"
        if _is_recoverable_error(first_error or ""):
            raise RecoverableTaskError(detail)
        raise PermanentTaskError(detail)

    task.done = task.total
    task.progress = 100
    task.updated_at = utcnow()
    await db.commit()

    logger.info(
        f"质量审核任务完成: #{task.id} 通过 {approved}，拒绝 {rejected}，"
        f"未判定 {pending}，失败 {failed}"
    )
