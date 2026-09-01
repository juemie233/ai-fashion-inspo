"""博主人脸缩略图服务：从已匹配素材的人脸检测框裁剪小图并缓存。

数据链路：素材人脸检测（inspiration_face_detections 含 bbox）→ 匹配到博主 →
按博主取置信度最高的一张人脸 → PIL 按 bbox 外扩裁剪 → 96x96 小图缓存到
``storage/faces/face_{blogger_id}.jpg`` → 博主列表/详情接口返回 face_thumb_path。

缓存策略：按博主 ID 命名（一位博主一张人脸小图）；删除博主时清理缓存文件。
素材重新检测或匹配变化后缓存可能陈旧（仍为该博主的人脸），可接受，不做失效追踪。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.face import InspirationFaceDetection
from app.models.inspiration import Inspiration

logger = logging.getLogger(__name__)

# 人脸缩略图子目录（相对 storage_root）
FACE_THUMB_DIR = "faces"
# 缩略图边长（正方形小图）
FACE_THUMB_SIZE = 96
# bbox 外扩比例：检测框通常紧贴人脸，外扩 20% 避免裁掉发丝/下颌
FACE_BBOX_PADDING = 0.2
# 缩略图 JPEG 质量
FACE_THUMB_QUALITY = 85


def face_thumb_rel_path(blogger_id: int) -> str:
    """博主人脸缩略图的相对路径（相对 storage_root）。"""
    return f"{FACE_THUMB_DIR}/face_{blogger_id}.jpg"


def face_thumb_exists(blogger_id: int) -> bool:
    """缩略图缓存是否已存在。"""
    return (settings.storage_root / face_thumb_rel_path(blogger_id)).is_file()


def delete_face_thumbnail(blogger_id: int) -> None:
    """删除博主人脸缩略图缓存（删除博主时调用，避免残留孤儿文件）。"""
    path = settings.storage_root / face_thumb_rel_path(blogger_id)
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"删除博主人脸缩略图失败（blogger={blogger_id}）: {e}")


async def _pick_detections(
    db: AsyncSession, blogger_ids: list[int]
) -> dict[int, dict]:
    """一次查询：每位博主取置信度最高、bbox 有效、素材未软删除的一条检测记录。

    返回 {blogger_id: {"file_path": str, "bbox": list}}；无可用检测的博主不在结果中。
    """
    if not blogger_ids:
        return {}
    result = await db.execute(
        select(
            InspirationFaceDetection.matched_blogger_id,
            Inspiration.file_path,
            InspirationFaceDetection.bbox,
        )
        .join(Inspiration, Inspiration.id == InspirationFaceDetection.inspiration_id)
        .where(
            InspirationFaceDetection.matched_blogger_id.in_(blogger_ids),
            InspirationFaceDetection.bbox.isnot(None),
            Inspiration.deleted_at.is_(None),
        )
        .order_by(
            InspirationFaceDetection.matched_blogger_id.asc(),
            InspirationFaceDetection.confidence.desc().nulls_last(),
            InspirationFaceDetection.created_at.desc(),
        )
    )
    picked: dict[int, dict] = {}
    for blogger_id, file_path, bbox in result.all():
        if blogger_id in picked:
            continue
        try:
            coords = json.loads(bbox)
        except (ValueError, TypeError):
            continue
        if not isinstance(coords, list) or len(coords) != 4:
            continue
        picked[blogger_id] = {"file_path": file_path, "bbox": coords}
    return picked


def _crop_face(image_bytes: bytes, bbox: list) -> bytes:
    """按 bbox 外扩裁剪人脸并缩放为正方形小图，返回 JPEG 字节。

    bbox 为原图坐标 [x1, y1, x2, y2]；外扩后 clamp 到图内，越界/贴边不裁黑边。
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        width, height = img.size
        x1, y1, x2, y2 = (float(v) for v in bbox)
        pad_x = (x2 - x1) * FACE_BBOX_PADDING
        pad_y = (y2 - y1) * FACE_BBOX_PADDING
        box = (
            max(0, int(x1 - pad_x)),
            max(0, int(y1 - pad_y)),
            min(width, int(x2 + pad_x)),
            min(height, int(y2 + pad_y)),
        )
        if box[2] - box[0] < 1 or box[3] - box[1] < 1:
            raise ValueError(f"人脸检测框无效: {bbox}")
        face = img.crop(box).resize((FACE_THUMB_SIZE, FACE_THUMB_SIZE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        face.convert("RGB").save(buf, format="JPEG", quality=FACE_THUMB_QUALITY)
        return buf.getvalue()


async def _generate(
    blogger_id: int, file_path: str, bbox: list
) -> str | None:
    """读取素材文件裁剪人脸图并写入缓存，返回相对路径；失败降级为 None。"""
    try:
        full_path = settings.storage_root / file_path
        image_bytes = await asyncio.to_thread(full_path.read_bytes)
        data = await asyncio.to_thread(_crop_face, image_bytes, bbox)
        out_dir = settings.storage_root / FACE_THUMB_DIR
        await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            (out_dir / f"face_{blogger_id}.jpg").write_bytes, data
        )
        return face_thumb_rel_path(blogger_id)
    except Exception as e:  # noqa: BLE001 素材缺失/损坏/坐标异常均降级为无缩略图
        logger.warning(f"生成博主人脸缩略图失败（blogger={blogger_id}）: {e}")
        return None


async def ensure_blogger_face_thumbnail(
    db: AsyncSession, blogger_id: int
) -> str | None:
    """确保单个博主的人脸缩略图缓存存在（详情页等单条场景），返回相对路径或 None。"""
    if face_thumb_exists(blogger_id):
        return face_thumb_rel_path(blogger_id)
    picked = await _pick_detections(db, [blogger_id])
    det = picked.get(blogger_id)
    if not det:
        return None
    return await _generate(blogger_id, det["file_path"], det["bbox"])


async def ensure_blogger_face_thumbnails(
    db: AsyncSession, blogger_ids: list[int]
) -> dict[int, str | None]:
    """批量确保缩略图缓存（列表场景）：一次查询所有候选检测，逐博主补齐缺失缓存。

    返回 {blogger_id: 相对路径 | None}；已有缓存的直接复用，不重复裁剪。
    """
    result: dict[int, str | None] = {}
    missing = [bid for bid in blogger_ids if not face_thumb_exists(bid)]
    for bid in blogger_ids:
        if bid not in missing:
            result[bid] = face_thumb_rel_path(bid)
    if missing:
        picked = await _pick_detections(db, missing)
        for bid in missing:
            det = picked.get(bid)
            result[bid] = (
                await _generate(bid, det["file_path"], det["bbox"]) if det else None
            )
    return result
