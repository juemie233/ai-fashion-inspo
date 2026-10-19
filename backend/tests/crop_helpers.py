"""test_crop_service 系列共用的构造助手（纯函数：建图 / 上传 / 扫描）。

从 test_crop_service.py 抽出（C-6）：三个测试文件都要用，留在任一测试文件里都会被反向 import。
"""

from io import BytesIO
from pathlib import Path
import numpy as np
from PIL import Image
from app.config import settings


def _make_vertical_screenshot(width=300, height=600, top_black=40, bottom_black=30, bg=(220, 220, 220)):
    """构造竖屏手机截图：背景色内容 + 顶部/底部黑色条带（模拟状态栏/导航栏）。"""
    img = Image.new("RGB", (width, height), bg)
    for y in range(top_black):
        for x in range(width):
            img.putpixel((x, y), (10, 10, 10))
    for y in range(height - bottom_black, height):
        for x in range(width):
            img.putpixel((x, y), (10, 10, 10))
    buf = BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue(), "image/jpeg"


def _make_content_band_screenshot(
    width=300,
    height=600,
    top_gray=100,
    bottom_gray=130,
    top_color=(70, 70, 70),
    bottom_color=(90, 90, 90),
):
    """构造「灰带包夹」截图：上下平坦灰色地带 + 中间彩色噪点内容区。

    噪点行颜色多样度 ≈1.0（内容区），纯色地带多样度 ≈0（灰带），
    边界锐利，供内容边界检测断言使用。
    """
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[:, :] = top_color
    arr[height - bottom_gray :, :] = bottom_color
    rng = np.random.default_rng(42)
    content = arr[top_gray : height - bottom_gray]
    content[:] = rng.integers(0, 256, size=content.shape, dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue(), "image/jpeg"


def _make_status_bar_screenshot(width=300, height=600, status_bar=70, player_bar=60):
    """构造「状态栏+播放器条」截图：顶部深色状态栏（含时间字形）+ 中间噪点内容 + 底部黑色播放器条。

    状态栏高度取 70px（大于全高 10%，使字形检测条带内只有状态栏），并画上
    「时间 + 右侧图标」字形——真实截图的状态栏必有时钟字形，这也是 content
    模式候选资格要求的 UI 证据（深色条带单独不再构成候选）。
    """
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[:status_bar, :] = (60, 60, 60)
    arr[height - player_bar :, :] = (10, 10, 10)
    rng = np.random.default_rng(7)
    content = arr[status_bar : height - player_bar]
    content[:] = rng.integers(0, 256, size=content.shape, dtype=np.uint8)
    # 状态栏字形：时间（两段式）+ 右侧信号/电量图标（x 坐标按宽度等比缩放）
    sx = width / 300.0
    for x0, x1, y0, y1, color in (
        (84, 90, 8, 19, 250),
        (100, 127, 8, 19, 250),
        (250, 263, 9, 18, 240),
        (266, 279, 9, 18, 240),
    ):
        arr[y0:y1, int(x0 * sx) : int(x1 * sx)] = (color, color, color)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue(), "image/jpeg"


def _upload_screenshot(client, data, ctype, **overrides):
    """上传图片素材（默认手动上传）。"""
    r = client.post(
        "/api/inspirations", files={"file": ("shot.jpg", data, ctype)}, data=overrides
    )
    assert r.status_code == 201, r.text
    return r.json()


def _scan(client, **overrides):
    """调用扫描候选接口。

    测试默认关闭 VLM 主判（vlm_review=False）——多数用例验证的是「算法
    检出判定」，不应受 VLM 裁决干扰（VLM 行为由 test_scan_vlm_review_*
    专门用例显式开启验证）。
    """
    body = {
        "mode": "ratio",
        "crop_top": 0.05,
        "crop_bottom": 0.05,
        "vlm_review": False,
    }
    body.update(overrides)
    r = client.post("/api/admin/crop-phone-screenshots/scan", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _scan_all(client, **overrides):
    """扫描候选并自动跟完所有分段，返回 (合并后的候选列表, 最后一次响应)。

    content 模式分「完整截图 / 已裁过」两段扫描（先高产出段，见 crop_service 的
    _STAGE_FULL 说明）。用例验证「候选资格结论」时用它，免得素材落在第二段被
    误判成「没列出来」。
    """
    items: list[dict] = []
    body: dict = {}
    cursor = None
    for _ in range(10):  # 段数有限：10 次足够，同时防实现出错时死循环
        body = _scan(client, cursor=cursor, **overrides) if cursor else _scan(client, **overrides)
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if not body["truncated"] or not cursor:
            break
    return items, body


def _file_size(insp_id, client):
    """从数据库读取素材文件的 (宽, 高, content_hash)。"""
    import asyncio

    from app.database import async_session
    from app.models.inspiration import Inspiration

    async def _get():
        async with async_session() as db:
            insp = await db.get(Inspiration, insp_id)
            full = settings.storage_root / insp.file_path
            with Image.open(full) as im:
                w, h = im.size
            return w, h, insp.content_hash

    return asyncio.run(_get())


def _backup_count():
    """返回裁剪备份目录中的文件数。"""
    backup_root = settings.storage_root / "_crop_backup"
    if not backup_root.exists():
        return 0
    return sum(len(list(d.glob("*"))) for d in backup_root.iterdir() if d.is_dir())


def _make_status_bar_strip_image(textured: bool = False) -> Image.Image:
    """构造合成截图顶部：平滑（或正弦纹理）底 + 时间字形 + 右侧信号图标块。

    时间字形按真实截图形态建模（实测「12:30」渲染为两个连通域：窄块 + 宽块，
    块间有正间隙）；纹理底用正弦波（局部斜率 13/px，低于局部对比阈值 22，
    因此不会自己产生前景，但 20px 邻域灰度方差 ≈15~20 > 背景方差门槛）。
    """
    width, height = 300, 600
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    if textured:
        xs = np.arange(width)
        arr[:, :] = (65 + 25 * np.sin(2 * np.pi * xs / 24.0)).astype(np.uint8)[None, :, None]
    else:
        arr[:, :] = (60, 60, 60)
    arr[12:23, 84:90] = (250, 250, 250)  # 时间窄块（模拟「1」）
    arr[12:23, 100:127] = (250, 250, 250)  # 时间宽块（模拟「2:30」）
    arr[13:22, 250:263] = (240, 240, 240)  # 右侧信号图标
    arr[13:22, 266:279] = (240, 240, 240)  # 右侧电量图标
    return Image.fromarray(arr)


def _bounds_fixture(**overrides) -> dict:
    """构造 analyze_screenshot_combined 的边界结果字典（默认全零）。"""
    base = {
        "top_frac": 0.0,
        "bottom_frac": 0.0,
        "top_edge": 0,
        "bottom_edge": 0,
        "correction": False,
        "kind": "plain",
        "already_cropped": False,
        "residual_top_frac": 0.0,
        "residual_bottom_frac": 0.0,
        "glyph_top_frac": 0.0,
        "glyph_strong": False,
        "glyph_found": False,
    }
    base.update(overrides)
    return base


def _make_phone_screenshot(tmp_path: Path) -> Path:
    """构造标准手机截图：顶部状态栏（纯色背景+图标行）+ 彩色内容 + 底部手势条。"""
    img = Image.new("RGB", (360, 780), (40, 40, 40))  # 19.5:9
    # 状态栏：纯黑背景，第 2 行放白色图标
    for x in range(360):
        img.putpixel((x, 0), (20, 20, 20))
        img.putpixel((x, 1), (20, 20, 20))
        if x % 9 == 0:
            img.putpixel((x, 1), (255, 255, 255))
    # 内容区：彩色块（模拟照片）
    for y in range(40, 740):
        for x in range(360):
            img.putpixel((x, y), ((x * 7) % 256, (y * 3) % 256, 128))
    # 底部手势条：黑色窄条
    for y in range(740, 780):
        for x in range(360):
            img.putpixel((x, y), (15, 15, 15))
    p = tmp_path / "screenshot.jpg"
    img.save(p, "JPEG")
    return p


def _make_plain_vertical(tmp_path: Path) -> Path:
    """构造普通竖图：整图渐变/纯色（无状态栏/手势条结构）。"""
    img = Image.new("RGB", (360, 780), (200, 200, 200))
    for y in range(780):
        for x in range(360):
            img.putpixel((x, y), (y % 40 + 180, 180, 180))  # 顶部稍深、整体近纯色
    p = tmp_path / "plain.jpg"
    img.save(p, "JPEG")
    return p


def _make_gradient_top_screenshot(width=300, height=600, grad_rows=20):
    """构造「照片顶部缓升渐变」图：顶部 grad_rows 行多样度从 ~0.02 渐升到 ~0.12，
    下方为噪点内容区（模拟照片暗角/天空渐变 + 主体，无任何系统 UI）。"""
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    rng = np.random.default_rng(31)
    arr[grad_rows:] = rng.integers(0, 256, size=arr[grad_rows:].shape, dtype=np.uint8)
    # 渐变行：每行叠加递增数量的随机色像素（行 y 约 4 + 2y 个 → 96 宽缩放下多样度 0.02→0.14）
    for y in range(grad_rows):
        n = 4 + y * 2
        cols = rng.choice(width, size=n, replace=False)
        arr[y, cols] = rng.integers(0, 256, size=(n, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue(), "image/jpeg"


def _make_narrow_top_band_screenshot(width=300, height=600, band_rows=2):
    """构造「顶部极窄低多样度地带」图（0.8% 场景）：顶部 band_rows 行纯色 + 噪点内容。"""
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[:band_rows] = (70, 70, 70)
    rng = np.random.default_rng(17)
    arr[band_rows:] = rng.integers(0, 256, size=arr[band_rows:].shape, dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue(), "image/jpeg"
