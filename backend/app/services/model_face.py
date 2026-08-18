"""模特人脸特征库服务：人脸注册（平均池化）、素材人脸检测匹配、手动关联。

对接独立人脸识别子服务（face-service，face_client），数据落本地两张表：
- model_face_embeddings：一位模特一条平均池化特征（512 维 float32）
- inspiration_face_detections：素材图内每张人脸一条检测与匹配结果
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.face import InspirationFaceDetection, ModelFaceEmbedding
from app.models.person import Model
from app.services.face_client import FaceServiceUnavailableError, face_client

logger = logging.getLogger(__name__)

# 注册照片张数限制（需求：1~5 张）
MAX_REGISTER_PHOTOS = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bytes_to_embedding(data: bytes) -> np.ndarray:
    """BLOB bytes → float32 数组（校验维度）。"""
    emb = np.frombuffer(data, dtype=np.float32)
    if emb.shape[0] != 512:
        raise ValueError(f"特征维度异常: {emb.shape[0]}（期望 512）")
    return emb


# ── 模特人脸注册 ──


async def register_model_face(
    db: AsyncSession,
    model_id: int,
    image_bytes_list: list[bytes],
) -> dict:
    """注册/重新注册模特人脸：逐张照片提取特征（取每张图置信度最高的人脸），
    平均池化为一条特征 upsert 入库。

    重新注册即覆盖（同 model_id 唯一），符合需求「提供重新注册功能」。
    """
    if not image_bytes_list:
        raise HTTPException(status_code=422, detail="请上传 1~5 张模特正脸照片")
    if len(image_bytes_list) > MAX_REGISTER_PHOTOS:
        raise HTTPException(
            status_code=422, detail=f"最多上传 {MAX_REGISTER_PHOTOS} 张照片"
        )

    model = await db.get(Model, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="模特未找到")

    embeddings: list[np.ndarray] = []
    used_photos = 0
    for data in image_bytes_list:
        try:
            result = await face_client.embed(data)
        except FaceServiceUnavailableError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        faces = result.get("faces", [])
        if not faces:
            continue  # 该照片未检测到人脸，跳过
        used_photos += 1
        # 取该图置信度最高的人脸（正脸照片通常只有一张脸）
        best = max(faces, key=lambda f: f.get("det_score", 0))
        embeddings.append(np.asarray(best["embedding"], dtype=np.float32))

    if not embeddings:
        raise HTTPException(
            status_code=400,
            detail="所有照片均未检测到清晰人脸，请上传正脸、光线充足的照片",
        )

    avg = np.mean(np.stack(embeddings, axis=0), axis=0)
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
        "photos_used": used_photos,
        "photos_total": len(image_bytes_list),
        "updated_at": _now_iso(),
    }


async def get_model_face_status(db: AsyncSession, model_id: int) -> dict:
    """查询模特人脸注册状态。"""
    record = await db.execute(
        select(ModelFaceEmbedding).where(ModelFaceEmbedding.model_id == model_id)
    )
    rec = record.scalar_one_or_none()
    if rec is None:
        return {"registered": False, "model_id": model_id}
    return {
        "registered": True,
        "model_id": model_id,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
    }


# ── 素材人脸检测与匹配 ──


async def detect_inspiration_faces(
    db: AsyncSession, inspiration_id: str, image_bytes: bytes | None = None
) -> dict:
    """检测素材中的人脸并与模特特征库匹配（余弦相似度，阈值 face_match_threshold）。

    - 多张人脸分别匹配；同一图中命中多个已知模特时全部关联
    - 低于阈值的人脸 matched_model_id 置空（疑似未知人脸，供用户手动选择）
    - 重新检测会覆盖旧记录（先清后写）
    """
    from app.models.inspiration import Inspiration

    insp = await db.execute(
        select(Inspiration).where(Inspiration.id == inspiration_id)
    )
    inspiration = insp.scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="素材未找到")

    if image_bytes is None:
        full_path = settings.storage_root / inspiration.file_path
        try:
            image_bytes = full_path.read_bytes()
        except OSError as e:
            raise HTTPException(status_code=404, detail=f"素材文件缺失: {e}") from e

    try:
        result = await face_client.embed(image_bytes)
    except FaceServiceUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    faces = result.get("faces", [])

    # 加载模特特征库
    rows = await db.execute(select(ModelFaceEmbedding))
    library = [
        {"model_id": r.model_id, "embedding": _bytes_to_embedding(r.embedding)}
        for r in rows.scalars().all()
    ]

    # 清旧记录（重新检测覆盖）
    await db.execute(
        delete(InspirationFaceDetection).where(
            InspirationFaceDetection.inspiration_id == inspiration_id
        )
    )

    threshold = settings.face_match_threshold
    detections = []
    for idx, face in enumerate(faces):
        query = np.asarray(face["embedding"], dtype=np.float32)
        best_model_id: int | None = None
        best_score = 0.0
        for item in library:
            score = float(np.dot(query, item["embedding"]))  # 均归一化，点积即余弦
            if score > best_score:
                best_score = score
                best_model_id = item["model_id"]
        if best_model_id is None or best_score < threshold:
            best_model_id = None
            best_score = 0.0  # 未命中不记录相似度（或记录？未命中显示为空）
        det = InspirationFaceDetection(
            inspiration_id=inspiration_id,
            face_index=idx,
            embedding=np.asarray(face["embedding"], dtype=np.float32).tobytes(),
            matched_model_id=best_model_id,
            confidence=round(best_score, 4) if best_model_id is not None else None,
        )
        db.add(det)
        detections.append(det)

    await db.commit()

    # 组装返回（含模特名）
    return {
        "inspiration_id": inspiration_id,
        "face_count": len(detections),
        "detections": [
            {
                "id": d.id,
                "face_index": d.face_index,
                "matched_model_id": d.matched_model_id,
                "confidence": d.confidence,
            }
            for d in detections
        ],
    }


async def list_inspiration_detections(
    db: AsyncSession, inspiration_id: str
) -> list[dict]:
    """素材人脸检测列表（含匹配模特信息）。"""
    rows = await db.execute(
        select(InspirationFaceDetection)
        .where(InspirationFaceDetection.inspiration_id == inspiration_id)
        .options(selectinload(InspirationFaceDetection.matched_model))
        .order_by(InspirationFaceDetection.face_index)
    )
    result = []
    for d in rows.scalars().all():
        result.append(
            {
                "id": d.id,
                "face_index": d.face_index,
                "matched_model_id": d.matched_model_id,
                "matched_model_name": d.matched_model.name if d.matched_model else None,
                "confidence": d.confidence,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
        )
    return result


async def set_detection_model(
    db: AsyncSession, detection_id: int, model_id: int | None
) -> dict:
    """手动指定/解除人脸检测的模特关联（model_id 为 None 即解除）。"""
    det = await db.get(InspirationFaceDetection, detection_id)
    if not det:
        raise HTTPException(status_code=404, detail="人脸检测记录未找到")
    if model_id is not None:
        model = await db.get(Model, model_id)
        if not model:
            raise HTTPException(status_code=404, detail="模特未找到")
    det.matched_model_id = model_id
    det.confidence = None if model_id is None else det.confidence
    await db.commit()
    return {"updated": True, "detection_id": detection_id, "matched_model_id": model_id}


async def delete_detection(db: AsyncSession, detection_id: int) -> None:
    """删除单条人脸检测记录。"""
    det = await db.get(InspirationFaceDetection, detection_id)
    if not det:
        raise HTTPException(status_code=404, detail="人脸检测记录未找到")
    await db.delete(det)
    await db.commit()
