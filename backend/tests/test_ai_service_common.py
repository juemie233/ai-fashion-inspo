"""AI 服务共用辅助 _read_image_base64 的格式转换单测。

覆盖「按实际内容格式（而非扩展名）识别 WebP 并转 JPEG」的修复，
防止 WebP 内容误标 .jpg 扩展名时直接交给 Ollama 导致 400。
"""

import base64
from io import BytesIO

import pytest
from PIL import Image

from app.config import settings
from app.services.ai_service.common import _read_image_base64


def _write_image(rel_path: str, fmt: str, color=(100, 150, 200), size=(32, 32)) -> str:
    """在测试存储目录写入一张图片，返回相对路径。"""
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    full = settings.storage_root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(buf.getvalue())
    return rel_path


def test_read_image_base64_converts_webp_content_mislabeled_jpg():
    """WebP 内容存成 .jpg 扩展名：按实际格式识别并转 JPEG（修复 Ollama 400）。"""
    rel = _write_image("images/webp_as_jpg.jpg", "WEBP")
    image_data, _ = _read_image_base64(rel)
    raw = base64.b64decode(image_data)
    assert raw[:3] == b"\xff\xd8\xff"  # JPEG SOI 魔数


def test_read_image_base64_converts_webp_extension():
    """正常 .webp 扩展名 + WebP 内容：同样转 JPEG。"""
    rel = _write_image("images/real.webp", "WEBP")
    image_data, _ = _read_image_base64(rel)
    raw = base64.b64decode(image_data)
    assert raw[:3] == b"\xff\xd8\xff"


def test_read_image_base64_keeps_jpeg_unchanged():
    """真实 JPEG：保留原字节，不做无谓重新编码。"""
    rel = _write_image("images/real.jpg", "JPEG", color=(10, 20, 30))
    orig = (settings.storage_root / rel).read_bytes()
    image_data, _ = _read_image_base64(rel)
    assert base64.b64decode(image_data) == orig


def test_read_image_base64_keeps_png_unchanged():
    """真实 PNG：保留原字节。"""
    rel = _write_image("images/real.png", "PNG")
    orig = (settings.storage_root / rel).read_bytes()
    image_data, _ = _read_image_base64(rel)
    assert base64.b64decode(image_data) == orig


def test_read_image_base64_rejects_corrupt_image():
    """无法解析的图片：明确报错，而非把损坏字节交给 Ollama。"""
    rel_path = "images/corrupt.jpg"
    full = settings.storage_root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"not-an-image-at-all")
    with pytest.raises(ValueError, match="图片解析失败"):
        _read_image_base64(rel_path)
