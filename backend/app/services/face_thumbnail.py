"""博主人脸缩略图（人物头像）服务：从已匹配素材的人脸检测框裁剪小图并缓存。

数据链路：素材人脸检测（inspiration_face_detections 含 bbox）→ 匹配到博主 →
按博主取一张可信且可裁剪的人脸 → PIL 按 bbox 外扩裁剪 → 96x96 小图缓存到
``storage/faces/face_{博主id}_{检测id}.jpg`` → 博主列表/详情接口返回 face_thumb_path。

**缓存键带来源检测 id**：来源（哪张素材的哪张脸）变了就是另一个文件名，下次请求
自动重裁，旧文件在重裁时清理。这样「头像永远停在第一次裁剪的那张脸」不再可能——
换素材、重新扫描、驳回某张脸、把脸改派给别的博主、素材被删之后，头像都会跟着变。
（历史命名 ``face_{id}.jpg`` 视为过期缓存，正常路径会清掉。）

**只有可信来源参与**（三条过滤，缺一条就会出现「头像是别人」）：
  1. ``match_status`` 为 NULL（手动指定 / 单素材检测结果）或 ``confirmed``（人工确认）
     —— **pending 是未审核的 AI 候选**（见 face_match.match_all_faces），
     素材详情页都不展示它，头像更不该先用上
  2. ``match_excluded`` 为假 —— 用户明确点过「不匹配」的脸不再参与
  3. 素材是**图片** —— 视频的人脸检测来自**关键帧**（face_scan 送检的是抽帧图），
     bbox 是帧坐标，拿去裁 mp4/海报图要么打不开要么裁错位置，故不参与头像

顺序：匹配置信度高的优先，其次新检测的优先（同分时更可能来自较新的素材）。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path

from PIL import Image
from sqlalchemy import or_, select
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


def face_thumb_rel_path(blogger_id: int, detection_id: int) -> str:
    """博主人脸缩略图的相对路径（相对 storage_root）。

    Args:
        blogger_id: 博主 id。
        detection_id: 来源人脸检测记录 id（缓存键的一部分：来源变了就换文件）。
    """
    return f"{FACE_THUMB_DIR}/face_{blogger_id}_{detection_id}.jpg"


def _thumb_files(directory: Path, blogger_id: int) -> list[Path]:
    """某博主的全部人脸小图缓存文件（含历史命名 ``face_{id}.jpg``）。"""
    files = list(directory.glob(f"face_{blogger_id}_*.jpg"))
    legacy = directory / f"face_{blogger_id}.jpg"
    if legacy.is_file():
        files.append(legacy)
    return files


def clear_face_thumbnails(blogger_id: int) -> int:
    """删除某博主的全部人脸小图缓存（含历史命名），返回删除数量。"""
    directory = settings.storage_root / FACE_THUMB_DIR
    if not directory.is_dir():
        return 0
    removed = 0
    for path in _thumb_files(directory, blogger_id):
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as e:
            logger.warning(f"删除博主人脸缩略图失败（blogger={blogger_id}）: {e}")
    return removed


def delete_face_thumbnail(blogger_id: int) -> None:
    """删除博主人脸缩略图缓存（注销人脸 / 删除博主时调用，避免残留孤儿文件）。"""
    clear_face_thumbnails(blogger_id)


async def _pick_detections(db: AsyncSession, blogger_ids: list[int]) -> dict[int, dict]:
    """一次查询：每位博主取一条**可信且可裁剪**的人脸检测（见模块 docstring）。

    Returns:
        {blogger_id: {"detection_id", "file_path", "bbox"}}；无可用人脸的博主不在结果中。
    """
    if not blogger_ids:
        return {}
    result = await db.execute(
        select(
            InspirationFaceDetection.matched_blogger_id,
            InspirationFaceDetection.id,
            Inspiration.file_path,
            InspirationFaceDetection.bbox,
        )
        .join(Inspiration, Inspiration.id == InspirationFaceDetection.inspiration_id)
        .where(
            InspirationFaceDetection.matched_blogger_id.in_(blogger_ids),
            InspirationFaceDetection.bbox.isnot(None),
            # 人工「不匹配」的脸不参与
            InspirationFaceDetection.match_excluded.is_(False),
            # 未审核的 AI 候选（pending）不参与；NULL=手动/单素材结果，confirmed=人工确认
            or_(
                InspirationFaceDetection.match_status.is_(None),
                InspirationFaceDetection.match_status == "confirmed",
            ),
            # 只裁图片素材：视频的 bbox 是抽帧坐标，裁 mp4/海报都会错
            Inspiration.media_type == "image",
            Inspiration.deleted_at.is_(None),
        )
        .order_by(
            InspirationFaceDetection.matched_blogger_id.asc(),
            InspirationFaceDetection.confidence.desc().nulls_last(),
            InspirationFaceDetection.created_at.desc(),
        )
    )
    picked: dict[int, dict] = {}
    for blogger_id, detection_id, file_path, bbox in result.all():
        if blogger_id in picked:
            continue
        try:
            coords = json.loads(bbox)
        except (ValueError, TypeError):
            continue
        if not isinstance(coords, list) or len(coords) != 4:
            continue
        picked[blogger_id] = {
            "detection_id": detection_id,
            "file_path": file_path,
            "bbox": coords,
        }
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


async def _generate(blogger_id: int, det: dict) -> str | None:
    """按检测来源裁剪人脸图并写入缓存，返回相对路径；失败降级为 None。

    写入新文件后清理该博主的旧缓存（含历史命名）：头像只保留当前来源那一张。
    """
    detection_id = det["detection_id"]
    try:
        full_path = settings.storage_root / det["file_path"]
        image_bytes = await asyncio.to_thread(full_path.read_bytes)
        data = await asyncio.to_thread(_crop_face, image_bytes, det["bbox"])
        out_dir = settings.storage_root / FACE_THUMB_DIR
        await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
        target = out_dir / f"face_{blogger_id}_{detection_id}.jpg"
        await asyncio.to_thread(target.write_bytes, data)
        for stale in _thumb_files(out_dir, blogger_id):
            if stale.name != target.name:
                try:
                    stale.unlink(missing_ok=True)
                except OSError:
                    pass
        return face_thumb_rel_path(blogger_id, detection_id)
    except Exception as e:  # noqa: BLE001 素材缺失/损坏/坐标异常均降级为无缩略图
        logger.warning(f"生成博主人脸缩略图失败（blogger={blogger_id}）: {e}")
        return None


async def _ensure_cached(blogger_id: int, det: dict) -> str | None:
    """缓存命中直接复用；来源变了就重裁（缓存键含检测 id）。"""
    rel_path = face_thumb_rel_path(blogger_id, det["detection_id"])
    if (settings.storage_root / rel_path).is_file():
        return rel_path
    return await _generate(blogger_id, det)


async def ensure_blogger_face_thumbnail(
    db: AsyncSession, blogger_id: int
) -> str | None:
    """确保单个博主的人脸缩略图（详情页等单条场景），返回相对路径或 None。"""
    picked = await _pick_detections(db, [blogger_id])
    det = picked.get(blogger_id)
    if not det:
        return None
    return await _ensure_cached(blogger_id, det)


async def ensure_blogger_face_thumbnails(
    db: AsyncSession, blogger_ids: list[int]
) -> dict[int, str | None]:
    """批量确保缩略图（列表场景）：一次查询所有博主当前来源，只补缺失/过期的缓存。

    返回 {blogger_id: 相对路径 | None}。
    """
    if not blogger_ids:
        return {}
    picked = await _pick_detections(db, blogger_ids)
    result: dict[int, str | None] = {}
    for blogger_id in blogger_ids:
        det = picked.get(blogger_id)
        result[blogger_id] = await _ensure_cached(blogger_id, det) if det else None
    return result
