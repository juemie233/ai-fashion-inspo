"""图像工具（缩略图生成 / 主色调提取）单元测试。"""

from pathlib import Path

from PIL import Image

from app.config import settings
from app.services.file_service import generate_thumbnail
from app.utils.image_utils import extract_dominant_colors


async def test_generate_thumbnail_creates_file():
    """生成缩略图（生产实现 file_service 版）：落在日期目录，返回相对路径，且是有效 JPEG。"""
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    img_path = settings.images_dir / "sample.jpg"
    Image.new("RGB", (100, 100), (200, 100, 50)).save(img_path, "JPEG")

    rel = await generate_thumbnail(img_path)

    assert rel is not None
    assert rel.startswith("thumbnails/")
    thumb = settings.storage_root / rel
    assert thumb.exists()
    assert thumb.name == "thumb_sample.jpg"
    with Image.open(thumb) as im:
        im.verify()  # 有效 JPEG，损坏会抛异常


async def test_generate_thumbnail_invalid():
    """无效文件生成缩略图返回 None。"""
    assert await generate_thumbnail(Path("/no/such/file.jpg")) is None


def test_extract_dominant_colors_solid(tmp_path):
    """纯色图提取主色调：返回该颜色的 hex（PNG 无损，颜色精确）。"""
    p = tmp_path / "red.png"
    Image.new("RGB", (64, 64), (255, 0, 0)).save(p, "PNG")
    assert extract_dominant_colors(p) == ["#FF0000"]


def test_extract_dominant_colors_invalid(tmp_path):
    """非图片文件提取主色调返回空列表。"""
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"not an image")
    assert extract_dominant_colors(p) == []
