"""裁剪服务主流程测试：上传→扫描→应用、去重、备份与图片处理。"""

from io import BytesIO

import numpy as np
import pytest
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
from app.services.image_cropping import _right_icon_supports

from crop_helpers import (
    _make_vertical_screenshot,
    _make_content_band_screenshot,
    _upload_screenshot,
    _file_size,
    _backup_count,
    _make_phone_screenshot,
    _make_plain_vertical,
)


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


def test_detect_content_bounds_marks_cropped(tmp_path):
    """内容边界检测：整图都是内容区（无包夹地带）时标记 already_cropped（防误裁普通照片）。"""
    arr = np.zeros((300, 600, 3), dtype=np.uint8)
    rng = np.random.default_rng(5)
    arr[:] = rng.integers(0, 256, size=arr.shape, dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    p = tmp_path / "plain.jpg"
    p.write_bytes(buf.getvalue())

    r = detect_content_bounds(p)
    assert r["already_cropped"] is True
    assert r["top_frac"] + r["bottom_frac"] < 0.01


def test_right_icon_supports_requires_smooth_same_band_icon():
    """单块时间回退的右侧印证：右区同带、低背景方差的图标块才算印证。"""
    time_blob = (100, 140, 15, 30, 400, 0.5)
    assert _right_icon_supports(time_blob, [time_blob], 320) is False
    noisy = (240, 300, 14, 31, 500, 30.0)  # 右侧块但背景纹理重
    assert _right_icon_supports(time_blob, [time_blob, noisy], 320) is False
    off_band = (240, 300, 50, 60, 500, 1.0)  # 右侧块但垂直不同带
    assert _right_icon_supports(time_blob, [time_blob, off_band], 320) is False
    icon = (240, 300, 14, 31, 500, 0.8)  # 同带 + 平滑 → 成立
    assert _right_icon_supports(time_blob, [time_blob, icon], 320) is True


def test_status_bar_correction_top_residual():
    """状态栏残留修正（单元级）：顶格低多样度簇 → 后随更高内容时给出疑似裁剪建议。

    残留估算携带 ui_evidence=True（真实残留场景均为有状态栏/导航栏特征的
    截图）；无截图证据时从严（_RESIDUAL_TAIL_JUMP_STRICT），防照片渐变误报。
    """
    from app.services.crop_service import _residual_top_estimate, _status_bar_correction

    # 场景 1：顶格残留（行 0~1 d≈0.19~0.20）后随内容区（d≈0.30+）→ 疑似建议 2 行
    d1 = np.array([0.19, 0.20] + [0.30, 0.31, 0.29, 0.33, 0.32] * 30)
    assert _residual_top_estimate(d1, ui_evidence=True) == 2

    # 场景 1b：顶格残留较厚（5 行，d 0.16~0.27，透明状态栏图标下沿）
    # 后随内容区（d≈0.36）→ 疑似建议 5 行（实测 51e564d6 类需裁 5 行才干净）
    d1b = np.array([0.16, 0.20, 0.24, 0.27, 0.25] + [0.36] * 80)
    assert _residual_top_estimate(d1b, ui_evidence=True) == 5

    # 场景 2：顶格但内容直接开始（d 高，无残留）→ 无疑似建议
    d2 = np.array([0.30, 0.31, 0.29, 0.33, 0.32] * 32)
    assert _residual_top_estimate(d2, ui_evidence=True) == 0

    # 场景 3：顶格低多样度簇但后随内容不高（可能是照片暗部，非残留）→ 无疑似建议
    d3 = np.array([0.19, 0.20, 0.21, 0.20, 0.22, 0.21] * 30)
    assert _residual_top_estimate(d3, ui_evidence=True) == 0

    # 场景 4：未裁截图（图标行从行 12 起，前有纯色背景，n≈200 真实规模）→ 修正到内容区起点
    d4 = np.array([0.02] * 12 + [0.18, 0.19, 0.20, 0.21, 0.20] + [0.30, 0.31] * 90)
    assert _status_bar_correction(d4, 12) == 17

    # 场景 5：顶格残留不并入自动裁剪（top_edge=0 时修正函数不后移边界）
    assert _status_bar_correction(d1, 0) == 0

    # 场景 6：内容抬升量区分有无截图证据——条带 first_med=0.19，tail 抬升
    # 0.07（介于残留门槛 0.06 与照片门槛 0.08 之间）：有证据给出建议，
    # 无证据（防照片渐变误报）拒绝
    d6 = np.array([0.19] * 12 + [0.26] * 148)
    assert _residual_top_estimate(d6, ui_evidence=True) == 12
    assert _residual_top_estimate(d6, ui_evidence=False) == 0

    # 场景 7：饱和度通道——条带高饱和（照片渐变/天空）即便形态像残留也拒绝
    d5 = np.array([0.16, 0.20, 0.24, 0.27, 0.25] + [0.36] * 80)
    sat_low = np.full_like(d5, 0.03)
    sat_high = np.full(84 if len(d5) == 84 else len(d5), 0.35)
    assert _residual_top_estimate(d5, sat_low, ui_evidence=True) == 5  # 低饱和通过
    assert _residual_top_estimate(d5, sat_high, ui_evidence=True) == 0


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
    assert _file_size(insp_a["id"], client)[1] == 540


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
    with pytest.raises(ValueError, match="无黑边"):
        detect_photo_band(p)


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
    assert _file_size(insp["id"], client)[1] == 600


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
    assert _file_size(insp["id"], client)[1] == 540


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
    assert _backup_count() == 1


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


def test_probe_size_rotates_by_exif_orientation(tmp_path):
    """EXIF 尺寸（2.6）：Orientation=6 的图片返回旋转后的显示尺寸（与裁剪基准一致）。"""
    img = Image.new("RGB", (600, 300), (200, 200, 200))
    exif = Image.Exif()
    exif[0x0112] = 6
    p = tmp_path / "rotated.jpg"
    img.save(p, "JPEG", exif=exif)

    assert _probe_size(p) == (300, 600)


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
    with pytest.raises(ValueError):  # 合计 ≥ 高度
        crop_image_to_temp(p, 0.9, 0.9)
    assert set(tmp_path.iterdir()) == before


def test_ui_band_valid_structure(tmp_path):
    """地带有效性判定（单元级）：纯色带通过；缓升渐变、无突变峰的起伏被拒。"""
    from app.services.image_cropping import _ui_band_valid

    # 纯色地带（导航栏/灰带）：通过
    d1 = np.array([0.01] * 8 + [0.5] * 40)
    assert _ui_band_valid(d1, 0, 8, 8) is True
    # 缓升渐变（暗角）：前后半段中位差 ≥ 0.05 → 拒绝
    d2 = np.array([0.02, 0.04, 0.06, 0.08, 0.10, 0.12] + [0.5] * 40)
    assert _ui_band_valid(d2, 0, 6, 6) is False
    # 状态栏结构（纯色背景 + 突变图标峰）：通过
    d3 = np.array([0.01] * 4 + [0.02, 0.18, 0.15] + [0.5] * 40)
    assert _ui_band_valid(d3, 0, 7, 7) is True
    # 过窄地带（1 行）：拒绝
    d4 = np.array([0.01, 0.5] + [0.5] * 40)
    assert _ui_band_valid(d4, 0, 1, 1) is False


def test_strip_jpeg_bytes_slices_top_and_bottom(tmp_path):
    """条带裁切（VLM 复核输入）：顶部 12% / 底部 15%，放大到 768 宽。"""
    from app.services.crop_service import _strip_jpeg_bytes

    p = tmp_path / "strip.jpg"
    Image.new("RGB", (200, 400), (128, 128, 128)).save(p)

    top = _strip_jpeg_bytes(p, top=True)  # 顶部 12% = 48px
    bottom = _strip_jpeg_bytes(p, top=False)  # 底部 15% = 60px
    for raw in (top, bottom):
        assert len(raw) > 0
    with Image.open(BytesIO(top)) as im:
        assert im.size[0] == 768
        assert im.size[1] == 48 * 768 // 200
    with Image.open(BytesIO(bottom)) as im:
        assert im.size[0] == 768
        assert im.size[1] == 60 * 768 // 200


def test_parse_bar_answer_variants():
    """VLM 条带回答解析（三态）：JSON 优先，兼容 markdown 围栏与空格变体，
    垃圾输入/空响应返回 None（未知）——不能把解析失败当成「明确阴性」。"""
    from app.services.crop_service import _parse_bar_answer

    assert _parse_bar_answer('{"bar": true}') is True
    assert _parse_bar_answer('{"bar": false}') is False
    assert _parse_bar_answer('```json\n{"bar": true}\n```') is True
    assert _parse_bar_answer('{ "bar" : true }') is True
    assert _parse_bar_answer('{"bar": "是"}') is None  # 非布尔值 → 未知
    assert _parse_bar_answer("BAR TRUE") is None  # 无引号/冒号结构，不误判
    assert _parse_bar_answer("") is None


def test_content_bounds_gap_uses_first_content_row():
    """内容区有缺口时，上界取该段第一个内容行，而不是落进缺口内部。"""
    from app.services.image_cropping_content import _content_bounds

    d = np.zeros(30, dtype=np.float32)
    d[[10, 13, 14]] = 0.30
    top, bottom = _content_bounds(d)
    assert top == 10
    assert bottom == 14


def test_row_quantization_matches_between_profiles_and_diversity():
    """两套行多样度实现必须同口径：纯白 255 也只落 15 档（不再多出第 17 档）。"""
    from PIL import Image
    from app.services.image_cropping_content import (
        _profiles_from_small,
        _row_diversity,
    )

    img = Image.new("RGB", (96, 20), (255, 255, 255))
    _, _, diversity = _profiles_from_small(img)
    row = _row_diversity(img, 96)
    assert np.allclose(diversity, row)


def test_glyph_top_frac_uses_real_strip_ratio_on_short_image():
    """矮图条带被 max(12, H*0.1) 抬高时，建议比例要按真实条带/全高换算。"""
    from app.services.image_cropping_glyph import _glyph_top_frac

    # 100px 高图：条带 12px，字形底行 5 → 建议约 7/100，而不是 7/12*0.1
    assert abs(_glyph_top_frac(5, 12, 100) - 0.07) < 1e-9


def test_crop_rejects_animated_gif(tmp_path):
    """动图裁剪会丢帧：入口直接拒绝，不产生临时文件。"""
    from PIL import Image

    frames = [Image.new("RGB", (20, 20), color) for color in ((255, 0, 0), (0, 255, 0))]
    p = tmp_path / "anim.gif"
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=100, loop=0)

    with pytest.raises(ValueError, match="动图"):
        crop_image_to_temp(p, 0.1, 0.1)
    assert not list(tmp_path.glob("*_crop_*"))


def test_save_cropped_image_keeps_icc_profile(tmp_path):
    """WebP 裁剪保留 ICC，避免 iPhone P3 照片裁剪后偏色。"""
    from PIL import Image
    from app.services.image_cropping import _save_cropped_image

    src_path = tmp_path / "src.webp"
    Image.new("RGB", (20, 20), (180, 40, 30)).save(
        src_path, "WEBP", quality=90, icc_profile=b"fake-icc"
    )
    with Image.open(src_path) as src:
        cropped = src.convert("RGB").crop((0, 0, 10, 10))
        out = _save_cropped_image(cropped, src, tmp_path / "out.webp")
    with Image.open(out) as saved:
        assert saved.info.get("icc_profile") == b"fake-icc"


# ── 内容边界/字形回归（d2217b7 收口：缺口边界、glyph_only 返回键、矮图比例、above_fg 跨元素误伤）──


def test_content_bounds_gaps_keep_first_and_last_content_rows():
    """内容区多处缺口时：上界取首个内容行，下界取末个内容行（不落进缺口）。"""
    from app.services.image_cropping_content import _content_bounds

    # 三段内容 [5,8] / [12,14] / [20,23]，缺口均 ≤5 行（允许续接）
    d = np.zeros(40, dtype=np.float32)
    d[[5, 6, 7, 8, 12, 13, 14, 20, 21, 22, 23]] = 0.30
    top, bottom = _content_bounds(d)
    assert top == 5
    assert bottom == 23

    # 缺口超过上限（12 行 > 5）时分段：上下界仍各取自己区段的首/末内容行
    d2 = np.zeros(40, dtype=np.float32)
    d2[[5, 6, 7, 20, 21, 22]] = 0.30
    top2, bottom2 = _content_bounds(d2)
    assert top2 == 5
    assert bottom2 == 22

    # 底部 2 行孤立噪声不足最小连续行数（3）→ 不得把下界抬到噪声行
    d3 = np.zeros(40, dtype=np.float32)
    d3[[10, 11, 12, 13, 14, 30, 31]] = 0.30
    top3, bottom3 = _content_bounds(d3)
    assert top3 == 10
    assert bottom3 == 14


def test_glyph_only_bounds_share_unified_keys_with_normal_path(tmp_path, monkeypatch):
    """glyph_only（内容边界失败但有字形证据）与正常路径必须返回同一套键。

    回归 d2217b7：glyph_only 曾缺 ``residual_bottom_frac`` / ``bounds_valid``，
    扫描与 apply 两条路径消费同一份 bounds 时会因字段缺失而口径不一致。
    """
    from app.services import image_cropping as ic

    # 正常路径：灰带包夹截图 → 内容边界有效，返回键齐全
    data, _ = _make_content_band_screenshot()
    band_path = tmp_path / "band.jpg"
    band_path.write_bytes(data)
    normal = ic.detect_content_bounds(band_path)
    assert normal["bounds_valid"] is True
    assert normal["residual_bottom_frac"] == 0.0

    # glyph_only 路径：强制内容边界检测失败 + 字形证据 found（透明叠加状态栏场景）
    tiny = tmp_path / "tiny.jpg"
    Image.new("RGB", (60, 120), (30, 30, 30)).save(tiny, "JPEG")

    def _raise_no_bounds(*args, **kwargs):
        raise ValueError("未检测到内容区边界")

    monkeypatch.setattr(ic, "_content_bounds_from_small", _raise_no_bounds)
    monkeypatch.setattr(
        ic,
        "_glyph_evidence",
        lambda *args, **kwargs: {"found": True, "strong": False, "top_frac": 0.05},
    )
    _features, glyph_only = ic.analyze_screenshot_combined(tiny)

    assert glyph_only is not None
    assert glyph_only["kind"] == "glyph_only"
    assert glyph_only["bounds_valid"] is False
    assert glyph_only["residual_bottom_frac"] == 0.0
    # 关键：与正常路径同一套键，消费方无需按分支补默认值
    assert set(glyph_only) == set(normal)


def test_glyph_top_frac_short_image_uses_full_height_and_cap():
    """矮图建议比例按真实条带/全高换算，且始终受 12% 上限约束。"""
    from app.services.image_cropping_glyph import _GLYPH_TOP_FRAC_CAP, _glyph_top_frac

    # 60px 高图：条带被 max(12, H*0.10)=12 抬高；底行 4 + 余量 2 → 6/60 = 0.10
    # （旧口径 6/12×10% 只有 0.05，会把矮图状态栏裁不干净）
    assert abs(_glyph_top_frac(4, 12, 60) - 0.10) < 1e-9

    # 30px 高图：8/30≈0.267 超过上限 → 封顶
    assert _glyph_top_frac(6, 12, 30) == _GLYPH_TOP_FRAC_CAP
    # 未传全高时退回条带高（1.0）→ 同样受上限约束，不会给出 >12% 的比例
    assert _glyph_top_frac(10, 12, None) == _GLYPH_TOP_FRAC_CAP
    # 100px 高图 + 底行 10：恰好 12/100 = 0.12
    assert abs(_glyph_top_frac(10, 12, 100) - 0.12) < 1e-9


def test_glyph_signature_above_fg_ignores_taller_right_icon():
    """above_fg 只看时间字形窗口列：右侧图标更高不得把左侧时间块判掉。"""
    from app.services.image_cropping_glyph import _glyph_time_signature

    h, w = 60, 320
    left_time = (30, 80, 15, 27, 600, 1.0)  # 左侧时间块（中心 x≈0.17）
    taller_right = (250, 290, 8, 27, 760, 1.0)  # 右区图标：更高（上沿更靠上）
    fg = np.zeros((h, w), dtype=bool)
    fg[8:15, 250:290] = True  # 右图标像素出现在左时间块上沿之上

    strong, note = _glyph_time_signature([left_time, taller_right], fg, h, w)
    assert strong is True, "右侧图标较高不应把左侧时间块的上方前景算进来"
    assert "单块时间回退" in note

    # 反例 1：没有右侧图标 → 左侧孤立一块不构成状态栏
    assert (
        _glyph_time_signature([left_time], np.zeros((h, w), dtype=bool), h, w)[0] is False
    )

    # 反例 2：时间块上方确有照片内容 → 仍必须拒绝（修复不能放宽真实判据）
    fg_photo = fg.copy()
    fg_photo[3:15, 30:86] = True
    assert _glyph_time_signature([left_time, taller_right], fg_photo, h, w)[0] is False


def test_cleanup_stale_crop_temps_only_removes_old_crop_files(tmp_path):
    """裁剪临时文件清理只删 1 小时前的 *_crop_*，不碰正常素材/新临时文件。"""
    import os
    import time

    from app.services.image_cropping import cleanup_stale_crop_temps

    old = tmp_path / "a_crop_old.jpg"
    fresh = tmp_path / "b_crop_fresh.jpg"
    normal = tmp_path / "normal.jpg"
    for p in (old, fresh, normal):
        p.write_bytes(b"x")
    past = time.time() - 2 * 3600
    os.utime(old, (past, past))

    removed = cleanup_stale_crop_temps(tmp_path, older_than_seconds=3600)

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()
    assert normal.exists()
    assert cleanup_stale_crop_temps(tmp_path / "missing") == 0


def test_public_functions_accept_string_paths(tmp_path):
    """公开裁剪/探测入口统一接受 str 路径（内部转 Path）。"""
    from PIL import Image

    from app.services.image_cropping import crop_region_to_temp, probe_size

    p = tmp_path / "strpath.png"
    Image.new("RGB", (80, 120), (30, 30, 30)).save(p)

    assert probe_size(str(p)) == (80, 120)
    out = crop_region_to_temp(str(p), 0.1, 0.9)
    assert out.exists()
    out.unlink()


def test_cleanup_stale_crop_temps_for_roots_startup(tmp_path):
    """启动兜底：遍历月份子目录，只删旧 *_crop_*，新文件/普通文件/空目录不报错。"""
    import os
    import time

    from app.services.image_cropping import cleanup_stale_crop_temps_for_roots

    images = tmp_path / "images"
    thumbnails = tmp_path / "thumbnails"
    videos = tmp_path / "videos"
    image_month = images / "2026-11"
    thumb_month = thumbnails / "2026-10"
    for folder in (image_month, thumb_month, videos):
        folder.mkdir(parents=True, exist_ok=True)

    old_image = image_month / "a_crop_old.jpg"
    new_image = image_month / "b_crop_fresh.jpg"
    normal_image = image_month / "normal.jpg"
    old_thumb = thumb_month / "c_crop_old.jpg"
    for p in (old_image, new_image, normal_image, old_thumb):
        p.write_bytes(b"x")
    past = time.time() - 2 * 3600
    os.utime(old_image, (past, past))
    os.utime(old_thumb, (past, past))

    stats = cleanup_stale_crop_temps_for_roots(
        (images, thumbnails, videos), older_than_seconds=3600, max_dirs=10
    )

    assert stats["removed"] == 2
    assert stats["timed_out"] is False
    assert stats["errors"] == 0
    assert not old_image.exists()
    assert not old_thumb.exists()
    assert new_image.exists()
    assert normal_image.exists()

    # 空目录 / 不存在的根目录：不报错、不删除任何东西
    empty = tmp_path / "empty"
    empty.mkdir()
    stats_empty = cleanup_stale_crop_temps_for_roots((empty, tmp_path / "missing"))
    assert stats_empty == {
        "removed": 0,
        "scanned_dirs": 0,
        "timed_out": False,
        "errors": 0,
    }
