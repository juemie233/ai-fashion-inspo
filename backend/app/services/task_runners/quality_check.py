"""质量审核任务：逐张调用轻量 AI 审核并维护进度。

本模块包含「质量审核」（quality_check）任务的创建与执行逻辑，
由 worker 进程（app/worker.py）通过 TASK_HANDLERS 分发表调度。
"""

import asyncio
import logging
import time

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
    # 与 f2 导入共用同一份实现（原先各写一遍）：这里保留同名别名，
    # `monkeypatch.setattr(quality_check, "_current_status", fake)` 依然生效。
    current_status as _current_status,
    utcnow,
)
from app.services.vector import store as vector_store

logger = logging.getLogger(__name__)

"""失败率告警阈值：超过该比例即写入 result.warning（全部失败另有任务级异常兜底）。"""
QUALITY_FAIL_WARN_RATIO = 0.2


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
    stats: dict | None = None,
) -> tuple[str, str, str, bool]:
    """审核单张图片（独立数据库会话），返回 (素材 ID, 状态, 原因, 是否疑似 AI)。

    状态为 approved/rejected/pending（审核失败保持 pending）。

    参数:
        force: 为 True 时覆盖写入已审查素材（随机复审场景）
        prefilter_vector: 预取的图像向量，供负样本初筛器复用（避免逐条全表扫描）
    """
    async with sem:
        async with async_session() as db:
            # 记录单条耗时：供 /ai/quality-stats 的「实测平均耗时」与前端 ETA 使用
            started = time.monotonic()
            status, reason, ai_generated = await check_image_quality(
                db,
                inspiration_id,
                file_path,
                force=force,
                prefilter_vector=prefilter_vector,
                stats=stats,
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            # 写入质量审核日志（失败时记录原因，供前端排查）
            log_entry = AIAnalysisLog(
                inspiration_id=inspiration_id,
                model_name=settings.ollama_vision_model,
                log_type="quality_check",
                model_version=settings.ollama_vision_model,
                processing_time_ms=elapsed_ms,
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
                    # 复盘三件套：模型原始输出、prompt 指纹、模型名（历史数据为空）
                    raw_output=stats.get("raw_output") if stats else None,
                    prompt_hash=stats.get("prompt_hash") if stats else None,
                    model_name=stats.get("model_name") if stats else None,
                    confidence=stats.get("prefilter_score") if stats else None,
                )
            )
            await db.commit()
            return inspiration_id, status, reason, ai_generated


def _audit_path(media_type: str, file_path: str, thumbnail_path: str | None) -> str:
    """返回送审用的文件路径。

    视频审核用首帧缩略图（AI 视觉模型只接受图片）：上传时 ffmpeg 已抽好首帧，
    审核时直接用，避免逐条再跑 ffmpeg；没有缩略图才退回原文件（会读图失败 → 保持
    pending 并在原因里写明，不静默漏审）。图片始终用原文件，保证复审能看清细节。

    Args:
        media_type: 素材类型（image / video）
        file_path: 原始文件相对路径
        thumbnail_path: 缩略图相对路径（视频首帧）

    Returns:
        实际传给 AI 审核的文件相对路径
    """
    if media_type == "video" and thumbnail_path:
        return thumbnail_path
    return file_path


async def execute_quality_check(db: AsyncSession, task: TaskQueue) -> None:
    """执行质量审核任务：逐张调用轻量审核并维护进度（由 worker 调用）。

    质量审核是「尽力而为」的：单张失败保持 pending，不触发整个任务重试。
    但整批全部失败时（如 Ollama 未启动、请求被拒），任务不能冒充「完成」——
    抛出任务级异常交由 worker 处理：可恢复错误自动重试，永久错误标记失败，
    避免出现「显示完成 N/N 实际一张都没审」的假成功。数据库层异常由 worker 兜底。

    中断语义：每批（并发数张）检查一次任务状态，被外部置为 cancelled/paused 时
    停止并保留已判定的结果（不写 100% 进度、不做「全部失败」判定）。单任务上限
    已放宽到 5000 张（约 17 小时），没有中断能力就会变成不可撤销的长任务。
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
    stmt = select(
        Inspiration.id,
        Inspiration.file_path,
        Inspiration.media_type,
        Inspiration.thumbnail_path,
    ).where(
        Inspiration.id.in_(inspiration_ids),
        # 视频也参与审核（老 #1）：视觉模型只吃图片，视频用上传时 ffmpeg 抽的首帧缩略图。
        # 没有缩略图的视频退回原文件，会因读图失败保持 pending 并在原因里写明（不静默漏审）。
        Inspiration.media_type.in_(("image", "video")),
    )
    if not is_random:
        stmt = stmt.where(Inspiration.quality_status == "pending")
    result = await db.execute(stmt)
    rows = result.all()
    insp_map = {r[0]: _audit_path(r[2], r[1], r[3]) for r in rows}
    items = [(iid, insp_map[iid]) for iid in inspiration_ids if iid in insp_map]

    # 一次性批量预取全部图像向量供初筛器复用：逐条 get_vector 每次都会全表加载
    # LanceDB 表（O(N²)），批量读取只加载一次（与训练侧 get_vectors_batch 同理）
    vec_map: dict[str, list[float]] = {}
    if vector_store.is_lancedb_available():
        vec_map = await vector_store.get_vectors_batch("image", [iid for iid, _ in items])

    # 跳过明细（#7）：此前 skipped 是「保留字段」从未真正统计。这里用「请求数 - 实际审核数」
    # 拆成两类，用户能直接看出「为什么只审了 N 张」。
    existing_image_ids = (
        await db.execute(
            select(Inspiration.id).where(
                Inspiration.id.in_(inspiration_ids),
                # 与上面口径一致：图片 + 视频都算「可审核」
                Inspiration.media_type.in_(("image", "video")),
            )
        )
    ).scalars().all()
    # not_image 现语义为「既不是图片也不是视频」（如音频/文档），含义已在文档说明
    skipped_not_image = len(inspiration_ids) - len(existing_image_ids)
    skipped_not_pending = 0 if is_random else len(existing_image_ids) - len(items)
    skipped_detail = {
        "not_image": max(0, skipped_not_image),
        "not_pending": max(0, skipped_not_pending),
        "total": max(0, len(inspiration_ids) - len(items)),
    }

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
    prefilter_counts = {"hit": 0, "pass": 0, "skipped": 0, "disabled": 0, "precheck": 0}
    cancelled = False

    for start in range(0, len(items), concurrency):
        # 每批检查一次外部状态：上限放宽到 5000 张后单任务可跑十几小时，
        # 必须能在任务管理页暂停/取消（与 face_scan / enrich_blogger_profile 同款）
        if (await _current_status(db, task.id)) not in ("running", "pending"):
            cancelled = True
            break

        chunk = items[start:start + concurrency]
        # 每条一个出参字典，collect 后汇总初筛器结果（#10）
        per_item_stats = [{} for _ in chunk]
        # return_exceptions=True：单条素材的 DB/日志异常不能拖垮整批（几千条的任务里
        # 一条写失败就整体失败，代价过高）。异常在下面按「失败一条」计入。
        results = await asyncio.gather(
            *(
                _quality_check_one(
                    sem,
                    iid,
                    fp,
                    prefilter_vector=vec_map.get(iid),
                    force=is_random,
                    # 每条一个字典：初筛器结果由 check_image_quality 写入对应项
                    stats=per_item_stats[idx],
                )
                for idx, (iid, fp) in enumerate(chunk)
            ),
            return_exceptions=True,
        )
        for item_stats in per_item_stats:
            key = str(item_stats.get("prefilter", "skipped"))
            prefilter_counts[key] = prefilter_counts.get(key, 0) + 1
        for result in results:
            if isinstance(result, Exception):
                # 该条整体失败（如写日志时 DB 异常）：计入 failed，原因写进任务级报错
                failed += 1
                pending += 1
                if first_error is None:
                    first_error = f"审核异常: {str(result)[:100]}"
                continue
            _iid, status, _reason, _ai = result
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

    failed_ratio = round(failed / task.total, 3) if task.total else 0.0
    task.result = {
        "inspiration_ids": inspiration_ids,
        "skipped": payload.get("skipped", 0),
        "approved": approved,
        "rejected": rejected,
        "pending": pending,
        "failed": failed,
        # 失败率：全部失败已单独抛任务级异常；这里覆盖「大面积失败但没到 100%」的可见性
        "failed_ratio": failed_ratio,
        # 跳过明细：为什么请求 100 条却只审了 80 条
        "skipped_detail": skipped_detail,
        # 初筛器效果：直接判垃圾多少 / 通过多少 / 无向量跳过多少 / force 复审未启用
        "prefilter": prefilter_counts,
        "ai_generated": ai_generated,
        "cancelled": cancelled,
    }
    if failed_ratio >= QUALITY_FAIL_WARN_RATIO:
        task.result["warning"] = (
            f"本次审核失败 {failed}/{task.total}（{failed_ratio:.0%}），"
            f"请检查 Ollama 服务与模型状态；失败项已保持待审核，可稍后重新提交"
        )
    # 统计结果先落库：即使下面判定失败抛出任务级异常，失败详情也能在任务记录中查到
    await db.commit()

    if cancelled:
        # 被外部暂停/取消：保留部分结果与当前进度，并尊重外部状态
        # （worker 见 status != running 不会覆盖为 success/failed）
        task.status = await _current_status(db, task.id)
        task.updated_at = utcnow()
        await db.commit()
        logger.info(
            f"质量审核被中断（{task.status}）: #{task.id} 已处理 "
            f"{task.done}/{task.total}，已判定结果保留"
        )
        return

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
