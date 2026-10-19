"""质量审核前置规则检查（审查新 P1）。

在调用 VLM 之前先用规则拦掉「肉眼可判的硬性垃圾」（尺寸过小 / 近乎纯色 / 极端比例），
省下整轮两次模型调用。误拦代价低——随机复审可 force 覆盖。
"""

import base64
import io

from PIL import Image

from app.services.ai_service.quality import _precheck_image


def _data_url(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def test_precheck_rejects_tiny_image():
    """短边 < 64 → 尺寸过小（low_quality）。"""
    assert _precheck_image(_data_url(Image.new("RGB", (32, 32), "white"))) == "low_quality"


def test_precheck_rejects_solid_color():
    """近乎纯色 → 空图/纯色块（low_quality）。"""
    assert _precheck_image(_data_url(Image.new("RGB", (512, 768), (200, 30, 30)))) == "low_quality"


def test_precheck_rejects_extreme_aspect_ratio():
    """900x100（9:1）→ 极端比例（partial）。用噪声图，避免先命中「纯色」规则。"""
    import random

    rnd = random.Random(7)
    im = Image.new("RGB", (900, 100))
    px = im.load()
    for y in range(100):
        for x in range(900):
            px[x, y] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
    assert _precheck_image(_data_url(im)) == "partial"


def test_precheck_passes_normal_image():
    """正常多色图片 → 不拦截，交给 VLM 判定。"""
    im = Image.new("RGB", (512, 768))
    px = im.load()
    for y in range(768):
        for x in range(0, 512, 8):
            px[x, y] = (x % 256, y % 256, (x + y) % 256)
    assert _precheck_image(_data_url(im)) is None


def test_precheck_tolerates_broken_data():
    """无法解析的数据不拦（交给下游 VLM/读图报错），避免把素材卡死在 pending。"""
    assert _precheck_image("data:image/jpeg;base64,bm90LWFuLWltYWdl") is None
