"""送往人脸识别子服务的图片准备：格式归一 + 视频取首帧缩略图。

为什么需要：子服务用 ``cv2.imdecode`` 解码，素材库里除了 jpg/png/webp 还有**视频**
（实测最近 400 条 f2 素材里 31 条是 mp4，占 7.75%）——视频字节送过去必然 400
「无法解析图片，请上传有效的 JPG/PNG 文件」，而博主人脸注册会把这个 400 当成
**整次注册失败**。

两条规则：
  1. **视频素材用导入时生成的首帧缩略图**：海报帧就是画面内容，照样能检出人脸
  2. 图片统一重编码为 JPEG：子服务声明只吃 JPG/PNG，库里还有 webp（抖音图片）等
     格式；重编码保证「只要 PIL 能打开，人脸服务就能解码」

不做像素级处理（不缩放、不按 EXIF 旋转）：检测结果里的 bbox 是**送检图片**坐标系，
后续按它裁脸/画框必须与送检图一致；cv2 同样不认 EXIF，两边口径保持一致。
"""

from __future__ import annotations

import asyncio
import io
import logging

from PIL import Image

logger = logging.getLogger(__name__)

"""JPEG 重编码质量：人脸检测只需结构信息，92 足够且文件不大。"""
JPEG_QUALITY = 92


def normalize_image(data: bytes) -> bytes | None:
    """把任意 PIL 可解码的图片转成 JPEG 字节；解不了返回 None。

    Args:
        data: 原始图片字节（jpg/png/webp/gif/带透明通道的 png 等）。

    Returns:
        JPEG 字节；空数据或无法解码时返回 None（由调用方跳过该张并给提示，不抛错）。
    """
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.mode in ("RGBA", "LA", "P"):
                # JPEG 不支持透明通道：先合成到白底，避免保存报错或出现黑块
                rgba = image.convert("RGBA")
                canvas = Image.new("RGB", rgba.size, (255, 255, 255))
                canvas.paste(rgba, mask=rgba.split()[-1])
                frame = canvas
            else:
                frame = image.convert("RGB")
            buffer = io.BytesIO()
            frame.save(buffer, format="JPEG", quality=JPEG_QUALITY)
            return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 —— 任何解码/编码问题都按「这张用不了」处理
        logger.debug("图片归一化失败（按跳过处理）：%s", exc)
        return None


async def load_inspiration_image(inspiration) -> tuple[bytes | None, str]:
    """读取素材里可用于人脸识别的图片并归一化（视频取首帧缩略图）。

    Args:
        inspiration: ``Inspiration`` 行（需要 file_path / thumbnail_path / media_type）。

    Returns:
        ``(JPEG 字节, 警告文案)``：成功时警告为空串；失败时字节为 None，警告说明原因
        （调用方据此跳过该素材并把它写进面向用户的 warnings，而不是让整次调用失败）。
    """
    from app.config import settings

    is_video = (inspiration.media_type or "") == "video"
    if is_video and not inspiration.thumbnail_path:
        return None, "视频素材缺少首帧缩略图，已跳过"
    rel_path = (
        inspiration.thumbnail_path
        if is_video
        else (inspiration.file_path or inspiration.thumbnail_path)
    )
    label = "视频首帧缩略图" if is_video else "素材文件"
    if not rel_path:
        return None, "素材没有可用的图片路径"

    path = settings.storage_root / rel_path
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError as exc:
        return None, f"{label}读取失败（{exc}）"

    normalized = await asyncio.to_thread(normalize_image, data)
    if normalized is None:
        suffix = path.suffix or "无扩展名"
        return None, f"{label}不是可识别的图片格式（{suffix}），已跳过"
    return normalized, ""
