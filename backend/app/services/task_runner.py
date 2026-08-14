"""任务队列执行逻辑：供独立 worker 进程（app/worker.py）调用。

本模块只负责「执行」：读取 task_queue 记录、逐张调用现有 AI 分析服务、
更新进度与结果，不包含任何 HTTP/API 层逻辑。
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration, analysis_log_filter
from app.models.scraper import ScraperSeenURL
from app.models.tag import InspirationTag
from app.models.task import TaskQueue
from app.services.ai_service import analyze_image, check_image_quality
from app.utils.file_hash import build_hash_map

logger = logging.getLogger(__name__)

# 批内并发度：批量分析任务量大，固定为 1（逐张串行）对单卡最稳。
# 注意：worker 进程与 API 进程（routers/ai_shared.py 的 _analysis_semaphore=2）各自持有
# 独立的进程内信号量，互不感知、无法跨进程共享。串行后最坏情况为 1（worker）+ 2（API）= 3 路，
# 相比原 2+2=4 路进一步降低单卡显存溢出风险。如需整体调整，请同步修改两处。
_ANALYZE_CONCURRENCY = 1


class RecoverableTaskError(Exception):
    """可恢复错误：任务应自动重试（模型超时 / Ollama 连接失败 / SQLite 数据库锁）。"""


class PermanentTaskError(Exception):
    """永久错误：任务不应重试（图片损坏 / 文件不存在等）。"""


def _utcnow() -> datetime:
    """返回当前 UTC 时间（naive datetime）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_recoverable_error(message: str) -> bool:
    """判断错误消息是否属于「可恢复错误」。

    可恢复错误包括：模型响应超时、无法连接 Ollama、Ollama 服务不可用、
    SQLite 数据库被锁（database is locked）。其余错误（图片损坏、文件不存在等）
    视为永久错误，不重试。

    参数:
        message: 错误消息

    返回:
        True 表示可恢复，False 表示永久错误
    """
    if not message:
        return False
    keywords = (
        "超时",
        "无法连接 Ollama",
        "无法连接",
        "Ollama 服务",
        "Ollama 返回",
        "返回空内容",
        "返回格式异常",
        "缺少 message",
        "暂时不可用",
        "请求过于频繁",
        "database is locked",
        "locked",
    )
    return any(k in message for k in keywords)


def _retry_delay(retry_count: int) -> int:
    """指数退避延迟（秒）：第 1 次约 30s，第 2 次约 2min。

    参数:
        retry_count: 当前已重试次数（1 表示第 1 次重试）
    """
    return min(120, 30 * (4 ** (retry_count - 1)))


def _chunked(values: list, size: int = 500) -> list[list]:
    """将列表按指定大小分片，避免 SQLite IN(...) 超过变量上限（SQLite 默认约 999 个变量）。

    参数:
        values: 待分片的列表
        size: 每片大小（默认 500，留足余量）

    返回:
        分片后的列表
    """
    return [values[i:i + size] for i in range(0, len(values), size)]


async def _delete_inspiration_vectors(inspiration_ids: list[str]) -> None:
    """批量删除素材在 LanceDB 中的向量（素材物理删除后调用）。

    删除函数由 app.services.vector_store 提供（并行 agent 实现），LanceDB 未安装时
    静默返回；此处额外兜底捕获异常并降级为警告，避免向量清理失败影响任务结果。
    """
    if not inspiration_ids:
        return
    try:
        from app.services.vector_store import delete_inspiration_vectors_batch
        await delete_inspiration_vectors_batch(inspiration_ids)
    except Exception as e:
        logger.warning(f"批量删除素材向量失败（忽略，不影响任务结果）: {e}")


async def _schedule_retry(db: AsyncSession, task: TaskQueue, error_msg: str) -> None:
    """安排任务自动重试（指数退避）；超过最大重试次数则标记失败。

    参数:
        db: 任务生命周期会话
        task: 任务记录
        error_msg: 本次失败原因
    """
    task.retry_count += 1
    task.error = error_msg
    # 重试前重置进度，避免重试任务显示 100% 却处于 pending
    task.progress = 0
    task.done = 0
    if task.retry_count <= task.max_retries:
        delay = _retry_delay(task.retry_count)
        task.status = "pending"
        task.next_retry_at = _utcnow() + timedelta(seconds=delay)
        logger.warning(
            f"任务将自动重试 #{task.id}，第 {task.retry_count}/{task.max_retries} 次，{delay} 秒后"
        )
    else:
        task.status = "failed"
        task.next_retry_at = None
        logger.error(f"任务已超过最大重试次数，标记失败: #{task.id}")


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
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path).where(
            Inspiration.id.in_(inspiration_ids),
            Inspiration.media_type == "image",
        )
    )
    rows = result.all()
    insp_map = {r[0]: r[1] for r in rows}
    items = [(iid, insp_map[iid]) for iid in inspiration_ids if iid in insp_map]

    # 崩溃恢复幂等：跳过「已有成功标签分析日志」的素材，避免重跑时对前 N 张再次调用 Ollama
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
        task.updated_at = _utcnow()
        await db.commit()
        logger.info(
            f"批量分析进度: #{task.id} {task.progress}% ({task.done}/{task.total})"
        )

    # 整个批次全部失败：按错误类型决定是否自动重试
    # 采用宽松判定：只要存在可恢复错误的失败样本，就按可恢复处理（Ollama 瞬时故障时
    # 不同图片可能报不同错误，若要求「全部可恢复」才会被误判为永久失败、放弃重试）
    if task.total > 0 and success_count == 0:
        if recoverable_failed:
            sample = failed_items[0][1] or "未知错误"
            raise RecoverableTaskError(
                f"批量分析全部失败（{task.total} 张），存在疑似可恢复的系统性错误：{sample}"
            )
        sample = failed_items[0][1] or "未知错误"
        raise PermanentTaskError(
            f"批量分析全部失败（{task.total} 张），均为永久错误（图片损坏/文件不存在等）：{sample}"
        )

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
    task.updated_at = _utcnow()
    await db.commit()
    logger.info(
        f"批量分析任务执行完毕: #{task.id} 成功 {success_count}，失败 {len(failed_items)}"
    )


async def create_quality_check_task(
    db: AsyncSession, inspiration_ids: list[str], skipped: int = 0
) -> TaskQueue:
    """创建「质量审核」任务记录，返回任务对象（供 API 创建任务后返回 task_id）。

    参数:
        db: 数据库会话
        inspiration_ids: 待审核的素材 ID 列表（API 层已过滤为 pending 图片）
        skipped: 被跳过的素材数量（保留字段）

    返回:
        新建的任务记录
    """
    task = TaskQueue(
        type="quality_check",
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


async def _quality_check_one(
    sem: asyncio.Semaphore, inspiration_id: str, file_path: str
) -> tuple[str, str, str]:
    """审核单张图片（独立数据库会话），返回 (素材 ID, 状态, 原因)。

    状态为 approved/rejected/pending（审核失败保持 pending）。
    """
    async with sem:
        async with async_session() as db:
            status, reason = await check_image_quality(db, inspiration_id, file_path)
            # 写入质量审核日志（失败时记录原因，供前端排查）
            db.add(AIAnalysisLog(
                inspiration_id=inspiration_id,
                model_name=settings.ollama_vision_model,
                log_type="quality_check",
                error=reason if status == "pending" else None,
            ))
            await db.commit()
            return inspiration_id, status, reason


async def execute_quality_check(db: AsyncSession, task: TaskQueue) -> None:
    """执行质量审核任务：逐张调用轻量审核并维护进度（由 worker 调用）。

    质量审核是「尽力而为」的：单张失败保持 pending，不触发整个任务重试，
    因此本函数不主动抛出 Recoverable/Permanent 错误（数据库层异常由 worker 兜底）。
    """
    payload = task.result or {}
    inspiration_ids = payload.get("inspiration_ids") or []
    if not inspiration_ids:
        task.total = 0
        task.done = 0
        task.progress = 100
        await db.commit()
        return

    # 加载仍待审核的图片素材（执行期间可能已被人工翻案，仅保留仍 pending 的）
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path).where(
            Inspiration.id.in_(inspiration_ids),
            Inspiration.media_type == "image",
            Inspiration.quality_status == "pending",
        )
    )
    rows = result.all()
    insp_map = {r[0]: r[1] for r in rows}
    items = [(iid, insp_map[iid]) for iid in inspiration_ids if iid in insp_map]

    task.total = len(items)
    task.done = 0
    task.progress = 0
    task.error = None
    await db.commit()

    sem = asyncio.Semaphore(_ANALYZE_CONCURRENCY)
    approved = 0
    rejected = 0
    pending = 0

    for start in range(0, len(items), _ANALYZE_CONCURRENCY):
        chunk = items[start:start + _ANALYZE_CONCURRENCY]
        results = await asyncio.gather(
            *(_quality_check_one(sem, iid, fp) for iid, fp in chunk)
        )
        for _iid, status, _reason in results:
            if status == "approved":
                approved += 1
            elif status == "rejected":
                rejected += 1
            else:
                pending += 1

        task.done = min(start + len(chunk), task.total)
        task.progress = round(task.done / task.total * 100) if task.total else 100
        task.updated_at = _utcnow()
        await db.commit()
        logger.info(
            f"质量审核进度: #{task.id} {task.progress}% ({task.done}/{task.total})"
        )

    task.result = {
        "inspiration_ids": inspiration_ids,
        "skipped": payload.get("skipped", 0),
        "approved": approved,
        "rejected": rejected,
        "pending": pending,
    }
    task.done = task.total
    task.progress = 100
    task.updated_at = _utcnow()
    await db.commit()
    logger.info(
        f"质量审核任务完成: #{task.id} 通过 {approved}，拒绝 {rejected}，未判定 {pending}"
    )


async def create_batch_delete_task(
    db: AsyncSession, inspiration_ids: list[str], label: str = ""
) -> TaskQueue:
    """创建「批量删除」任务记录，返回任务对象（供 API 创建任务后返回 task_id）。

    参数:
        db: 数据库会话
        inspiration_ids: 待删除的素材 ID 列表（API 层已按条件解析）
        label: 删除类型标签（untagged / analysis_failed / ids），用于完成后提示

    返回:
        新建的任务记录
    """
    task = TaskQueue(
        type="batch_delete",
        status="pending",
        progress=0,
        total=len(inspiration_ids),
        done=0,
        result={"inspiration_ids": inspiration_ids, "label": label},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def execute_batch_delete(db: AsyncSession, task: TaskQueue) -> None:
    """执行批量删除任务：删文件、写墓碑、删数据库记录（由 worker 调用）。

    单个素材删除是确定性操作，文件缺失跳过即可，不抛可恢复错误。
    """
    payload = task.result or {}
    inspiration_ids = payload.get("inspiration_ids") or []
    if not inspiration_ids:
        task.total = 0
        task.done = 0
        task.progress = 100
        await db.commit()
        return

    # 查待删除素材的文件路径与来源 URL
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.thumbnail_path, Inspiration.source_url)
        .where(Inspiration.id.in_(inspiration_ids))
    )
    files_to_delete = result.all()

    storage_root = settings.storage_root

    # 写入墓碑表（防止被删除素材的 URL 被重新采集）
    urls_to_seal = [r[3] for r in files_to_delete if r[3]]
    if urls_to_seal:
        for url in urls_to_seal:
            await db.execute(
                sqlite_insert(ScraperSeenURL).values(source_url=url).prefix_with("OR IGNORE")
            )

    # 先提交数据库删除（级联删除关联 tags 与 analysis_logs），再删磁盘文件，
    # 降低「文件已删但 DB 未删」的不一致窗口
    deleted_ids = [r[0] for r in files_to_delete]
    await db.execute(
        Inspiration.__table__.delete().where(Inspiration.id.in_(deleted_ids))
    )
    await db.commit()

    # 删除 LanceDB 向量，避免孤儿向量（由 vector_store 提供，未安装时静默返回）
    await _delete_inspiration_vectors(deleted_ids)

    freed_bytes = 0
    for _fid, fpath, thumb, _surl in files_to_delete:
        for p in (fpath, thumb):
            if p:
                full = storage_root / p
                try:
                    if full.exists():
                        freed_bytes += full.stat().st_size
                        full.unlink()
                except Exception:
                    pass

    task.result = {
        "inspiration_ids": inspiration_ids,
        "label": payload.get("label", ""),
        "deleted_count": len(deleted_ids),
        "freed_bytes": freed_bytes,
    }
    task.done = task.total
    task.progress = 100
    task.updated_at = _utcnow()
    await db.commit()
    logger.info(f"批量删除任务完成: #{task.id} 删除 {len(deleted_ids)} 个素材")


async def create_deduplicate_task(db: AsyncSession) -> TaskQueue:
    """创建「智能去重」任务记录，返回任务对象。

    去重无需预加载 ID：由 worker 执行时全库扫描并计算 MD5，
    因此创建时 total 未知（设为 0，执行阶段再更新）。
    """
    task = TaskQueue(
        type="deduplicate",
        status="pending",
        progress=0,
        total=0,
        done=0,
        result={"inspiration_ids": []},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def execute_deduplicate(db: AsyncSession, task: TaskQueue) -> None:
    """执行智能去重任务：全库 MD5 扫描 + 评分保留 + 物理删除冗余副本（由 worker 调用）。

    评分规则与 admin 路由的旧版一致：有标签 +100、已收藏 +50、AI 分析成功 +30、
    有缩略图 +10、创建时间更早优先（平局时 ID 更小）。
    """
    storage_root = settings.storage_root
    task.error = None
    task.progress = 5
    await db.commit()

    # 阶段 1：全库扫描，计算 MD5 并分组
    # 同步逐块读文件算 MD5 会阻塞事件循环数分钟，放入线程池执行
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.thumbnail_path,
               Inspiration.is_favorite, Inspiration.created_at)
    )
    hash_map = await asyncio.to_thread(
        build_hash_map, result.all(), storage_root, include_meta=True
    )

    dup_groups = [(h, files) for h, files in hash_map.items() if len(files) > 1]
    if not dup_groups:
        task.result = {"groups_processed": 0, "files_deleted": 0, "freed_bytes": 0, "details": []}
        task.total = 0
        task.done = 0
        task.progress = 100
        await db.commit()
        return

    task.total = len(dup_groups)
    task.done = 0
    task.progress = 30
    await db.commit()

    # 阶段 2：评分并决定每组保留哪个
    all_ids = [f["id"] for _h, group in dup_groups for f in group]

    # 全库去重时 all_ids 可能很大，按片查询避免 IN(...) 超过 SQLite 变量上限
    tagged_ids: set[str] = set()
    for chunk in _chunked(all_ids):
        tagged_result = await db.execute(
            select(InspirationTag.inspiration_id)
            .where(InspirationTag.inspiration_id.in_(chunk))
            .distinct()
        )
        tagged_ids.update(r[0] for r in tagged_result.all())

    analyzed_ids: set[str] = set()
    for chunk in _chunked(all_ids):
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

    details: list[dict] = []
    ids_to_delete: list[str] = []
    files_to_delete: list[tuple[str, str | None]] = []

    for dup_hash, group in dup_groups:
        scored = []
        for f in group:
            score = 0
            reasons: list[str] = []
            if f["id"] in tagged_ids:
                score += 100
                reasons.append("有标签")
            if f["is_favorite"]:
                score += 50
                reasons.append("已收藏")
            if f["id"] in analyzed_ids:
                score += 30
                reasons.append("AI 已分析")
            if f["thumbnail_path"]:
                score += 10
                reasons.append("有缩略图")
            created_ts = f["created_at"].timestamp() if f["created_at"] else 0
            scored.append({**f, "score": score, "reasons": reasons, "created_ts": created_ts})

        scored.sort(key=lambda x: (-x["score"], x["created_ts"], x["id"]))
        keeper = scored[0]
        victims = scored[1:]

        # 安全检查：保留文件磁盘已丢失时，换一个磁盘存在的作为保留，避免误删全部副本
        keeper_full = storage_root / keeper["file_path"]
        if not keeper_full.exists():
            found = False
            for alt in scored[1:]:
                if (storage_root / alt["file_path"]).exists():
                    keeper = alt
                    victims = [f for f in scored if f["id"] != alt["id"]]
                    found = True
                    break
            if not found:
                continue

        detail = {
            "hash": dup_hash,
            "kept": {
                "id": keeper["id"],
                "file_path": keeper["file_path"],
                "score": keeper["score"],
                "reasons": keeper["reasons"],
                "size_bytes": keeper["size_bytes"],
            },
            "deleted": [],
        }
        for v in victims:
            ids_to_delete.append(v["id"])
            files_to_delete.append((v["file_path"], v["thumbnail_path"]))
            detail["deleted"].append({
                "id": v["id"],
                "file_path": v["file_path"],
                "score": v["score"],
                "reasons": v["reasons"],
                "size_bytes": v["size_bytes"],
            })
        if detail["deleted"]:
            details.append(detail)

    if not ids_to_delete:
        task.result = {"groups_processed": 0, "files_deleted": 0, "freed_bytes": 0, "details": []}
        task.done = task.total
        task.progress = 100
        await db.commit()
        return

    # 阶段 3：写墓碑、删数据库记录、删磁盘文件
    urls_to_seal: list[str] = []
    for chunk in _chunked(ids_to_delete):
        url_result = await db.execute(
            select(Inspiration.source_url).where(Inspiration.id.in_(chunk))
        )
        urls_to_seal.extend(r[0] for r in url_result.all() if r[0])
    if urls_to_seal:
        for url in urls_to_seal:
            await db.execute(
                sqlite_insert(ScraperSeenURL).values(source_url=url).prefix_with("OR IGNORE")
            )

    # 分片删除，避免 IN(...) 超过 SQLite 变量上限
    for chunk in _chunked(ids_to_delete):
        await db.execute(
            Inspiration.__table__.delete().where(Inspiration.id.in_(chunk))
        )
    await db.commit()

    # 删除 LanceDB 向量，避免孤儿向量（由 vector_store 提供，未安装时静默返回）
    await _delete_inspiration_vectors(ids_to_delete)

    freed_bytes = 0
    for fpath, thumb in files_to_delete:
        for p in (fpath, thumb):
            if p:
                full = storage_root / p
                try:
                    if full.exists():
                        freed_bytes += full.stat().st_size
                        full.unlink()
                except Exception:
                    pass

    task.result = {
        "groups_processed": len(details),
        "files_deleted": len(ids_to_delete),
        "freed_bytes": freed_bytes,
        "details": details,
    }
    task.done = task.total
    task.progress = 100
    task.updated_at = _utcnow()
    await db.commit()
    logger.info(
        f"去重任务完成: #{task.id} 处理 {len(details)} 组，删除 {len(ids_to_delete)} 个冗余文件"
    )


# 任务类型 → 执行函数的分发表：worker 按 task.type 分发到对应执行器。
# 新增任务类型时，在此注册对应的 execute_xxx 函数即可，worker 无需改动。
TASK_HANDLERS = {
    "batch_analyze": execute_batch_analyze,
    "quality_check": execute_quality_check,
    "batch_delete": execute_batch_delete,
    "deduplicate": execute_deduplicate,
}
