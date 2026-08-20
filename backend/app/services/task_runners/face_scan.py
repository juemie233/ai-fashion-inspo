"""人脸库扫描任务：批量检测素材人脸（层 1 重活）与全库候选匹配（层 2 轻活）。

- ``face_scan``：增量/全量扫描素材 → embed-batch 批量检测 → 人脸明细写入
  detections 表（每张人脸一条，含 embedding/bbox）。增量语义 = 只扫无检测
  记录的素材（断点续跑：取消/失败后重跑自动跳过已扫部分）；无脸素材写入
  一条空 embedding 的占位记录，保证「有记录即已扫」成立。
  任务执行期间每批检查 task.status，被取消（cancelled）则停止（已写不回滚）。
  扫描完成后按载荷 auto_match 自动创建 face_match 任务。
- ``face_match``：全库候选匹配（矩阵乘），产出 pending 候选待人工审核，
  不写人物关联表（确认由扫描审核接口完成）。

批处理约定：
- 每批 ≤64 张 且 ≤150MB（字节超限自动封批），embed-batch 每 32 张一个请求、
  并发 4 路，均衡 GPU 吞吐与内存峰值；
- 素材文件缺失/损坏计入 failed_files 跳过，不阻塞整批；
- face-service 不可用抛 RecoverableTaskError 交由 worker 重试（增量语义下
  重跑成本 = 未扫部分）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import numpy as np
from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.face import InspirationFaceDetection
from app.models.inspiration import Inspiration, NOT_DELETED
from app.models.task import TaskQueue
from app.services.face_client import FaceServiceUnavailableError, face_client
from app.services.face_match import match_all_faces
from app.services.task_runners.common import RecoverableTaskError, _chunked, utcnow

logger = logging.getLogger(__name__)

# 每批素材数上限与字节上限（原图上传体积控制，超限自动封批）
SCAN_BATCH_SIZE = 64
SCAN_BATCH_BYTES = 150 * 1024 * 1024
# embed-batch 单请求张数与并发路数
EMBED_REQUEST_SIZE = 32
EMBED_CONCURRENCY = 4
# 连续失败批数阈值：超过视为系统性故障直接终止（交 worker 重试）
MAX_CONSECUTIVE_FAILED_BATCHES = 10


async def create_face_scan_task(
    db: AsyncSession, scope: str = "incremental", auto_match: bool = True
) -> TaskQueue:
    """创建人脸库扫描任务（total = 待扫图片素材数）。"""
    if scope not in ("incremental", "all"):
        raise ValueError(f"未知扫描范围: {scope}")
    total = await _count_scan_images(db, scope)
    task = TaskQueue(
        type="face_scan",
        status="pending",
        progress=0,
        total=total,
        done=0,
        result={"scope": scope, "auto_match": auto_match},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    logger.info(f"已创建人脸扫描任务: #{task.id}，范围 {scope}，待扫 {total} 个素材")
    return task


async def create_face_match_task(
    db: AsyncSession,
    scope: str = "all",
    person_ids: list[int] | None = None,
    threshold: float | None = None,
) -> TaskQueue:
    """创建全库候选匹配任务（层 2 轻活，秒级~分钟级）。"""
    task = TaskQueue(
        type="face_match",
        status="pending",
        progress=0,
        total=0,
        done=0,
        result={
            "scope": scope,
            "person_ids": person_ids,
            "threshold": threshold,
        },
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    logger.info(f"已创建人脸匹配任务: #{task.id}（scope={scope}）")
    return task


async def _count_scan_images(db: AsyncSession, scope: str) -> int:
    """统计待扫图片素材数（incremental = 无检测记录的图片素材）。"""
    stmt = select(func.count()).select_from(Inspiration).where(
        Inspiration.media_type == "image", NOT_DELETED
    )
    if scope == "incremental":
        stmt = stmt.where(
            ~exists().where(InspirationFaceDetection.inspiration_id == Inspiration.id)
        )
    return (await db.execute(stmt)).scalar() or 0


async def _collect_scan_ids(db: AsyncSession, scope: str) -> list[str]:
    """收集待扫素材 ID（一次查询全部；几万 UUID 内存可控）。"""
    stmt = select(Inspiration.id).where(
        Inspiration.media_type == "image", NOT_DELETED
    )
    if scope == "incremental":
        stmt = stmt.where(
            ~exists().where(InspirationFaceDetection.inspiration_id == Inspiration.id)
        )
    stmt = stmt.order_by(Inspiration.created_at, Inspiration.id)  # id 兜底保证同秒内顺序稳定
    return list((await db.execute(stmt)).scalars().all())


async def _is_cancelled(db: AsyncSession, task: TaskQueue) -> bool:
    """检查任务是否被外部置为 cancelled（每批调用一次）。"""
    result = await db.execute(select(TaskQueue.status).where(TaskQueue.id == task.id))
    return (result.scalar() or "running") == "cancelled"


async def execute_face_scan(db: AsyncSession, task: TaskQueue) -> None:
    """执行人脸库扫描任务：分批检测素材并写 detections（增量/全量）。"""
    payload = task.result or {}
    scope = payload.get("scope", "incremental")
    auto_match = bool(payload.get("auto_match", True))
    ids = await _collect_scan_ids(db, scope)
    total = len(ids)
    task.total = total
    if total == 0:
        task.progress = 100
        task.done = 0
        task.result = {
            **payload,
            "scanned": 0,
            "faces": 0,
            "failed_files": 0,
            "match_task_id": None,
            "message": "无可扫描素材（增量场景下可能已全部扫描过）",
        }
        await db.commit()
        return

    if scope == "all":
        # 全量重扫：先清空全部检测记录
        await db.execute(delete(InspirationFaceDetection))
        await db.commit()

    # 一次查出全部素材的 file_path（IN 分片 500）
    path_map: dict[str, str] = {}
    for chunk in _chunked(ids, 500):
        rows = await db.execute(
            select(Inspiration.id, Inspiration.file_path).where(
                Inspiration.id.in_(chunk)
            )
        )
        path_map.update({r[0]: r[1] for r in rows.all()})

    scanned = 0
    faces_total = 0
    failed_files = 0
    cancelled = False
    consecutive_failures = 0
    start = 0
    while start < total:
        if await _is_cancelled(db, task):
            cancelled = True
            break

        # 组装一批（≤64 张 且 ≤150MB；缺失文件跳过并剔除出 id 列表）
        batch_ids: list[str] = []
        batch_bytes: list[bytes] = []
        batch_size = 0
        while (
            start + len(batch_ids) < total
            and len(batch_ids) < SCAN_BATCH_SIZE
            and batch_size < SCAN_BATCH_BYTES
        ):
            insp_id = ids[start + len(batch_ids)]
            fpath = path_map.get(insp_id)
            if not fpath:
                failed_files += 1
                scanned += 1
                ids.pop(start + len(batch_ids))
                continue
            try:
                full_path = Path(settings.storage_root) / fpath
                data = await asyncio.to_thread(full_path.read_bytes)
            except OSError:
                failed_files += 1
                scanned += 1
                ids.pop(start + len(batch_ids))
                continue
            batch_ids.append(insp_id)
            batch_bytes.append(data)
            batch_size += len(data)

        if not batch_ids:
            # 剩余全部文件缺失：避免死循环
            break

        try:
            faces_list = await _embed_batch_concurrent(batch_bytes)
            await _write_detections(db, batch_ids, faces_list)
        except FaceServiceUnavailableError as e:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILED_BATCHES:
                raise RecoverableTaskError(
                    f"人脸识别子服务连续 {consecutive_failures} 批不可用，任务终止: {e}"
                ) from e
            raise RecoverableTaskError(
                f"人脸识别子服务不可用，任务将重试: {e}"
            ) from e
        consecutive_failures = 0
        scanned += len(batch_ids)
        faces_total += sum(len(faces) for faces in faces_list)
        task.done = scanned
        task.progress = round(scanned / total * 100)
        task.updated_at = utcnow()
        await db.commit()
        logger.info(f"人脸扫描进度: #{task.id} {task.progress}% ({scanned}/{total})")
        start += len(batch_ids)

    # 自动创建全库匹配任务（扫完才执行；worker 串行保证顺序）
    match_task_id: int | None = None
    if not cancelled and auto_match:
        match_task = await create_face_match_task(db, scope="all")
        match_task_id = match_task.id

    task.result = {
        **payload,
        "scanned": scanned,
        "faces": faces_total,
        "failed_files": failed_files,
        "match_task_id": match_task_id,
        "cancelled": cancelled,
    }
    if cancelled:
        task.status = "cancelled"  # worker 见 status != running 不会覆盖为 success
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"人脸扫描任务结束: #{task.id} scanned={scanned} faces={faces_total} "
        f"failed={failed_files} cancelled={cancelled}"
    )


async def _embed_batch_concurrent(batch_bytes: list[bytes]) -> list[list[dict]]:
    """批量提取特征（每 32 张一个请求、4 路并发），返回每张图的 faces 列表。

    单张解码失败按空列表处理（不落人脸记录，但素材仍算「已扫」）；
    服务不可用向上抛（可恢复重试）。
    """
    results: list[list[dict] | None] = [None] * len(batch_bytes)
    sem = asyncio.Semaphore(EMBED_CONCURRENCY)

    async def _one(start: int, chunk: list[bytes]) -> None:
        async with sem:
            result = await face_client.embed_batch(chunk)
        for item in result.get("items", []):
            idx = item.get("index")
            if idx is None or not 0 <= idx < len(chunk):
                continue
            if "error" in item:
                results[start + idx] = []
                continue
            results[start + idx] = item.get("faces", [])

    chunks = [
        (start, batch_bytes[start : start + EMBED_REQUEST_SIZE])
        for start in range(0, len(batch_bytes), EMBED_REQUEST_SIZE)
    ]
    await asyncio.gather(*(_one(start, chunk) for start, chunk in chunks))
    return [r if r is not None else [] for r in results]


async def _write_detections(
    db: AsyncSession, inspiration_ids: list[str], faces_list: list[list[dict]]
) -> None:
    """先清后写本批素材的检测记录（幂等）。

    有脸素材每张人脸一条记录（embedding/bbox 落库，供矩阵匹配与人脸缩略图）；
    无脸素材写入一条空 embedding 的占位记录，保证「有记录即已扫」的增量语义
    （占位记录不参与矩阵匹配——match_all_faces 查询时过滤空 embedding）。
    """
    await db.execute(
        delete(InspirationFaceDetection).where(
            InspirationFaceDetection.inspiration_id.in_(inspiration_ids)
        )
    )
    for insp_id, faces in zip(inspiration_ids, faces_list):
        if not faces:
            db.add(
                InspirationFaceDetection(
                    inspiration_id=insp_id,
                    face_index=0,
                    embedding=b"",
                    match_status=None,
                )
            )
            continue
        for idx, face in enumerate(faces):
            db.add(
                InspirationFaceDetection(
                    inspiration_id=insp_id,
                    face_index=idx,
                    embedding=np.asarray(
                        face["embedding"], dtype=np.float32
                    ).tobytes(),
                    bbox=(
                        json.dumps(face["bbox"])
                        if isinstance(face.get("bbox"), list)
                        else None
                    ),
                    det_score=(
                        round(float(face["det_score"]), 4)
                        if face.get("det_score") is not None
                        else None
                    ),
                    match_status=None,
                )
            )


async def execute_face_match(db: AsyncSession, task: TaskQueue) -> None:
    """执行全库候选匹配任务：矩阵乘比对 → 写 pending 候选。"""
    payload = task.result or {}
    stats = await match_all_faces(
        db,
        scope=payload.get("scope", "all"),
        person_ids=payload.get("person_ids"),
        threshold=payload.get("threshold"),
    )
    task.progress = 100
    task.done = stats["total_faces"]
    task.total = stats["total_faces"]
    task.result = {**payload, **stats}
    task.error = None
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"人脸匹配任务完成: #{task.id} total={stats['total_faces']} "
        f"matched={stats['matched']} unmatched={stats['unmatched']}"
    )
