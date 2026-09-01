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
        img = img.resize((150, 150), Image.Resampling.LANCZOS)

        # 量化以减少颜色数量
        img_quantized = img.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
        img_quantized = img_quantized.convert("RGB")

        # 统计每种颜色的像素数
        # get_flattened_data：Pillow 11.3+ 新接口，getdata 将于 Pillow 14 移除；
        # 旧版本回退 getdata（均返回逐像素 (r,g,b) 扁平序列，Counter 用法一致）
        data = (
            img_quantized.get_flattened_data()
            if hasattr(img_quantized, "get_flattened_data")
            else img_quantized.getdata()
        )
        pixels = list(data)
        color_counts = Counter(pixels)

        # 取前 N 个颜色，转换为 hex
        dominant = []
        for color, _ in color_counts.most_common(n_colors):
            hex_color = "#{:02X}{:02X}{:02X}".format(*color)
            dominant.append(hex_color)

        return dominant
    except Exception:
        return []
