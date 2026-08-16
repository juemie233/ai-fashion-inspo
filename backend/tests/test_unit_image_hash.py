"""感知哈希单元测试：近似不变性（同图缩放/压缩）、不同内容与不同颜色的区分度。"""

from pathlib import Path

from PIL import Image

from app.utils.image_hash import hamming_distance, perceptual_hash


def _make_structured(size=(64, 64), tint=(255, 255, 255)) -> Image.Image:
    """生成有结构的 RGB 图（左暗右亮 + 中部斜线），tint 控制配色。"""
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            v = 255 if x >= size[0] // 2 else 0
            if abs(x - y) < 3:
                v = 128
            px[x, y] = (v * tint[0] // 255, v * tint[1] // 255, v * tint[2] // 255)
    return img


def test_hash_is_768_bits(tmp_path: Path):
    """默认 hash_size=16 → 768 位（192 个 16 进制字符）。"""
    p = tmp_path / "a.jpg"
    _make_structured().save(p, format="JPEG", quality=90)
    h = perceptual_hash(p)
    assert h is not None and len(h) == 192  # 768 位 / 4


def test_same_image_resized_is_similar(tmp_path: Path):
    """同一构图的不同尺寸/压缩版本，感知哈希距离应很小。"""
    base = _make_structured()
    p1 = tmp_path / "a.jpg"
    p2 = tmp_path / "b.jpg"
    base.save(p1, format="JPEG", quality=90)
    base.resize((48, 48)).resize((64, 64)).save(p2, format="JPEG", quality=80)

    h1 = perceptual_hash(p1)
    h2 = perceptual_hash(p2)
    assert h1 is not None and h2 is not None
    assert hamming_distance(h1, h2) <= 40


def test_different_images_are_far(tmp_path: Path):
    """构图差异明显的两张图，感知哈希距离应较大。"""
    base = _make_structured()
    other = Image.new("RGB", (64, 64), (255, 255, 255))  # 纯白
    p1 = tmp_path / "a.jpg"
    p2 = tmp_path / "b.jpg"
    base.save(p1, format="JPEG", quality=90)
    other.save(p2, format="JPEG", quality=90)

    h1 = perceptual_hash(p1)
    h2 = perceptual_hash(p2)
    assert hamming_distance(h1, h2) > 64


def test_same_shape_different_color_is_far(tmp_path: Path):
    """同构图不同配色（红 vs 蓝）应被区分——验证 RGB 三通道保留颜色信息。"""
    red = _make_structured(tint=(255, 0, 0))
    blue = _make_structured(tint=(0, 0, 255))
    p1 = tmp_path / "red.jpg"
    p2 = tmp_path / "blue.jpg"
    red.save(p1, format="JPEG", quality=90)
    blue.save(p2, format="JPEG", quality=90)

    h1 = perceptual_hash(p1)
    h2 = perceptual_hash(p2)
    assert hamming_distance(h1, h2) > 64


def test_hamming_distance_symmetric():
    """汉明距离：相同为 0，且对称。"""
    assert hamming_distance("0000000000000000", "0000000000000000") == 0
    assert hamming_distance("ffffffffffffffff", "0000000000000000") == 64
    a, b = "0f0f0f0f0f0f0f0f", "f0f0f0f0f0f0f0f0f"
    assert hamming_distance(a, b) == hamming_distance(b, a)


def test_perceptual_hash_invalid_file(tmp_path: Path):
    """非图片文件应返回 None。"""
    p = tmp_path / "not_image.jpg"
    p.write_bytes(b"not an image")
    assert perceptual_hash(p) is None
