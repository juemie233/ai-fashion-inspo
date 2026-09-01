"""感知哈希工具：检测视觉近似重复的图片。

采用差异哈希（dHash，difference hash）——感知哈希的一种：
- 将图片缩放到 (hash_size+1) × hash_size，对 R/G/B 三通道分别做 dHash 再拼接
- 逐行比较相邻像素亮度，左 > 右记 1，每通道得 hash_size² 位，共 3 × hash_size² 位
- 保留颜色信息（避免「同款不同色」被误判），对缩放/压缩/轻微水印不敏感，
  且无需 numpy 等额外依赖

与 SHA-256（精确去重）互补：SHA-256 只识别字节完全相同的文件，
dHash 识别「视觉上一样但字节不同」的近似重复。
"""

from pathlib import Path

from PIL import Image


def perceptual_hash(path: Path, hash_size: int = 16) -> str | None:
    """计算图片的彩色差异哈希（dHash），返回 16 进制字符串。

    参数:
        path: 图片文件路径
        hash_size: 哈希边长（默认 16 → 每通道 256 位，共 768 位）

    返回:
        3 × hash_size² 位的 16 进制字符串；文件不可读/非图片时返回 None
    """
    try:
        with Image.open(path) as img:
            # draft() 让底层解码器（JPEG 的 DCT 缩放）只解码目标尺寸附近的像素，
            # 避免对 3000×4000 大图全尺寸解码——单图耗时可降一个数量级；
            # 非 JPEG 格式（PNG 等无渐进解码）会自动忽略该提示，行为不变。
            img.draft("RGB", (hash_size + 1, hash_size))
            small = img.convert("RGB").resize(
                (hash_size + 1, hash_size), Image.Resampling.LANCZOS
            )
            bits = 0
            # R/G/B 三通道分别计算 dHash 并拼接，保留颜色维度
            for band in small.split():
                # get_flattened_data：Pillow 11.3+ 新接口，getdata 将于 Pillow 14
                # 移除；旧版本回退 getdata（返回逐像素扁平序列，用法一致）
                data = band.get_flattened_data() if hasattr(band, "get_flattened_data") else band.getdata()
                pixels = list(data)
                for y in range(hash_size):
                    row_start = y * (hash_size + 1)
                    for x in range(hash_size):
                        bits <<= 1
                        if pixels[row_start + x] > pixels[row_start + x + 1]:
                            bits |= 1
            total_bits = 3 * hash_size * hash_size
            return f"{bits:0{total_bits // 4}x}"
    except Exception:
        return None


def hamming_distance(h1: str, h2: str) -> int:
    """计算两个等长十六进制感知哈希的汉明距离（不同比特数）。

    距离越小越相似；0 表示完全相同。用于近似重复判定的阈值比较。
    """
    n1 = int(h1, 16)
    n2 = int(h2, 16)
    return (n1 ^ n2).bit_count()
