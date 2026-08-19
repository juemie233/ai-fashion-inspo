"""职业模特人脸特征库服务：从写真照片组注册（Top-K 高质量人脸平均池化）。

与博主人脸特征库（blogger_face.py）对称：一模特一条平均池化 512 维特征，
重复注册即覆盖；区别在于数据源为模特写真照片组（model_photos），且按
检测置信度挑选 Top-K 张最高质量人脸——写真组可能包含几十上百张，
侧脸/远景/闭眼废图会稀释平均特征，只取质量最高的前 K 张。

流程：收集照片 → 并发读取文件 → 批量 embed（复用 embed-batch，32 张/批）
→ 每张取最高置信度人脸并做质量过滤（置信度 + 人脸宽度占比，与博主一致）
→ 按 det_score 取 Top-K → 平均池化归一化 upsert 入库。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import numpy as np
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.face import ModelFaceEmbedding
from app.models.person import Model, ModelPhoto, ModelPhotoSet
from app.services.blogger_face import (
    LOW_CONFIDENCE_THRESHOLD,
    MIN_FACE_WIDTH_RATIO,
    _face_width_ratio,
    _image_size,
)
from app.services.face_client import FaceServiceUnavailableError, face_client
from app.utils.time import format_utc

logger = logging.getLogger(__name__)

# 照片组选图上限：Top-K 取值范围 1~9，默认取 5 张最高质量人脸平均池化
MAX_TOP_K = 9
DEFAULT_TOP_K = 5
# 批量 embed 张数（复用批量端点，减少 HTTP 往返）
EMBED_BATCH_SIZE = 32
# 照片文件读取并发上限（磁盘 I/O 放线程池）
READ_CONCURRENCY = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def register_model_face(
    db: AsyncSession,
    model_id: int,
    top_k: int = DEFAULT_TOP_K,
) -> dict:
    """从模特写真照片组注册/重新注册人脸特征（Top-K 平均池化）。

    质量过滤与博主注册一致：人脸置信度低于 LOW_CONFIDENCE_THRESHOLD、
    人脸 bbox 宽度占比低于 MIN_FACE_WIDTH_RATIO 的照片跳过；
    全部照片均无合格人脸时抛 400。
    """
    if not 1 <= top_k <= MAX_TOP_K:
        raise HTTPException(status_code=422, detail=f"top_k 取值 1~{MAX_TOP_K}")
    model = await db.get(Model, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="模特未找到")

    # ── 收集该模特全部照片（按组内排序）──
    rows = await db.execute(
        select(ModelPhoto)
        .join(ModelPhotoSet, ModelPhoto.set_id == ModelPhotoSet.id)
        .where(ModelPhotoSet.model_id == model_id)
        .order_by(ModelPhoto.sort_order, ModelPhoto.id)
    )
    photos = list(rows.scalars().all())
    if not photos:
        raise HTTPException(status_code=400, detail="该模特暂无照片，请先添加照片组")

    # ── 并发读取照片文件（磁盘 I/O 放线程池；缺失/失败跳过并记警告）──
    warnings: list[str] = []
    image_bytes_list: list[bytes] = []
    sem = asyncio.Semaphore(READ_CONCURRENCY)

    async def _read(photo: ModelPhoto) -> bytes | None:
        full_path = settings.storage_root / photo.file_path
        try:
            async with sem:
                return await asyncio.to_thread(full_path.read_bytes)
        except OSError as e:
            warnings.append(f"照片 {photo.file_path} 读取失败（{e}），已跳过")
            return None

    for photo in photos:
        data = await _read(photo)
        if data:
            image_bytes_list.append(data)
    if not image_bytes_list:
        raise HTTPException(
            status_code=400, detail="所选照片均无法读取，请检查照片文件是否完好"
        )

    # ── 批量提取特征并做质量过滤（32 张/批；item 级失败跳过）──
    candidates: list[dict] = []  # 通过质量过滤的人脸（每张图取最高置信度者）
    for start in range(0, len(image_bytes_list), EMBED_BATCH_SIZE):
        batch = image_bytes_list[start : start + EMBED_BATCH_SIZE]
        try:
            result = await face_client.embed_batch(batch)
        except FaceServiceUnavailableError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        for item in result.get("items", []):
            idx = item.get("index")
            if "error" in item or idx is None or not 0 <= idx < len(batch):
                warnings.append(f"第 {start + (idx or 0) + 1} 张照片处理失败，已跳过")
                continue
            faces = item.get("faces", [])
            if not faces:
                continue
            best = max(faces, key=lambda f: f.get("det_score", 0))
            det_score = float(best.get("det_score", 0))
            if det_score < LOW_CONFIDENCE_THRESHOLD:
                continue
            face_ratio = _face_width_ratio(best.get("bbox") or [], _image_size(batch[idx]))
            if face_ratio is not None and face_ratio < MIN_FACE_WIDTH_RATIO:
                continue
            candidates.append({"det_score": det_score, "embedding": best["embedding"]})

    if not candidates:
        raise HTTPException(
            status_code=400, detail="照片中未检出清晰人脸，请更换照片或检查照片质量"
        )

    # ── 取 Top-K 最高质量人脸，平均池化归一化入库（覆盖旧特征）──
    candidates.sort(key=lambda c: c["det_score"], reverse=True)
    top = candidates[:top_k]
    avg = np.mean(
        np.stack([np.asarray(c["embedding"], dtype=np.float32) for c in top], axis=0),
        axis=0,
    )
    norm = float(np.linalg.norm(avg))
    if norm < 1e-6:
        raise HTTPException(status_code=400, detail="特征提取异常（零向量）")
    avg = avg / norm  # 归一化，保证余弦匹配语义

    existing = await db.execute(
        select(ModelFaceEmbedding).where(ModelFaceEmbedding.model_id == model_id)
    )
    record = existing.scalar_one_or_none()
    if record is None:
        record = ModelFaceEmbedding(model_id=model_id)
        db.add(record)
    record.embedding = avg.astype(np.float32).tobytes()
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "registered": True,
        "model_id": model_id,
        "model_name": model.name,
        "photos_used": len(top),
        "photos_total": len(photos),
        "qualified": len(candidates),  # 通过质量过滤的人脸总数（Top-K 的前 K 张）
        "warnings": warnings,
        "updated_at": _now_iso(),
    }


async def get_model_face_status(db: AsyncSession, model_id: int) -> dict:
    """查询模特人脸特征注册状态。"""
    record = await db.execute(
        select(ModelFaceEmbedding).where(ModelFaceEmbedding.model_id == model_id)
    )
    rec = record.scalar_one_or_none()
    if rec is None:
        return {"registered": False, "model_id": model_id}
    return {
        "registered": True,
        "model_id": model_id,
        "updated_at": format_utc(rec.updated_at),
    }
