"""感知哈希工具：检测视觉近似重复的图片。

采用差异哈希（dHash，difference hash）——感知哈希的一种：
- 将图片缩放到 (hash_size+1) × hash_size 的灰度图
- 逐行比较相邻像素亮度，左 > 右记 1，得到 hash_size² 位二进制串
- 对缩放、压缩、轻微水印/亮度变化不敏感，且无需 numpy 等额外依赖

与 SHA-256（精确去重）互补：SHA-256 只识别字节完全相同的文件，
dHash 识别「视觉上一样但字节不同」的近似重复。
"""

from pathlib import Path

from PIL import Image


def perceptual_hash(path: Path, hash_size: int = 8) -> str | None:
    """计算图片的差异哈希（dHash），返回 16 进制字符串。

    参数:
        path: 图片文件路径
        hash_size: 哈希边长（默认 8 → 64 位）

    返回:
        hash_size² 位的 16 进制字符串；文件不可读/非图片时返回 None
    """
    try:
        with Image.open(path) as img:
            gray = img.convert("L").resize(
                (hash_size + 1, hash_size), Image.Resampling.LANCZOS
            )
            pixels = list(gray.getdata())
            bits = 0
            for y in range(hash_size):
                row_start = y * (hash_size + 1)
                for x in range(hash_size):
                    bits <<= 1
                    if pixels[row_start + x] > pixels[row_start + x + 1]:
                        bits |= 1
            return f"{bits:0{hash_size * hash_size // 4}x}"
    except Exception:
        return None


def hamming_distance(h1: str, h2: str) -> int:
    """计算两个等长十六进制感知哈希的汉明距离（不同比特数）。

    距离越小越相似；0 表示完全相同。用于近似重复判定的阈值比较。
    """
    n1 = int(h1, 16)
    n2 = int(h2, 16)
    return (n1 ^ n2).bit_count()
