"""手机图裁剪服务测试：扫描候选、按勾选执行裁剪、黑边检测、截图特征、范围过滤。"""

from io import BytesIO
from pathlib import Path

from PIL import Image

from app.config import settings
from app.services.crop_service import (
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
    """按勾选执行：只处理提交的 ID，裁剪后高度减小、哈希更新、原图备份、向量任务入队。"""
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
    assert body["vector_task_id"] is not None  # 自动入队向量回填

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

    # 用户决定：把重复素材 B 移入垃圾桶 → 再次裁剪 A 成功（垃圾桶素材不再算重复）
    tr = client.post(
        "/api/inspirations/batch-trash",
        json={"ids": [insp_b["id"]], "reason": "重复"},
    )
    assert tr.status_code == 200
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

    # 用户处理第一组（A）：B 入垃圾桶后重新 apply 仅 A
    client.post("/api/inspirations/batch-trash", json={"ids": [insp_b["id"]], "reason": "重复"})
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
