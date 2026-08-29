"""批量分析任务：逐张调用 AI 标签分析并维护进度。

本模块包含「批量分析」（batch_analyze）与「多模型 × 多提示词组合分析」
（multi_analyze）任务的创建与执行逻辑，
由 worker 进程（app/worker.py）通过 TASK_HANDLERS 分发表调度。
"""

import asyncio
import hashlib
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

# 多模型 × 多提示词组合分析允许的最大组合数（防止一次任务产生过多分析）
MAX_MULTI_COMBINATIONS = 10


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
            if not error:
                # 日志未能读到失败原因（_write_analysis_log 内部吞异常时日志
                # 可能未写入）：给出可辨识的兜底文案，避免「可恢复错误被误判
                # 为永久错误」时连原因都查不到
                error = "分析失败（未能记录错误详情，可能日志写入异常）"
            return inspiration_id, False, error


async def _resolve_analysis_source(
    db: AsyncSession, inspiration_id: str, file_path: str, media_type: str
) -> str | None:
    """解析素材的分析源相对路径（相对 storage_root，供 analyze_image 读取）。

    图片素材 → 原文件相对路径；视频素材 → 第一关键帧相对路径
    （首帧未提取时现场懒提取，ffmpeg 耗时；失败返回 None）；其余类型 None。
    """
    if media_type == "image":
        return file_path
    if media_type == "video":
        from app.config import settings
        from app.services import video_service

        insp = await db.get(Inspiration, inspiration_id)
        if insp is None:
            return None
        frame = await video_service.ensure_first_frame(insp)
        if frame is None:
            return None
        return frame.relative_to(settings.storage_root).as_posix()
    return None


async def _load_pending_items(
    db: AsyncSession, inspiration_ids: list[str]
) -> tuple[list[tuple[str, str]], int, int]:
    """加载仍存在的图片/视频素材，并跳过已有成功分析日志的（崩溃恢复幂等）。

    视频素材解析第一关键帧为分析源；关键帧提取失败的素材计入 unavailable
    （不进入分析、不算失败——ffmpeg 系统性故障时任务不该整体重试）。

    返回 (待分析 (id, file_path) 列表, 已分析跳过数量, 关键帧不可用数量)。
    """
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.media_type).where(
            Inspiration.id.in_(inspiration_ids),
            Inspiration.media_type.in_(("image", "video")),
        )
    )
    rows = result.all()
    row_map = {r[0]: (r[1], r[2]) for r in rows}
    items: list[tuple[str, str]] = []
    unavailable = 0
    for iid in inspiration_ids:
        if iid not in row_map:
            continue
        fp, mt = row_map[iid]
        source = await _resolve_analysis_source(db, iid, fp, mt)
        if source is None:
            unavailable += 1
            logger.warning(f"素材分析源不可用（视频关键帧提取失败），跳过: {iid}")
            continue
        items.append((iid, source))

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
    return items, already_analyzed, unavailable


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

    # 加载待分析素材（执行期间可能被删除，仅保留仍存在的图片/视频素材）
    items, already_analyzed, unavailable = await _load_pending_items(db, inspiration_ids)

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
        "unavailable": unavailable,
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


# ============ 多模型 × 多提示词组合分析（multi_analyze） ============


def _prompt_version(prompt: str) -> str:
    """计算 Prompt 的内容版本（SHA-256 前 8 位），与分析日志写入口径一致。"""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]


async def create_multi_analyze_task(
    db: AsyncSession,
    inspiration_ids: list[str],
    combinations: list[dict],
    apply_tags: bool = False,
    skipped: int = 0,
) -> TaskQueue:
    """创建「多模型 × 多提示词组合分析」任务记录，返回任务对象。

    参数:
        db: 数据库会话
        inspiration_ids: 待分析的素材 ID 列表（已过滤非图片/不存在）
        combinations: 组合列表，每项 {"model": 模型名, "prompt": Prompt 文本或
            None（执行时按模型解析当前 Prompt）, "prompt_label": 展示用标签}
        apply_tags: 是否把标签合并到素材（组合分析默认 False：只写日志与快照）
        skipped: 被跳过的素材数量（不存在或非图片）

    返回:
        新建的任务记录
    """
    task = TaskQueue(
        type="multi_analyze",
        status="pending",
        progress=0,
        total=len(inspiration_ids) * len(combinations),
        done=0,
        result={
            "mode": "multi",
            "inspiration_ids": inspiration_ids,
            "apply_tags": apply_tags,
            "skipped": skipped,
            "combinations": combinations,
        },
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _load_items(
    db: AsyncSession, inspiration_ids: list[str]
) -> list[tuple[str, str]]:
    """加载仍存在的图片/视频素材，返回 (素材 ID, 分析源文件路径) 列表。

    与单模型批量分析不同：组合分析允许对已分析过的素材重复分析
    （对比不同模型/提示词正是核心诉求），因此不做「已分析跳过」；
    幂等恢复改为按「组合 × 素材」粒度判断（见 _load_done_ids）。
    视频素材解析第一关键帧为分析源（提取失败的素材跳过并记日志）。
    """
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.media_type).where(
            Inspiration.id.in_(inspiration_ids),
            Inspiration.media_type.in_(("image", "video")),
        )
    )
    rows = result.all()
    row_map = {r[0]: (r[1], r[2]) for r in rows}
    items: list[tuple[str, str]] = []
    for iid in inspiration_ids:
        if iid not in row_map:
            continue
        fp, mt = row_map[iid]
        source = await _resolve_analysis_source(db, iid, fp, mt)
        if source is None:
            logger.warning(f"组合分析素材分析源不可用（视频关键帧提取失败），跳过: {iid}")
            continue
        items.append((iid, source))
    return items


async def _load_done_ids(
    db: AsyncSession, inspiration_ids: list[str], model_name: str, prompt_version: str
) -> set[str]:
    """查询某组合下已有成功分析日志的素材 ID（崩溃重试幂等）。

    按 (素材, 模型名, Prompt 版本哈希) 三元组判定：任务重试时跳过
    已成功的组合项，避免重复产生分析日志。
    """
    done: set[str] = set()
    for chunk in _chunked(list(inspiration_ids)):
        result = await db.execute(
            select(AIAnalysisLog.inspiration_id)
            .where(
                analysis_log_filter(),
                AIAnalysisLog.inspiration_id.in_(chunk),
                AIAnalysisLog.model_name == model_name,
                AIAnalysisLog.prompt_version == prompt_version,
                (AIAnalysisLog.error.is_(None)) | (AIAnalysisLog.error == ""),
            )
            .distinct()
        )
        done.update(r[0] for r in result.all())
    return done


async def _last_analysis_error_for(
    db: AsyncSession, inspiration_id: str, model_name: str
) -> str | None:
    """读取指定素材 + 模型最近一次分析日志的错误信息（组合失败原因定位用）。"""
    result = await db.execute(
        select(AIAnalysisLog.error)
        .where(
            AIAnalysisLog.inspiration_id == inspiration_id,
            AIAnalysisLog.model_name == model_name,
        )
        .order_by(AIAnalysisLog.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _analyze_one_multi(
    sem: asyncio.Semaphore,
    inspiration_id: str,
    file_path: str,
    model_name: str,
    prompt: str | None,
    apply_tags: bool,
) -> tuple[str, str, bool, str | None]:
    """按组合分析单张图片（独立数据库会话），返回 (素材 ID, 模型名, 是否成功, 失败原因)。"""
    async with sem:
        async with async_session() as db:
            success = await analyze_image(
                db,
                inspiration_id,
                file_path,
                model_name=model_name,
                prompt=prompt,
                apply_tags=apply_tags,
            )
            if success:
                return inspiration_id, model_name, True, None
            error = await _last_analysis_error_for(db, inspiration_id, model_name)
            if not error:
                error = "分析失败（未能记录错误详情，可能日志写入异常）"
            return inspiration_id, model_name, False, error


async def execute_multi_analyze(db: AsyncSession, task: TaskQueue) -> None:
    """执行多模型 × 多提示词组合分析任务（由 worker 调用）。

    按组合顺序逐个执行：每个组合 × 每个素材产生独立的
    ``ai_analysis_log`` + ``ai_extracted_tags`` 快照记录；
    apply_tags=False 时不修改素材的正式标签。
    单个组合项失败跳过并记录，不影响其他组合继续执行。

    抛出:
        RecoverableTaskError: 全部组合项均因可恢复错误失败（如 Ollama 宕机）
        PermanentTaskError: 全部组合项均为永久错误（如图片损坏/模型未安装）
    """
    payload = task.result or {}
    inspiration_ids = payload.get("inspiration_ids") or []
    combinations = payload.get("combinations") or []
    apply_tags = bool(payload.get("apply_tags", False))

    if not inspiration_ids or not combinations:
        # 空任务：无素材或无组合，直接标记完成
        task.total = 0
        task.done = 0
        task.progress = 100
        task.error = None
        await db.commit()
        return

    # 加载仍存在的图片素材（执行期间可能被删除）
    items = await _load_items(db, inspiration_ids)

    task.total = len(items) * len(combinations)
    task.done = 0
    task.progress = 0
    task.error = None
    await db.commit()

    # 批内并发信号量：同一组合内多张图片并发分析，与单模型批量分析保持一致
    sem = asyncio.Semaphore(_ANALYZE_CONCURRENCY)

    success_count = 0
    failed_items: list[tuple[str, str, str | None]] = []  # (素材 ID, 模型名, 错误)
    recoverable_failed: list[str] = []
    completed = 0

    for combo in combinations:
        model_name = combo.get("model") or ""
        prompt = combo.get("prompt")  # None = 执行时按模型解析当前 Prompt
        prompt_label = combo.get("prompt_label") or "当前默认提示词"
        # Prompt 版本哈希：用于重试幂等判定（与日志 prompt_version 口径一致）
        prompt_version = _prompt_version(prompt) if prompt else None

        todo = items
        if prompt_version:
            done_ids = await _load_done_ids(
                db, [iid for iid, _ in items], model_name, prompt_version
            )
            todo = [(iid, fp) for iid, fp in items if iid not in done_ids]
            if len(done_ids):
                logger.info(
                    f"组合分析跳过已成功项: {model_name} × {prompt_label}，"
                    f"{len(done_ids)}/{len(items)} 个素材已有成功记录"
                )

        for start in range(0, len(todo), _ANALYZE_CONCURRENCY):
            chunk = todo[start:start + _ANALYZE_CONCURRENCY]
            results = await asyncio.gather(
                *(
                    _analyze_one_multi(sem, iid, fp, model_name, prompt, apply_tags)
                    for iid, fp in chunk
                )
            )
            for iid, combo_model, ok, err in results:
                if ok:
                    success_count += 1
                else:
                    failed_items.append((iid, combo_model, err))
                    if _is_recoverable_error(err or ""):
                        recoverable_failed.append(iid)

            completed += len(chunk)
            task.done = min(completed, task.total)
            task.progress = round(task.done / task.total * 100) if task.total else 100
            task.updated_at = utcnow()
            await db.commit()

        logger.info(
            f"组合分析进度: #{task.id} {task.progress}% "
            f"({task.done}/{task.total})，当前组合 {model_name} × {prompt_label}"
        )

    if task.total and success_count == 0:
        if recoverable_failed:
            sample = failed_items[0][2] or "未知错误"
            raise RecoverableTaskError(
                f"组合分析全部失败（{task.total} 项），存在疑似可恢复的系统性错误：{sample}"
            )
        sample = failed_items[0][2] or "未知错误"
        raise PermanentTaskError(
            f"组合分析全部失败（{task.total} 项），均为永久错误"
            f"（模型未安装/图片损坏/文件不存在等）：{sample}"
        )

    # 正常完成（部分组合项失败也视为任务成功，失败详情写入结果）
    task.result = {
        "mode": "multi",
        "inspiration_ids": inspiration_ids,
        "apply_tags": apply_tags,
        "combinations": combinations,
        "skipped": payload.get("skipped", 0),
        "success_count": success_count,
        "failed_count": len(failed_items),
        "failed": [
            {"inspiration_id": iid, "model_name": model, "error": err}
            for iid, model, err in failed_items
            if err
        ],
    }
    task.done = task.total
    task.progress = 100
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"组合分析任务执行完毕: #{task.id} 成功 {success_count}，失败 {len(failed_items)}"
    )
