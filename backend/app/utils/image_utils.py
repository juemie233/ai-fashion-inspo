"""图像工具：缩略图生成、主色调提取。"""

from pathlib import Path
from collections import Counter

from PIL import Image

from app.config import settings


def generate_thumbnail(image_path: Path) -> Path | None:
    """为图片生成缩略图。返回缩略图路径，失败则返回 None。"""
    try:
        img = Image.open(image_path)
        img.thumbnail(settings.thumbnail_size, Image.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        thumb_name = f"thumb_{image_path.name}"
        # 缩略图放在原始图片对应的平行目录结构中
        relative = image_path.relative_to(settings.images_dir)
        thumb_path = settings.thumbnails_dir / relative.parent / thumb_name
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(thumb_path, "JPEG", quality=settings.thumbnail_quality)
        return thumb_path
    except Exception:
        return None


def extract_dominant_colors(image_path: Path, n_colors: int = 3) -> list[str]:
    """从图片中提取 N 个主色调。返回十六进制颜色字符串列表。"""
    try:
        img = Image.open(image_path)
        img = img.convert("RGB")
        # 缩小尺寸以提升性能
        img = img.resize((150, 150), Image.LANCZOS)

        # 量化以减少颜色数量
        img_quantized = img.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
        img_quantized = img_quantized.convert("RGB")

        # 统计每种颜色的像素数
        pixels = list(img_quantized.getdata())
        color_counts = Counter(pixels)

        # 取前 N 个颜色，转换为 hex
        dominant = []
        for color, _ in color_counts.most_common(n_colors):
            hex_color = "#{:02X}{:02X}{:02X}".format(*color)
            dominant.append(hex_color)

        return dominant
    except Exception:
        return []
