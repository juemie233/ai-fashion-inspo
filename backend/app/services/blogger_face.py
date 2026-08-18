"""穿搭博主人脸特征库服务：人脸注册（平均池化）、素材人脸检测匹配、手动关联。

对接独立人脸识别子服务（face-service，face_client），数据落本地两张表：
- blogger_face_embeddings：一位博主一条平均池化特征（512 维 float32）
- inspiration_face_detections：素材图内每张人脸一条检测与匹配结果

注：人脸识别服务于穿搭博主（素材人脸自动匹配博主特征库）；职业模特无需人脸能力。
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

import numpy as np
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.face import BloggerFaceEmbedding, InspirationFaceDetection
from app.models.person import Blogger
from app.services.face_client import (
    FaceServiceHttpError,
    FaceServiceUnavailableError,
    face_client,
)

logger = logging.getLogger(__name__)

# 注册照片张数限制（需求：1~5 张）
MAX_REGISTER_PHOTOS = 5
# 注册人脸质量阈值：face-service 已过滤置信度 < 0.5 的人脸，
# 0.5~0.65 区间视为「置信度偏低」；人脸 bbox 面积占比低于 3% 视为「人脸过小」
LOW_CONFIDENCE_THRESHOLD = 0.65
MIN_FACE_RATIO = 0.03


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bytes_to_embedding(data: bytes) -> np.ndarray:
    """BLOB bytes → float32 数组（校验维度）。"""
    emb = np.frombuffer(data, dtype=np.float32)
    if emb.shape[0] != 512:
        raise ValueError(f"特征维度异常: {emb.shape[0]}（期望 512）")
    return emb


def _image_size(data: bytes) -> tuple[int, int] | None:
    """从图片字节读取宽高（仅解析文件头，不完整解码）。"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size  # (w, h)
    except Exception:  # noqa: BLE001 图片损坏/格式不支持时无法计算占比
        return None


def _face_ratio(bbox: list, img_size: tuple[int, int] | None) -> float | None:
    """人脸 bbox 面积占图片面积的比例（bbox 为原图坐标）。"""
    if len(bbox) != 4 or img_size is None:
        return None
    bw = float(bbox[2] - bbox[0])
    bh = float(bbox[3] - bbox[1])
    return round((bw * bh) / (float(img_size[0]) * float(img_size[1])), 4)


# ── 博主人脸注册 ──


async def register_blogger_face(
    db: AsyncSession,
    blogger_id: int,
    image_bytes_list: list[bytes],
) -> dict:
    """注册/重新注册博主人脸：逐张照片提取特征（取每张图置信度最高的人脸），
    平均池化为一条特征 upsert 入库。

    重新注册即覆盖（同 blogger_id 唯一），符合需求「提供重新注册功能」。
    """
    if not image_bytes_list:
        raise HTTPException(status_code=422, detail="请上传 1~5 张博主正脸照片")
    if len(image_bytes_list) > MAX_REGISTER_PHOTOS:
        raise HTTPException(
            status_code=422, detail=f"最多上传 {MAX_REGISTER_PHOTOS} 张照片"
        )

    blogger = await db.get(Blogger, blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="博主未找到")

    embeddings: list[np.ndarray] = []
    # 每张照片的结果明细（供前端逐张提示跳过原因）
    photo_results: list[dict] = []
    for idx, data in enumerate(image_bytes_list, start=1):
        try:
            result = await face_client.embed(data)
        except FaceServiceHttpError as e:
            if e.status_code == 404:
                # 子服务 404 = 该照片未检测到人脸（业务结果）：跳过该照片，
                # 与返回空结果语义一致；全部照片都无人脸时由下方统一提示
                photo_results.append(
                    {
                        "index": idx,
                        "status": "skipped",
                        "reason": "no_face",
                        "message": "未检测到人脸",
                        "det_score": None,
                        "face_ratio": None,
                    }
                )
                continue
            raise HTTPException(status_code=503, detail=str(e)) from e
        except FaceServiceUnavailableError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        faces = result.get("faces", [])
        if not faces:
            # 子服务正常响应但未检出人脸（与 404 同语义）
            photo_results.append(
                {
                    "index": idx,
                    "status": "skipped",
                    "reason": "no_face",
                    "message": "未检测到人脸",
                    "det_score": None,
                    "face_ratio": None,
                }
            )
            continue
        # 取该图置信度最高的人脸（正脸照片通常只有一张脸）
        best = max(faces, key=lambda f: f.get("det_score", 0))
        det_score = float(best.get("det_score", 0))
        face_ratio = _face_ratio(best.get("bbox") or [], _image_size(data))
        # 质量判定：置信度偏低 → 人脸过小 → 合格
        if det_score < LOW_CONFIDENCE_THRESHOLD:
            photo_results.append(
                {
                    "index": idx,
                    "status": "skipped",
                    "reason": "low_confidence",
                    "message": f"人脸置信度偏低（{det_score:.2f}），建议换更清晰的正脸照片",
                    "det_score": round(det_score, 3),
                    "face_ratio": face_ratio,
                }
            )
            continue
        if face_ratio is not None and face_ratio < MIN_FACE_RATIO:
            photo_results.append(
                {
                    "index": idx,
                    "status": "skipped",
                    "reason": "small_face",
                    "message": f"人脸过小（占画面 {face_ratio * 100:.1f}%），建议裁剪放大后上传",
                    "det_score": round(det_score, 3),
                    "face_ratio": face_ratio,
                }
            )
            continue
        photo_results.append(
            {
                "index": idx,
                "status": "used",
                "reason": None,
                "message": None,
                "det_score": round(det_score, 3),
                "face_ratio": face_ratio,
            }
        )
        embeddings.append(np.asarray(best["embedding"], dtype=np.float32))

    if not embeddings:
        reasons = "；".join(
            f"第{r['index']}张{r['message']}" for r in photo_results
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"所有照片均未检出清晰人脸（{reasons}），"
                "请上传正脸、光线充足的照片"
            ),
        )

    avg = np.mean(np.stack(embeddings, axis=0), axis=0)
    norm = float(np.linalg.norm(avg))
    if norm < 1e-6:
        raise HTTPException(status_code=400, detail="特征提取异常（零向量）")
    avg = avg / norm  # 归一化，保证余弦匹配语义

    existing = await db.execute(
        select(BloggerFaceEmbedding).where(BloggerFaceEmbedding.blogger_id == blogger_id)
    )
    record = existing.scalar_one_or_none()
    if record is None:
        record = BloggerFaceEmbedding(blogger_id=blogger_id)
        db.add(record)
    record.embedding = avg.astype(np.float32).tobytes()
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "registered": True,
        "blogger_id": blogger_id,
        "blogger_name": blogger.name,
        "photos_used": len(embeddings),
        "photos_total": len(image_bytes_list),
        "updated_at": _now_iso(),
        "photo_results": photo_results,
    }


async def get_blogger_face_status(db: AsyncSession, blogger_id: int) -> dict:
    """查询博主人脸注册状态。"""
    record = await db.execute(
        select(BloggerFaceEmbedding).where(BloggerFaceEmbedding.blogger_id == blogger_id)
    )
    rec = record.scalar_one_or_none()
    if rec is None:
        return {"registered": False, "blogger_id": blogger_id}
    return {
        "registered": True,
        "blogger_id": blogger_id,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
    }


# ── 素材人脸检测与匹配 ──


async def detect_inspiration_faces(
    db: AsyncSession, inspiration_id: str, image_bytes: bytes | None = None
) -> dict:
    """检测素材中的人脸并与博主特征库匹配（余弦相似度，阈值 face_match_threshold）。

    - 多张人脸分别匹配；同一图中命中多个已知博主时全部关联
    - 低于阈值的人脸 matched_blogger_id 置空（疑似未知人脸，供用户手动选择）
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
    except FaceServiceHttpError as e:
        if e.status_code == 404:
            # 素材图中未检测到人脸（业务结果，非故障）：清空旧记录并返回空结果
            await db.execute(
                delete(InspirationFaceDetection).where(
                    InspirationFaceDetection.inspiration_id == inspiration_id
                )
            )
            await db.commit()
            return {"inspiration_id": inspiration_id, "face_count": 0, "detections": []}
        raise HTTPException(status_code=503, detail=str(e)) from e
    except FaceServiceUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    faces = result.get("faces", [])

    # 加载博主特征库
    rows = await db.execute(select(BloggerFaceEmbedding))
    library = [
        {"blogger_id": r.blogger_id, "embedding": _bytes_to_embedding(r.embedding)}
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
        best_blogger_id: int | None = None
        best_score = 0.0
        for item in library:
            score = float(np.dot(query, item["embedding"]))  # 均归一化，点积即余弦
            if score > best_score:
                best_score = score
                best_blogger_id = item["blogger_id"]
        if best_blogger_id is None or best_score < threshold:
            best_blogger_id = None
            best_score = 0.0  # 未命中不记录相似度（未命中显示为空）
        det = InspirationFaceDetection(
            inspiration_id=inspiration_id,
            face_index=idx,
            embedding=np.asarray(face["embedding"], dtype=np.float32).tobytes(),
            matched_blogger_id=best_blogger_id,
            confidence=round(best_score, 4) if best_blogger_id is not None else None,
        )
        db.add(det)
        detections.append(det)

    await db.commit()

    # 组装返回（含博主名）
    return {
        "inspiration_id": inspiration_id,
        "face_count": len(detections),
        "detections": [
            {
                "id": d.id,
                "face_index": d.face_index,
                "matched_blogger_id": d.matched_blogger_id,
                "confidence": d.confidence,
            }
            for d in detections
        ],
    }


async def list_inspiration_detections(
    db: AsyncSession, inspiration_id: str
) -> list[dict]:
    """素材人脸检测列表（含匹配博主信息）。"""
    rows = await db.execute(
        select(InspirationFaceDetection)
        .where(InspirationFaceDetection.inspiration_id == inspiration_id)
        .options(selectinload(InspirationFaceDetection.matched_blogger))
        .order_by(InspirationFaceDetection.face_index)
    )
    result = []
    for d in rows.scalars().all():
        result.append(
            {
                "id": d.id,
                "face_index": d.face_index,
                "matched_blogger_id": d.matched_blogger_id,
                "matched_blogger_name": (
                    d.matched_blogger.name if d.matched_blogger else None
                ),
                "confidence": d.confidence,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
        )
    return result


async def set_detection_blogger(
    db: AsyncSession, detection_id: int, blogger_id: int | None
) -> dict:
    """手动指定/解除人脸检测的博主关联（blogger_id 为 None 即解除）。"""
    det = await db.get(InspirationFaceDetection, detection_id)
    if not det:
        raise HTTPException(status_code=404, detail="人脸检测记录未找到")
    if blogger_id is not None:
        blogger = await db.get(Blogger, blogger_id)
        if not blogger:
            raise HTTPException(status_code=404, detail="博主未找到")
    det.matched_blogger_id = blogger_id
    det.confidence = None if blogger_id is None else det.confidence
    await db.commit()
    return {
        "updated": True,
        "detection_id": detection_id,
        "matched_blogger_id": blogger_id,
    }


async def delete_detection(db: AsyncSession, detection_id: int) -> None:
    """删除单条人脸检测记录。"""
    det = await db.get(InspirationFaceDetection, detection_id)
    if not det:
        raise HTTPException(status_code=404, detail="人脸检测记录未找到")
    await db.delete(det)
    await db.commit()
