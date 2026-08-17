"""图像工具：主色调提取。"""

from collections import Counter
from pathlib import Path

from PIL import Image


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
