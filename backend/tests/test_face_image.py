"""人脸识别送检图片准备（app/services/face_image.py）单元测试。

回归背景：子服务用 cv2.imdecode 解码，素材库里的**视频**（mp4）字节送过去必然 400
「无法解析图片，请上传有效的 JPG/PNG 文件」，而博主人脸注册会把它当成整次注册失败。
这里锁死两条规则：视频取首帧缩略图、图片统一转 JPEG；解不了则返回 None 由调用方跳过。
"""

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.config import settings
from app.services.face_image import load_inspiration_image, normalize_image


def _image_bytes(fmt: str, mode: str = "RGB", size: tuple[int, int] = (32, 32)) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, (200, 30, 30) if mode != "RGBA" else (200, 30, 30, 128)).save(
        buffer, format=fmt
    )
    return buffer.getvalue()


def _is_jpeg(data: bytes | None) -> bool:
    return bool(data) and data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"


def test_normalize_image_converts_common_formats_to_jpeg():
    """jpg/png/webp/带透明通道 png 都能转成子服务认的 JPEG。"""
    assert _is_jpeg(normalize_image(_image_bytes("JPEG")))
    assert _is_jpeg(normalize_image(_image_bytes("PNG")))
    assert _is_jpeg(normalize_image(_image_bytes("WEBP")))
    assert _is_jpeg(normalize_image(_image_bytes("PNG", mode="RGBA")))


def test_normalize_image_returns_none_for_undecodable():
    """视频字节 / 空数据 / 乱码：返回 None（调用方跳过该张，不抛错、不让整次调用失败）。"""
    assert normalize_image(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64) is None
    assert normalize_image(b"") is None
    assert normalize_image(b"not an image at all") is None


@pytest.mark.asyncio
async def test_load_inspiration_image_video_uses_thumbnail(tmp_path):
    """视频素材取首帧缩略图：mp4 主文件不送检（送检字节是 JPEG）。"""
    thumb_rel = "thumbnails/video_thumb.jpg"
    thumb_path = settings.storage_root / thumb_rel
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    thumb_path.write_bytes(_image_bytes("JPEG"))

    video_rel = "videos/clip.mp4"
    video_path = settings.storage_root / video_rel
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)

    row = SimpleNamespace(
        media_type="video", file_path=video_rel, thumbnail_path=thumb_rel
    )
    data, warning = await load_inspiration_image(row)

    assert warning == ""
    assert _is_jpeg(data)


@pytest.mark.asyncio
async def test_load_inspiration_image_video_without_thumbnail_warns():
    """视频缺首帧缩略图：明确说原因（而不是把 mp4 送过去换回子服务的 400）。"""
    row = SimpleNamespace(media_type="video", file_path="videos/clip.mp4", thumbnail_path=None)

    data, warning = await load_inspiration_image(row)

    assert data is None
    assert "首帧缩略图" in warning


@pytest.mark.asyncio
async def test_load_inspiration_image_image_format_unrecognized():
    """图片素材本身不是可识别格式（如被误存成别的文件）：跳过并说明格式。"""
    rel = "images/broken.avif"
    path = settings.storage_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"definitely-not-an-image")

    data, warning = await load_inspiration_image(
        SimpleNamespace(media_type="image", file_path=rel, thumbnail_path=None)
    )

    assert data is None
    assert ".avif" in warning


@pytest.mark.asyncio
async def test_load_inspiration_image_missing_file_warns():
    """文件缺失：记警告跳过，不影响其它来源。"""
    data, warning = await load_inspiration_image(
        SimpleNamespace(media_type="image", file_path="images/nope.webp", thumbnail_path=None)
    )

    assert data is None
    assert "读取失败" in warning
