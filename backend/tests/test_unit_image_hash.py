"""感知哈希单元测试：近似不变性（同图缩放/压缩）与不同内容的区分度。"""

from pathlib import Path

from PIL import Image

from app.utils.image_hash import hamming_distance, perceptual_hash


def _make_structured(size: tuple[int, int] = (64, 64)) -> Image.Image:
    """生成有结构的灰度图（左黑右白 + 中部斜线），避免纯色导致哈希恒为 0。"""
    img = Image.new("L", size, 0)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            v = 255 if x >= size[0] // 2 else 0
            if abs(x - y) < 3:
                v = 128
            px[x, y] = v
    return img


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
    assert hamming_distance(h1, h2) <= 10


def test_different_images_are_far(tmp_path: Path):
    """构图差异明显的两张图，感知哈希距离应较大。"""
    base = _make_structured()
    other = Image.new("L", (64, 64), 255)  # 纯白
    p1 = tmp_path / "a.jpg"
    p2 = tmp_path / "b.jpg"
    base.save(p1, format="JPEG", quality=90)
    other.save(p2, format="JPEG", quality=90)

    h1 = perceptual_hash(p1)
    h2 = perceptual_hash(p2)
    assert hamming_distance(h1, h2) > 10


def test_hamming_distance_symmetric():
    """汉明距离：相同为 0，且对称。"""
    assert hamming_distance("0000000000000000", "0000000000000000") == 0
    assert hamming_distance("ffffffffffffffff", "0000000000000000") == 64
    a, b = "0f0f0f0f0f0f0f0f", "f0f0f0f0f0f0f0f0"
    assert hamming_distance(a, b) == hamming_distance(b, a)


def test_perceptual_hash_invalid_file(tmp_path: Path):
    """非图片文件应返回 None。"""
    p = tmp_path / "not_image.jpg"
    p.write_bytes(b"not an image")
    assert perceptual_hash(p) is None
