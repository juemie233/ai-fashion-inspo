"""手机图裁剪服务测试：一键裁剪竖屏截图、黑边检测、范围过滤。"""

import sqlite3
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.config import settings
from app.services.crop_service import crop_phone_screenshots, detect_photo_band


def _make_vertical_screenshot(width=300, height=600, top_black=40, bottom_black=30):
    """构造竖屏手机截图：浅灰内容 + 顶部/底部黑色条带（模拟状态栏/导航栏）。"""
    img = Image.new("RGB", (width, height), (220, 220, 220))
    for y in range(top_black):
        for x in range(width):
            img.putpixel((x, y), (10, 10, 10))
    for y in range(height - bottom_black, height):
        for x in range(width):
            img.putpixel((x, y), (10, 10, 10))
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


def _file_size(insp_id, client):
    """从数据库读取素材文件的 (宽, 高, content_hash)。"""
    from app.database import async_session
    from app.models.inspiration import Inspiration

    import asyncio

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


def test_crop_phone_screenshots_ratio_mode(client):
    """固定比例模式：竖屏截图裁剪后高度减小、哈希更新、原图备份。"""
    data, ctype = _make_vertical_screenshot()  # 300x600
    insp = _upload_screenshot(client, data, ctype)
    old_hash = _file_size(insp["id"], client)[2]

    r = client.post(
        "/api/admin/crop-phone-screenshots",
        json={"mode": "ratio", "crop_top": 0.05, "crop_bottom": 0.05},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scanned"] == 1
    assert body["processed"] == 1
    assert body["skipped"] == []

    # 高度 600 → 600 * (1 - 0.05 - 0.05) = 540
    w, h, new_hash = _file_size(insp["id"], client)
    assert w == 300
    assert h == 540
    assert new_hash and new_hash != old_hash
    # 原图已备份
    assert _backup_count() == 1


def test_crop_phone_screenshots_auto_mode_detects_black_band(client):
    """自动模式：黑边检测识别出顶部 40px / 底部 30px 黑色条带。"""
    data, ctype = _make_vertical_screenshot(top_black=40, bottom_black=30)
    insp = _upload_screenshot(client, data, ctype)

    r = client.post(
        "/api/admin/crop-phone-screenshots",
        json={"mode": "auto"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 1

    # 裁剪后高度 ≈ 600 - 40 - 30 = 530（检测为像素边界，取整容差 ±2）
    _, h, _ = _file_size(insp["id"], client)
    assert abs(h - 530) <= 2


def test_crop_skips_non_vertical_and_non_manual(client):
    """范围过滤：横图不处理、非手动上传的竖屏图不处理。"""
    # 横图（比例 0.5 < 1.75）
    buf = BytesIO()
    Image.new("RGB", (600, 300), (200, 200, 200)).save(buf, "JPEG")
    _upload_screenshot(client, buf.getvalue(), "image/jpeg")

    # scraper 来源的竖屏截图
    data, ctype = _make_vertical_screenshot()
    _upload_screenshot(client, data, ctype, source_type="scraper")

    r = client.post(
        "/api/admin/crop-phone-screenshots",
        json={"mode": "ratio", "crop_top": 0.05, "crop_bottom": 0.05},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["scanned"] == 0
    assert body["processed"] == 0


def test_crop_skips_trash(client):
    """垃圾桶中的竖屏截图不处理。"""
    data, ctype = _make_vertical_screenshot()
    insp = _upload_screenshot(client, data, ctype)
    assert client.post(f"/api/inspirations/{insp['id']}/trash").status_code == 200

    r = client.post(
        "/api/admin/crop-phone-screenshots",
        json={"mode": "ratio", "crop_top": 0.05, "crop_bottom": 0.05},
    )
    assert r.status_code == 200
    assert r.json()["processed"] == 0


def test_crop_invalid_mode_rejected(client):
    """非法模式返回 400。"""
    r = client.post(
        "/api/admin/crop-phone-screenshots", json={"mode": "bogus"}
    )
    assert r.status_code == 400


def test_detect_photo_band_bounds(tmp_path):
    """黑边检测：返回内容条带的上下边界（含端点）。"""
    img = Image.new("RGB", (200, 400), (220, 220, 220))
    for y in range(50):
        for x in range(200):
            img.putpixel((x, y), (10, 10, 10))
    for y in range(360, 400):
        for x in range(200):
            img.putpixel((x, y), (10, 10, 10))
    p = tmp_path / "shot.jpg"
    img.save(p, "JPEG")

    top, bottom = detect_photo_band(p)
    assert top == 50
    assert bottom == 359


def test_detect_photo_band_full_content_raises(tmp_path):
    """主体占满全图（无黑边）时抛出 ValueError。"""
    img = Image.new("RGB", (200, 400), (220, 220, 220))
    p = tmp_path / "full.jpg"
    img.save(p, "JPEG")
    try:
        detect_photo_band(p)
        assert False, "应抛出 ValueError"
    except ValueError as e:
        assert "无黑边" in str(e)
