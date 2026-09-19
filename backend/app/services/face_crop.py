"""从一张照片里提取人像头像（人脸检测框外扩 + 正方形裁剪）。

用途：手动设置人物头像时，用户挑的那张照片通常是人物的**全身/半身**照，
直接整张当头像看不清脸。这里送检人脸识别子服务，取照片里**最大的一张人脸**
（最大 = 画面主体，避免合照里路人抢镜），按检测框外扩后裁成正方形人像。

从旧的 ``face_thumbnail.py`` 恢复了**裁剪**这一段（bbox 外扩 20% + clamp 到图内 +
LANCZOS 正方形缩放），但没有恢复它那套「按博主缓存 + face_thumb_path 字段」的
自动生成链路——头像是用户手动设置的产物，只在这条链路上做一次人脸提取。

送检与裁剪必须用**同一份字节**（bbox 是送检图坐标系）——调用方先
:func:`app.services.face_image.normalize_image` 归一化，再把归一化后的字节传进来。
"""

from __future__ import annotations

import asyncio
import io
import logging

from PIL import Image

from app.services.face_client import (
    FaceServiceHttpError,
    FaceServiceUnavailableError,
    face_client,
)

logger = logging.getLogger(__name__)

"""人像头像边长（像素）：列表 40px / 详情 72px 显示，256 足够清晰且文件小。"""
FACE_CROP_SIZE = 256

"""bbox 外扩比例：检测框通常紧贴人脸，外扩 20% 避免裁掉发丝/下颌。"""
FACE_BBOX_PADDING = 0.2

"""人像头像 JPEG 质量。"""
FACE_CROP_QUALITY = 92


def crop_face(image_bytes: bytes, bbox: list, size: int = FACE_CROP_SIZE) -> bytes:
    """按 bbox 外扩裁剪人脸并缩放为正方形，返回 JPEG 字节。

    Args:
        image_bytes: 送检用的那张图片字节（与 bbox 同坐标系）。
        bbox: 人脸检测框 [x1, y1, x2, y2]（原图坐标）。
        size: 输出正方形边长。

    Returns:
        JPEG 字节。

    Raises:
        ValueError: bbox 非法（退化为零面积等）。
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
        face = img.crop(box).resize((size, size), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        face.convert("RGB").save(buf, format="JPEG", quality=FACE_CROP_QUALITY)
        return buf.getvalue()


def _face_area(face: dict) -> float:
    """人脸框面积（用于挑画面主体；bbox 缺失/非法按 0 处理）。"""
    bbox = face.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return 0.0
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


async def face_avatar(image_bytes: bytes, size: int = FACE_CROP_SIZE) -> bytes | None:
    """从照片里裁出人像头像；无人脸 / 子服务不可用时返回 None（调用方回退整张照片）。

    Args:
        image_bytes: 归一化后的图片字节（JPEG）。
        size: 输出正方形边长。

    Returns:
        正方形人像 JPEG 字节；未检测到人脸、子服务未配置/异常、裁剪失败均返回 None。
    """
    try:
        result = await face_client.embed(image_bytes)
    except FaceServiceHttpError as e:
        # 404 = 这张图里没有检出人脸（正常结果）；其它状态码记日志后同样回退
        if e.status_code != 404:
            logger.warning(f"人像提取：人脸子服务返回 {e.status_code}，回退整张照片")
        return None
    except FaceServiceUnavailableError as e:
        logger.info(f"人像提取：人脸子服务不可用（{e}），回退整张照片")
        return None

    faces = [f for f in (result.get("faces") or []) if isinstance(f, dict)]
    if not faces:
        return None
    # 取最大的人脸当主体（子服务已过滤低置信度人脸）
    best = max(faces, key=_face_area)
    try:
        return await asyncio.to_thread(crop_face, image_bytes, best.get("bbox") or [], size)
    except Exception as exc:  # noqa: BLE001 —— 坐标异常等一律回退整张照片
        logger.warning(f"人像提取：裁剪失败（{exc}），回退整张照片")
        return None
