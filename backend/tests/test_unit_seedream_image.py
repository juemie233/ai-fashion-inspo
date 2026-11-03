"""云端出图预处理单测：EXIF 方向校正、透明背景贴白底（2026-11-03 评审 P2-6）。"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.services.task_runners.image_gen import _prepare_ark_image


def _decode(data: bytes) -> Image.Image:
    image = Image.open(BytesIO(data))
    image.load()
    return image


def test_prepare_applies_exif_orientation():
    """手机竖拍（EXIF Orientation=6）转正后宽高应交换，而不是输出横图。"""
    source = Image.new("RGB", (40, 20), "red")
    exif = Image.Exif()
    exif[274] = 6  # Orientation: rotate 90 CW
    buf = BytesIO()
    source.save(buf, format="JPEG", exif=exif)

    prepared = _decode(_prepare_ark_image(buf.getvalue(), output_format="jpeg"))
    assert prepared.size == (20, 40)


def test_prepare_flattens_transparency_to_white():
    """透明线稿/抠图转 RGB 时贴白底，不能变成黑底。"""
    source = Image.new("RGBA", (8, 8), (255, 0, 0, 0))  # 全透明
    buf = BytesIO()
    source.save(buf, format="PNG")

    prepared = _decode(_prepare_ark_image(buf.getvalue(), output_format="jpeg")).convert("RGB")
    red, green, blue = prepared.getpixel((0, 0))
    assert red > 240 and green > 240 and blue > 240, (red, green, blue)


def test_prepare_resizes_long_side():
    """长边超过 max_side 时等比缩小。"""
    source = Image.new("RGB", (4000, 2000), "beige")
    buf = BytesIO()
    source.save(buf, format="PNG")

    prepared = _decode(_prepare_ark_image(buf.getvalue(), output_format="png"))
    assert max(prepared.size) <= 2048
    assert prepared.size[0] / prepared.size[1] == 2.0
