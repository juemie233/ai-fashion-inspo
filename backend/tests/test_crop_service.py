"""手机图裁剪服务测试：扫描候选、按勾选执行裁剪、黑边检测、截图特征、内容边界检测、范围过滤。"""

from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from app.config import settings
from app.services.crop_service import (
    _probe_size,
    crop_image_to_temp,
    detect_content_bounds,
    detect_photo_band,
    detect_screenshot_features,
    screenshot_confidence,
)


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


def _make_status_bar_screenshot(width=300, height=600, status_bar=25, player_bar=60):
    """构造「状态栏+播放器条」截图：顶部深色状态栏 + 中间噪点内容 + 底部黑色播放器条。"""
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[:status_bar, :] = (60, 60, 60)
    arr[height - player_bar :, :] = (10, 10, 10)
    rng = np.random.default_rng(7)
    content = arr[status_bar : height - player_bar]
    content[:] = rng.integers(0, 256, size=content.shape, dtype=np.uint8)
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
    """调用扫描候选接口。"""
    body = {"mode": "ratio", "crop_top": 0.05, "crop_bottom": 0.05}
    body.update(overrides)
    r = client.post("/api/admin/crop-phone-screenshots/scan", json=body)
    assert r.status_code == 200, r.text
    return r.json()


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


def test_scan_lists_candidates(client):
    """扫描接口：列出竖屏截图候选（含尺寸/比例/裁剪信息），只读不修改。"""
    data, ctype = _make_vertical_screenshot()  # 300x600
    insp = _upload_screenshot(client, data, ctype)

    body = _scan(client, mode="ratio", crop_top=0.05, crop_bottom=0.05)
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == insp["id"]
    assert (item["width"], item["height"]) == (300, 600)
    assert item["ratio"] == 2.0
    assert item["crop_top"] == 0.05
    assert item["crop_bottom"] == 0.05
    assert item["auto_ok"] is True
    # 扫描不修改文件与备份
    assert _file_size(insp["id"], client)[1] == 600
    assert _backup_count() == 0


def test_scan_auto_mode_detects_black_band(client):
    """自动模式扫描：黑边检测结果写入裁剪比例。"""
    data, ctype = _make_vertical_screenshot(top_black=40, bottom_black=30)
    insp = _upload_screenshot(client, data, ctype)

    body = _scan(client, mode="auto")
    assert body["total"] == 1
    item = body["items"][0]
    # 40/600 ≈ 0.0667，30/600 = 0.05
    assert abs(item["crop_top"] - 40 / 600) < 0.001
    assert abs(item["crop_bottom"] - 30 / 600) < 0.001


def test_scan_content_mode_detects_gray_band(client):
    """内容边界模式扫描：灰带包夹截图的边界写入裁剪比例并标注类型。"""
    data, ctype = _make_content_band_screenshot(top_gray=100, bottom_gray=130)
    insp = _upload_screenshot(client, data, ctype)

    body = _scan(client, mode="content")
    assert body["total"] == 1
    item = body["items"][0]
    assert item["auto_ok"] is True
    assert abs(item["crop_top"] - 100 / 600) < 0.01
    assert abs(item["crop_bottom"] - 130 / 600) < 0.01
    assert item["boundary_kind"] == "gray_band"
    # 截图特征置信度仍照常计算（与 auto 模式并存，互不干扰）
    assert item["confidence"] in ("high", "medium", "low")


def test_scan_content_mode_detects_status_bar(client):
    """内容边界模式扫描：状态栏+播放器条截图（上下深色地带）也能检出边界。"""
    data, ctype = _make_status_bar_screenshot(status_bar=25, player_bar=60)
    insp = _upload_screenshot(client, data, ctype)

    body = _scan(client, mode="content")
    assert body["total"] == 1
    item = body["items"][0]
    assert item["auto_ok"] is True
    assert abs(item["crop_top"] - 25 / 600) < 0.01
    assert abs(item["crop_bottom"] - 60 / 600) < 0.01


def test_content_mode_status_bar_correction(client):
    """状态栏修正：顶部「低多样度内容簇」（状态栏图标）后移内容上界。"""
    # 顶部 40px：前 10px 纯色背景，行 10~20 为「少量噪点」（模拟状态栏图标，多样度低），
    # 行 21~39 纯色背景，行 40 起全噪点内容区
    width, height = 300, 600
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[:, :] = (200, 200, 200)
    rng = np.random.default_rng(3)
    # 状态栏图标行：每行仅 18 列随机色（96 宽缩放下多样度 ≈ 0.19）
    for y in range(10, 20):
        cols = rng.choice(width, size=18, replace=False)
        arr[y, cols] = rng.integers(0, 256, size=(18, 3), dtype=np.uint8)
    content = arr[40:]
    content[:] = rng.integers(0, 256, size=content.shape, dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    insp = _upload_screenshot(client, buf.getvalue(), "image/jpeg")

    body = _scan(client, mode="content")
    item = body["items"][0]
    assert item["auto_ok"] is True
    # 未修正会裁到行 10（状态栏图标簇）；修正后应裁到行 40（内容区起点）
    assert abs(item["crop_top"] - 40 / 600) < 0.02


def test_apply_content_mode_crops_to_bounds(client):
    """内容边界模式执行裁剪：按检测到的灰带边界裁剪，高度正确缩小。"""
    data, ctype = _make_content_band_screenshot(top_gray=100, bottom_gray=130)
    insp = _upload_screenshot(client, data, ctype)

    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp["id"]], "mode": "content"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 1
    # 600 - 100 - 130 = 370；96 宽缩放的行量化误差 ≤ 3px
    assert abs(_file_size(insp["id"], client)[1] - 370) <= 3


def test_detect_content_bounds_rejects_plain_image(tmp_path):
    """内容边界检测：整图都是内容区（无包夹地带）时拒绝（防误裁普通照片）。"""
    import pytest

    arr = np.zeros((300, 600, 3), dtype=np.uint8)
    rng = np.random.default_rng(5)
    arr[:] = rng.integers(0, 256, size=arr.shape, dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    p = tmp_path / "plain.jpg"
    p.write_bytes(buf.getvalue())

    with pytest.raises(ValueError):
        detect_content_bounds(p)


def test_scan_excludes_non_vertical_and_non_manual(client):
    """扫描过滤：横图不列、非手动上传的竖屏图不列。"""
    buf = BytesIO()
    Image.new("RGB", (600, 300), (200, 200, 200)).save(buf, "JPEG")
    _upload_screenshot(client, buf.getvalue(), "image/jpeg")

    data, ctype = _make_vertical_screenshot()
    _upload_screenshot(client, data, ctype, source_type="scraper")

    body = _scan(client)
    assert body["total"] == 0


def test_apply_crops_selected_only(client):
    """按勾选执行：只处理提交的 ID，裁剪后高度减小、哈希更新、原图备份、登记向量回填。"""
    d1, c1 = _make_vertical_screenshot()  # 300x600
    insp1 = _upload_screenshot(client, d1, c1)
    d2, c2 = _make_vertical_screenshot(bg=(180, 200, 230))  # 不同背景色，避免内容去重
    insp2 = _upload_screenshot(client, d2, c2)
    old_hash1 = _file_size(insp1["id"], client)[2]

    # 只勾选第一个
    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp1["id"]], "mode": "ratio", "crop_top": 0.05, "crop_bottom": 0.05},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 1
    assert body["skipped"] == []
    # 攒批机制：裁剪素材登记进待回填队列，未达阈值（100）时不立即创建任务
    assert body["vector_task_id"] is None

    # 第一个被裁剪：600 → 540；第二个未动：仍 600
    assert _file_size(insp1["id"], client)[1] == 540
    assert _file_size(insp1["id"], client)[2] != old_hash1
    assert _file_size(insp2["id"], client)[1] == 600
    assert _backup_count() == 1


def test_apply_skips_trash_and_missing(client):
    """执行时跳过：垃圾桶中的素材、不存在的 ID 计入跳过明细。"""
    data, ctype = _make_vertical_screenshot()
    insp = _upload_screenshot(client, data, ctype)
    client.post(f"/api/inspirations/{insp['id']}/trash")

    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp["id"], "no-such-id"], "mode": "ratio"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["processed"] == 0
    assert len(body["skipped"]) == 2
    reasons = {s["id"]: s["reason"] for s in body["skipped"]}
    assert "垃圾桶" in reasons[insp["id"]]
    assert "不存在" in reasons["no-such-id"]

    # 跳过明细附带素材文件信息（供前端缩略图展示与素材库定位跳转）
    trashed = next(s for s in body["skipped"] if s["id"] == insp["id"])
    assert trashed["file_path"] and trashed["thumbnail_path"]
    assert trashed["created_at"]
    # 记录不存在的条目只有 id + reason
    missing = next(s for s in body["skipped"] if s["id"] == "no-such-id")
    assert "file_path" not in missing


def test_apply_empty_ids_rejected(client):
    """空 ID 列表返回 400。"""
    r = client.post("/api/admin/crop-phone-screenshots/apply", json={"ids": []})
    assert r.status_code == 400


def test_apply_crop_invalidates_phash_cache(client):
    """裁剪替换文件后感知哈希缓存（近似重复检测用）应置空，扫描时懒重算。"""
    import asyncio
    import sqlite3

    from app.config import settings
    from app.database import async_session
    from app.models.inspiration import Inspiration

    data, ctype = _make_vertical_screenshot()
    insp = _upload_screenshot(client, data, ctype)

    # 预置 phash 缓存（模拟素材此前已被近似重复扫描缓存）
    conn = sqlite3.connect(str(settings.storage_root.parent / "fashion_inspo.db"))
    conn.execute("UPDATE inspirations SET phash=? WHERE id=?", ("ab" * 96, insp["id"]))
    conn.commit()
    conn.close()

    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp["id"]], "mode": "ratio", "crop_top": 0.05, "crop_bottom": 0.05},
    )
    assert r.status_code == 200
    assert r.json()["processed"] == 1

    async def _phash() -> str | None:
        async with async_session() as db:
            row = await db.get(Inspiration, insp["id"])
            return row.phash

    assert asyncio.run(_phash()) is None


def test_apply_duplicate_returns_preview_for_user_decision(client, monkeypatch):
    """裁剪结果与库中素材内容重复：返回 duplicates 对比数据（含预览图），
    不自动丢弃；用户将重复素材移入垃圾桶后再次裁剪即可成功。"""
    import sqlite3

    from app.config import settings

    # A、B 两张不同内容的竖屏截图（避免上传阶段内容去重）
    d1, c1 = _make_vertical_screenshot(bg=(220, 220, 220))
    insp_a = _upload_screenshot(client, d1, c1)
    d2, c2 = _make_vertical_screenshot(bg=(180, 200, 230))
    insp_b = _upload_screenshot(client, d2, c2)

    # 模拟「A 裁剪后的内容与 B 相同」：固定哈希值 + 把 B 的 content_hash 置为同值
    monkeypatch.setattr("app.services.crop_service.file_sha256", lambda _p: "dup-hash")
    db_path = settings.storage_root.parent / "fashion_inspo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE inspirations SET content_hash=? WHERE id=?", ("dup-hash", insp_b["id"])
    )
    conn.commit()
    conn.close()

    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp_a["id"]], "mode": "ratio", "crop_top": 0.05, "crop_bottom": 0.05},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 0
    assert body["skipped"] == []
    assert len(body["duplicates"]) == 1
    dup = body["duplicates"][0]
    assert dup["id"] == insp_a["id"]
    assert dup["dup_id"] == insp_b["id"]
    assert dup["dup_file_path"] and dup["dup_thumbnail_path"]
    assert "内容重复" in dup["reason"]
    # 裁剪结果预览图保留在 storage/_crop_dups/ 下，且未被应用到素材
    preview = settings.storage_root / dup["preview_path"]
    assert preview.exists()
    assert _file_size(insp_a["id"], client)[1] == 600  # A 原图未动

    # 用户决定：把重复素材 B 移入垃圾桶后再彻底删除
    # （active 素材禁止直接物理删除——垃圾桶守卫，先软删可恢复再彻底删除）
    assert (
        client.post(
            f"/api/inspirations/{insp_b['id']}/trash", json={"reason": "重复"}
        ).status_code
        == 200
    )
    r_del = client.delete(f"/api/inspirations/{insp_b['id']}")
    assert r_del.status_code == 204
    # 物理删除：正常列表与垃圾桶均不再包含 B
    assert client.get("/api/inspirations/trash").json()["total"] == 0
    r2 = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp_a["id"]], "mode": "ratio", "crop_top": 0.05, "crop_bottom": 0.05},
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["processed"] == 1
    assert body2["duplicates"] == []
    assert _file_size(insp_a["id"], client)[1] == 540  # 已裁剪


def test_apply_duplicate_rerun_keeps_other_previews(client, monkeypatch):
    """逐组决策时重新 apply 单张素材，不得误删其他组的对比预览（回归测试）。"""
    import sqlite3

    from app.config import settings

    def _fake_hash(path):
        """按素材文件路径返回不同哈希，模拟「裁剪后各自命中不同重复素材」。"""
        p = str(path)
        # 裁剪临时文件与素材同目录同 stem（{stem}_crop_tmp{suffix}），用路径前缀区分
        if p.startswith(str(settings.storage_root / insp_a["file_path"]).rsplit(".", 1)[0]):
            return "hash-a"
        if p.startswith(str(settings.storage_root / insp_c["file_path"]).rsplit(".", 1)[0]):
            return "hash-c"
        return "hash-x"

    monkeypatch.setattr("app.services.crop_service.file_sha256", _fake_hash)

    d1, c1 = _make_vertical_screenshot(bg=(220, 220, 220))
    insp_a = _upload_screenshot(client, d1, c1)
    d2, c2 = _make_vertical_screenshot(bg=(180, 200, 230))
    insp_b = _upload_screenshot(client, d2, c2)
    d3, c3 = _make_vertical_screenshot(bg=(150, 160, 170))
    insp_c = _upload_screenshot(client, d3, c3)
    d4, c4 = _make_vertical_screenshot(bg=(120, 130, 140))
    insp_d = _upload_screenshot(client, d4, c4)

    # 模拟 B 与「A 裁剪结果」重复、D 与「C 裁剪结果」重复
    conn = sqlite3.connect(str(settings.storage_root.parent / "fashion_inspo.db"))
    conn.execute("UPDATE inspirations SET content_hash=? WHERE id=?", ("hash-a", insp_b["id"]))
    conn.execute("UPDATE inspirations SET content_hash=? WHERE id=?", ("hash-c", insp_d["id"]))
    conn.commit()
    conn.close()

    body = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp_a["id"], insp_c["id"]], "mode": "ratio"},
    ).json()
    assert body["processed"] == 0
    assert len(body["duplicates"]) == 2
    c_preview = next(d for d in body["duplicates"] if d["id"] == insp_c["id"])["preview_path"]
    assert (settings.storage_root / c_preview).exists()

    # 用户处理第一组（A）：把 B 移入垃圾桶后彻底删除，再重新 apply 仅 A
    assert (
        client.post(
            f"/api/inspirations/{insp_b['id']}/trash", json={"reason": "重复"}
        ).status_code
        == 200
    )
    assert client.delete(f"/api/inspirations/{insp_b['id']}").status_code == 204
    r2 = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp_a["id"]], "mode": "ratio"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["processed"] == 1

    # 关键断言：C 组的对比预览必须仍然存在（否则弹窗第二组起左侧图 404）
    assert (settings.storage_root / c_preview).exists(), "重新 apply 误删了其他组的对比预览"


def test_crop_invalid_mode_rejected(client):
    """非法模式返回 400。"""
    assert client.post(
        "/api/admin/crop-phone-screenshots/scan", json={"mode": "bogus"}
    ).status_code == 400


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


def test_screenshot_features_high_confidence(tmp_path):
    """手机截图（状态栏+底部手势条）识别为 high 置信度。"""
    p = _make_phone_screenshot(tmp_path)
    features = detect_screenshot_features(p)
    assert features["top_bar"] is True
    assert features["bottom_bar"] is True
    assert screenshot_confidence(features) == "high"


def test_screenshot_features_low_confidence(tmp_path):
    """普通竖图（无截图结构）识别为 low 置信度。"""
    p = _make_plain_vertical(tmp_path)
    features = detect_screenshot_features(p)
    assert screenshot_confidence(features) == "low"


def test_screenshot_features_medium_without_bottom_bar(tmp_path):
    """只有状态栏没有底部手势条 → medium。"""
    p = _make_phone_screenshot(tmp_path)
    img = Image.open(p)
    # 把底部手势条区域填成内容色，去掉底部特征
    px = img.load()
    for y in range(740, 780):
        for x in range(360):
            px[x, y] = ((x * 7) % 256, (y * 3) % 256, 128)
    img.save(p, "JPEG")
    features = detect_screenshot_features(p)
    assert features["top_bar"] is True
    assert features["bottom_bar"] is False
    assert screenshot_confidence(features) == "medium"


# ── 审查修复回归测试 ────────────────────────────────────────────────────────


def test_apply_skips_non_manual_source(client):
    """来源边界（2.2）：非手动上传素材（如 scraper）被跳过，文件与数据库均不动。"""
    data, ctype = _make_vertical_screenshot()
    insp = _upload_screenshot(client, data, ctype, source_type="scraper")

    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp["id"]], "mode": "ratio"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 0
    assert len(body["skipped"]) == 1
    assert "手动上传" in body["skipped"][0]["reason"]
    assert _file_size(insp["id"], client)[1] == 600  # 原图未被裁剪


def test_apply_same_batch_duplicate_detected(client, monkeypatch):
    """同批次去重（2.3）：库中不存在该哈希时，后处理的素材与先成功者重复也须检出。"""
    d1, c1 = _make_vertical_screenshot(bg=(220, 220, 220))
    insp_a = _upload_screenshot(client, d1, c1)
    d2, c2 = _make_vertical_screenshot(bg=(180, 200, 230))
    insp_b = _upload_screenshot(client, d2, c2)
    # 固定哈希模拟「A、B 裁剪结果相同」，且库中（提交前）无此哈希
    monkeypatch.setattr("app.services.crop_service.file_sha256", lambda _p: "same-batch-hash")

    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={
            "ids": [insp_a["id"], insp_b["id"]],
            "mode": "ratio",
            "crop_top": 0.05,
            "crop_bottom": 0.05,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 1  # 第一张成功
    assert len(body["duplicates"]) == 1  # 第二张与第一张重复
    dup = body["duplicates"][0]
    assert dup["id"] == insp_b["id"]
    assert dup["dup_id"] == insp_a["id"]
    assert _file_size(insp_a["id"], client)[1] == 540  # A 已裁剪
    assert _file_size(insp_b["id"], client)[1] == 600  # B 未动
    # B 的裁剪预览保留供对比决策
    assert (settings.storage_root / dup["preview_path"]).exists()


def test_apply_rolls_back_when_post_replace_fails(client, monkeypatch):
    """异常回滚（2.1）：原图替换后生成缩略图抛异常 → 从备份恢复原文件，数据库不变。"""
    from app.services import crop_service

    data, ctype = _make_vertical_screenshot()
    insp = _upload_screenshot(client, data, ctype)
    old_hash = _file_size(insp["id"], client)[2]

    async def _boom(*args, **kwargs):
        raise RuntimeError("缩略图生成失败")

    monkeypatch.setattr(crop_service, "generate_thumbnail", _boom)

    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp["id"]], "mode": "ratio"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 0
    assert len(body["skipped"]) == 1
    assert "处理失败" in body["skipped"][0]["reason"]
    # 磁盘已从备份恢复为原始内容，数据库哈希不变
    assert _file_size(insp["id"], client)[1] == 600
    assert _file_size(insp["id"], client)[2] == old_hash


def test_apply_clears_thumbnail_when_regeneration_fails(client, monkeypatch):
    """缩略图失败（3.1）：重生成失败时置空 thumbnail_path 并删除旧缩略图，避免新旧内容错配。"""
    import asyncio

    from app.database import async_session
    from app.models.inspiration import Inspiration
    from app.services import crop_service

    data, ctype = _make_vertical_screenshot()
    insp = _upload_screenshot(client, data, ctype)

    async def _thumb() -> str | None:
        async with async_session() as db:
            row = await db.get(Inspiration, insp["id"])
            return row.thumbnail_path

    old_thumb = asyncio.run(_thumb())
    assert old_thumb
    assert (settings.storage_root / old_thumb).exists()

    async def _no_thumb(*args, **kwargs):
        return None

    monkeypatch.setattr(crop_service, "generate_thumbnail", _no_thumb)

    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp["id"]], "mode": "ratio", "crop_top": 0.05, "crop_bottom": 0.05},
    )
    assert r.status_code == 200, r.text
    assert r.json()["processed"] == 1
    assert asyncio.run(_thumb()) is None  # thumbnail_path 已置空
    assert not (settings.storage_root / old_thumb).exists()  # 旧缩略图文件已删除
    assert _file_size(insp["id"], client)[1] == 540  # 裁剪本身成功


def test_apply_dedups_repeated_ids(client):
    """重复 ID（4.1）：同一素材重复勾选只处理一次。"""
    data, ctype = _make_vertical_screenshot()
    insp = _upload_screenshot(client, data, ctype)

    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp["id"], insp["id"]], "mode": "ratio"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 1
    assert body["skipped"] == []
    assert _backup_count() == 1  # 只备份一次


def test_apply_skips_path_traversal(client):
    """路径越界（3.5）：file_path 越出存储根时防御性跳过，不访问外部文件。"""
    import sqlite3

    data, ctype = _make_vertical_screenshot()
    insp = _upload_screenshot(client, data, ctype)
    conn = sqlite3.connect(str(settings.storage_root.parent / "fashion_inspo.db"))
    conn.execute(
        "UPDATE inspirations SET file_path=? WHERE id=?", ("../../outside.jpg", insp["id"])
    )
    conn.commit()
    conn.close()

    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp["id"]], "mode": "ratio"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 0
    assert len(body["skipped"]) == 1
    assert "越出存储根" in body["skipped"][0]["reason"]


def test_scan_excludes_non_image_media_type(client):
    """扫描性能（3.2）：media_type 非 image 的素材在 SQL 层直接排除。"""
    import sqlite3

    data, ctype = _make_vertical_screenshot()
    insp = _upload_screenshot(client, data, ctype)
    conn = sqlite3.connect(str(settings.storage_root.parent / "fashion_inspo.db"))
    conn.execute("UPDATE inspirations SET media_type=? WHERE id=?", ("video", insp["id"]))
    conn.commit()
    conn.close()

    body = _scan(client)
    assert body["total"] == 0


def test_probe_size_rotates_by_exif_orientation(tmp_path):
    """EXIF 尺寸（2.6）：Orientation=6 的图片返回旋转后的显示尺寸（与裁剪基准一致）。"""
    img = Image.new("RGB", (600, 300), (200, 200, 200))
    exif = Image.Exif()
    exif[0x0112] = 6
    p = tmp_path / "rotated.jpg"
    img.save(p, "JPEG", exif=exif)

    assert _probe_size(p) == (300, 600)


def test_scan_lists_exif_rotated_candidate(client):
    """EXIF 旋转图（2.6）：显示尺寸为竖屏时列入候选，避免比例误判。"""
    from io import BytesIO

    img = Image.new("RGB", (600, 300), (200, 200, 200))
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = BytesIO()
    img.save(buf, "JPEG", exif=exif)
    _upload_screenshot(client, buf.getvalue(), "image/jpeg")

    body = _scan(client, mode="ratio")
    assert body["total"] == 1
    item = body["items"][0]
    assert (item["width"], item["height"]) == (300, 600)
    assert item["ratio"] == 2.0


def test_crop_image_to_temp_fallback_jpeg_suffix(tmp_path, monkeypatch):
    """降级保存（2.5）：PIL 无法写回原格式时降级 JPEG，返回 .jpg 后缀且内容为 JPEG。"""
    from PIL import Image as PILImage

    img = Image.new("RGB", (200, 400), (220, 220, 220))
    p = tmp_path / "shot.png"
    img.save(p, "PNG")

    real_save = PILImage.Image.save

    def _flaky_save(self, fp, format=None, **kwargs):
        if format == "PNG":
            raise ValueError("无法写回 PNG")
        return real_save(self, fp, format=format, **kwargs)

    monkeypatch.setattr(PILImage.Image, "save", _flaky_save)
    tmp = crop_image_to_temp(p, 0.05, 0.05)
    try:
        assert tmp.suffix == ".jpg"
        with PILImage.open(tmp) as f:
            assert f.format == "JPEG"
    finally:
        tmp.unlink(missing_ok=True)


def test_crop_image_to_temp_cleans_tmp_on_failure(tmp_path):
    """临时文件清理（2.4）：裁剪比例非法时不留残留临时文件。"""
    img = Image.new("RGB", (200, 400), (220, 220, 220))
    p = tmp_path / "shot.png"
    img.save(p, "PNG")

    before = set(tmp_path.iterdir())
    try:
        crop_image_to_temp(p, 0.9, 0.9)  # 合计 ≥ 高度，抛 ValueError
        assert False, "应抛出 ValueError"
    except ValueError:
        pass
    assert set(tmp_path.iterdir()) == before  # 无残留临时文件


def test_scan_ratio_params_sum_validation():
    """比例合计校验（4.3）：ratio 模式 crop_top + crop_bottom ≥ 1 时入口即抛（防御直调场景）。"""
    import asyncio

    from app.database import async_session
    from app.services.crop_service import scan_candidates

    async def _run() -> str | None:
        async with async_session() as db:
            try:
                await scan_candidates(db, mode="ratio", crop_top=0.6, crop_bottom=0.6)
                return None
            except ValueError as e:
                return str(e)

    msg = asyncio.run(_run())
    assert msg and "合计必须" in msg
