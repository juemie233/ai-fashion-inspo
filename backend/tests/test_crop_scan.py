"""裁剪扫描的判定逻辑测试（VLM 判定、内容带/字形证据、素材筛选）。"""

from io import BytesIO
import numpy as np
from PIL import Image
from app.config import settings
from app.services.crop_service import (
    _content_candidate_qualified,
    detect_content_bounds,
)
from app.services.image_cropping import _glyph_evidence

from crop_helpers import (
    _make_vertical_screenshot,
    _make_content_band_screenshot,
    _make_status_bar_screenshot,
    _upload_screenshot,
    _scan,
    _scan_all,
    _file_size,
    _backup_count,
    _make_status_bar_strip_image,
    _bounds_fixture,
    _make_gradient_top_screenshot,
    _make_narrow_top_band_screenshot,
)


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


def test_scan_cursor_pagination_no_loss(client):
    """游标续扫分批覆盖全部候选，不重不漏。

    回归：limit 截断的边界候选曾被计入 total 却未入选，且续扫游标指向它的
    id（id < cursor 扫描）→ 该候选被永久跳过丢失。修复后上限检查放在入选
    之后，断点游标指向已入选的最后一条，续扫恰好从其后接续。
    """
    ids = []
    for i in range(3):
        # 每次不同背景色，避免同内容上传被内容去重拦截
        data, ctype = _make_vertical_screenshot(bg=(220 - i * 30, 220, 220))
        ids.append(_upload_screenshot(client, data, ctype)["id"])

    r1 = _scan(client, limit=2)
    assert r1["truncated"] is True
    assert len(r1["items"]) == 2
    assert r1["total"] == 2
    assert r1["next_cursor"]

    r2 = _scan(client, limit=2, cursor=r1["next_cursor"])
    assert r2["truncated"] is False
    assert len(r2["items"]) == 1
    assert r2["next_cursor"] is None

    got = {str(i["id"]) for i in r1["items"]} | {str(i["id"]) for i in r2["items"]}
    assert got == {str(i) for i in ids}, "分批扫描丢失或重复了候选"


def test_scan_invalid_cursor_rejected(client):
    """非法分页游标返回 400 与中文提示（而非 int() 的英文报错）。"""
    r = client.post(
        "/api/admin/crop-phone-screenshots/scan",
        json={"mode": "ratio", "crop_top": 0.05, "crop_bottom": 0.05, "cursor": "not-a-number"},
    )
    assert r.status_code == 400
    assert "分页游标格式无效" in r.json()["detail"]


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
    """内容边界模式扫描：状态栏（含时钟字形）+ 播放器条截图也能检出边界。"""
    data, ctype = _make_status_bar_screenshot(status_bar=70, player_bar=60)
    insp = _upload_screenshot(client, data, ctype)

    body = _scan(client, mode="content")
    assert body["total"] == 1
    item = body["items"][0]
    assert item["auto_ok"] is True
    assert abs(item["crop_top"] - 70 / 600) < 0.01
    assert abs(item["crop_bottom"] - 60 / 600) < 0.01


def test_content_mode_high_saturation_top_not_status_bar(client):
    """回归（误报修正）：彩色照片顶部（高饱和度条带）不得被判为状态栏残留。

    真实素材诊断：8 个用户误报样本中 5 个顶部条带饱和度 0.17~0.45
    （彩色照片顶部：天空/头发/水印等），被字形证据误判 found=True →
    列进「疑似顶部状态栏残留」候选。真实状态栏/透明残留饱和度 ≤0.12，
    字形证据检测加饱和度门槛（>0.15 直接拒绝）后，高饱和条带不再触发。
    """
    width, height = 300, 600
    rng = np.random.default_rng(11)
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    # 全图：普通照片内容（匀质灰带渐变化 + 植被噪点），无状态栏/播放器条结构
    arr[:, :] = (150, 150, 160)
    for y in range(height):
        # 轻微行间亮度渐变（照片自然光感），无任何条带结构
        arr[y] = (int(150 + 20 * (y / height)), int(150 + 15 * (y / height)), int(160 + 10 * (y / height)))
    # 顶部 30px：染一点高饱和彩色（模拟照片顶部鲜艳天空/背景）
    arr[:30, :] = (200, 60, 60)
    arr[:30] = np.clip(arr[:30] + rng.integers(-10, 11, size=arr[:30].shape), 0, 255)
    # 照片内容：中低幅度噪点（非截图条带、非满屏纹理）
    noise = rng.integers(-25, 26, size=arr.shape)
    arr = np.clip(arr.astype(np.int32) + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    insp = _upload_screenshot(client, buf.getvalue(), "image/jpeg")

    body = _scan(client, mode="content", vlm_review=False)
    # 高饱和顶部条带：不是状态栏，content 模式不应列为候选
    assert body["total"] == 0, body


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


def test_content_mode_weak_residual_not_listed(client):
    """内容边界模式：仅弱残留信号（无强字形/实底带）的非完整截图不再列候选。

    用户反馈「上下没有明显要素」的素材被弱信号误列（标注的 8 张负样本
    ratio 1.32~1.78，全部由行剖面 top_bar / 残留估算 / 弱字形单独命中）。
    候选资格收紧后，弱残留不再单独构成候选——列表只保留强字形（左右两角
    时间/信号签名）、底部实底带、或顶部实底带+字形佐证的素材。
    """
    # 比例 1.5（400x600）：顶部 3 行低多样度残留 + 高多样度内容区（无字形）
    width, height = 400, 600  # 比例 1.5 < 1.75
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    rng = np.random.default_rng(23)
    arr[:, :] = (170, 170, 170)
    for y in range(0, 3):
        cols = rng.choice(width, size=20, replace=False)
        arr[y, cols] = rng.integers(0, 256, size=(20, 3), dtype=np.uint8)
    for y in range(3, height):
        cols = rng.choice(width, size=200, replace=False)
        arr[y, cols] = rng.integers(0, 256, size=(200, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    _upload_screenshot(client, buf.getvalue(), "image/jpeg")

    # content 模式：弱残留（无强字形、无实底带）→ 不列候选（列表保持干净）
    items, _body = _scan_all(client, mode="content")
    assert items == []

    # ratio 模式：仍按 1.75 过滤，不可见
    body2 = _scan(client, mode="ratio")
    assert body2["total"] == 0


def test_content_mode_strong_glyph_listed(client, monkeypatch):
    """内容边界模式：非完整截图但有强字形证据 → 仍列候选（收紧不误伤真残留）。"""
    width, height = 400, 600  # 比例 1.5
    arr = np.full((height, width, 3), 170, dtype=np.uint8)
    rng = np.random.default_rng(29)
    for y in range(3, height):
        cols = rng.choice(width, size=200, replace=False)
        arr[y, cols] = rng.integers(0, 256, size=(200, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    insp = _upload_screenshot(client, buf.getvalue(), "image/jpeg")

    # 伪造检测结果：强字形命中（模拟真实状态栏时间/信号签名）
    # 切片 ⑤ 之后扫描逻辑住在 crop_scan：`analyze_screenshot_combined` 由**该模块**
    # 读取，patch 旧模块（crop_service）不生效且不报错，只会静默扫不出候选。
    from app.services import crop_scan as cs

    original = cs.analyze_screenshot_combined

    def _fake_combined(path):
        features, bounds = original(path)
        if bounds is None:
            bounds = {
                "top_frac": 0.0,
                "bottom_frac": 0.0,
                "top_edge": 0,
                "bottom_edge": 0,
                "correction": False,
                "kind": "plain",
                "already_cropped": False,
                "residual_top_frac": 0.0,
                "bounds_valid": False,
                "glyph_top_frac": 0.0,
                "glyph_strong": False,
            }
        bounds["glyph_top_frac"] = 0.10  # 强字形必带建议比例（真实检测同口径）
        bounds["glyph_strong"] = True
        return features, bounds

    monkeypatch.setattr(cs, "analyze_screenshot_combined", _fake_combined)
    items, _body = _scan_all(client, mode="content")
    assert len(items) == 1
    assert items[0]["id"] == insp["id"]


def test_content_scan_stages_full_screenshot_first(client, monkeypatch):
    """content 模式分段扫描：先扫「完整截图」段，扫完再把游标切到「已裁过」段。

    回归（用户反馈「内容边界检测（抖音截图）似乎失效」）：两段混在一轮按 id 随机
    顺序解码时，60 秒预算几乎全花在已裁过的截图与普通照片上（单张完整解码 ~85ms，
    预算只够 ~630 张），连扫多轮候选都是 0，看起来像功能坏了。现在第一段只解码
    完整截图比例（合格候选几乎都在这一段），第一轮就能出候选；第二段（已裁过、
    只认强字形证据）由用户点「继续扫描」时接着扫。
    """
    from app.services import crop_scan as cs

    original = cs.analyze_screenshot_combined

    def _fake_combined(path):
        """伪造强字形命中（模拟真实状态栏时间/信号签名）。"""
        features, bounds = original(path)
        if bounds is None:
            bounds = {
                "top_frac": 0.0,
                "bottom_frac": 0.0,
                "top_edge": 0,
                "bottom_edge": 0,
                "correction": False,
                "kind": "plain",
                "already_cropped": False,
                "residual_top_frac": 0.0,
                "bounds_valid": False,
                "glyph_top_frac": 0.0,
                "glyph_strong": False,
            }
        bounds["glyph_top_frac"] = 0.10
        bounds["glyph_strong"] = True
        return features, bounds

    monkeypatch.setattr(cs, "analyze_screenshot_combined", _fake_combined)

    # 「已裁过」比例（400x600 = 1.5）：只有强字形证据才能入选 → 属第二段
    width, height = 400, 600
    arr = np.full((height, width, 3), 170, dtype=np.uint8)
    rng = np.random.default_rng(41)
    for y in range(3, height):
        cols = rng.choice(width, size=200, replace=False)
        arr[y, cols] = rng.integers(0, 256, size=(200, 3), dtype=np.uint8)
    buf = BytesIO()
    Image.fromarray(arr).save(buf, "JPEG")
    cropped_id = _upload_screenshot(client, buf.getvalue(), "image/jpeg")["id"]
    # 完整截图比例（300x600 = 2.0）：属第一段
    full_data, full_ctype = _make_vertical_screenshot(bg=(180, 200, 230))
    _upload_screenshot(client, full_data, full_ctype)

    first = _scan(client, mode="content")
    assert cropped_id not in [i["id"] for i in first["items"]], "第一段不该处理已裁过的素材"
    assert first["truncated"] is True
    assert first["next_cursor"] == "cropped:"  # 段游标：接下来扫已裁过段

    second = _scan(client, mode="content", cursor=first["next_cursor"])
    assert [i["id"] for i in second["items"]] == [cropped_id], "第二段应列出强字形的已裁截图"
    assert second["truncated"] is False
    assert second["next_cursor"] is None


def test_glyph_evidence_detects_status_bar_signature():
    """字形证据（真阳性保护）：平滑底上的时间数字 + 右侧图标 → strong=True。

    收紧误报（下沿截断/上沿/背景方差）后，真实状态栏结构必须仍判强字形，
    否则 content 模式会漏掉真截图。
    """
    g = _glyph_evidence(_make_status_bar_strip_image(), debug=True)
    assert g["found"] is True
    assert g["strong"] is True, g["debug"]
    assert 0 < g["top_frac"] < 0.06


def test_glyph_evidence_textured_background_not_strong():
    """字形证据（误报修正）：同样字形叠在纹理底上 → 不判强字形。

    照片边缘（发丝/饰品/衣料轮廓）与印刷体文字的区别在于「周边背景是否
    平滑」：背景灰度标准差 > _GLYPH_BG_STD_MAX 时不算状态栏字形（连通域
    仍保留为弱证据，found=True）。
    """
    g = _glyph_evidence(_make_status_bar_strip_image(textured=True), debug=True)
    assert g["found"] is True
    assert g["strong"] is False


def test_glyph_evidence_rejects_blob_clipped_by_strip():
    """字形证据（误报修正）：内容被顶部 10% 条带截断（贴条带下沿）→ 无字形。"""
    arr = np.full((600, 300, 3), 170, dtype=np.uint8)
    arr[30:200, 50:95] = (30, 30, 30)  # 深色块跨过条带下沿（行 60）
    g = _glyph_evidence(Image.fromarray(arr), debug=True)
    assert g["found"] is False
    assert any("下沿" in r["reason"] for r in g["debug"]["rejected"])


def test_glyph_evidence_rejects_blob_at_image_top():
    """字形证据（误报修正）：内容压在画面最顶行（条带前 8%）→ 无字形。"""
    arr = np.full((600, 300, 3), 170, dtype=np.uint8)
    arr[0:40, 50:95] = (30, 30, 30)  # 顶到画面顶端，但未触及条带下沿
    g = _glyph_evidence(Image.fromarray(arr), debug=True)
    assert g["found"] is False
    assert any("上沿" in r["reason"] for r in g["debug"]["rejected"])


def test_glyph_evidence_rejects_content_above_band():
    """字形证据（误报修正）：字形带上方还有内容（照片纹理）→ 不判强字形。

    状态栏文字之上是状态栏自身背景（实测真实截图前景密度 0），照片在字形
    上方永远还有内容（实测误报 0.034~0.142）——「带上方前景密度 ≤ 1%」是
    区分状态栏文字与照片纹理块最直接的结构信号。
    """
    arr = np.zeros((600, 300, 3), dtype=np.uint8)
    arr[:, :] = (60, 60, 60)
    arr[12:23, 84:90] = (250, 250, 250)  # 时间窄块
    arr[12:23, 100:127] = (250, 250, 250)  # 时间宽块
    arr[13:22, 250:263] = (240, 240, 240)
    arr[13:22, 266:279] = (240, 240, 240)
    # 字形带上方（行 0~6）叠加照片纹理块
    arr[0:7, 120:170] = (200, 200, 200)
    arr[0:7, 40:80] = (20, 20, 20)
    g = _glyph_evidence(Image.fromarray(arr), debug=True)
    assert g["found"] is True
    assert g["strong"] is False
    assert g["debug"]["fg_above"] > 0.01


def test_content_candidate_qualified_rules():
    """候选资格裁决（纯函数）：强字形 / 完整截图比例+弱字形两条通道，其余排除。

    用户标注 24 张「上下都没有明显要素」的负样本，误列根因是「仅实底带」
    （3 张，照片暗部/灰底）、「仅弱字形」（11 张）、「仅残留估算」；另外
    640x1280 竖版照片（ratio = 2.0）会被「纯比例先验」误列——因此完整截图
    先验必须叠加字形证据才成立。
    """
    q = _content_candidate_qualified
    # 1) 强字形单独成立（非完整截图、低置信也认）
    assert q(_bounds_fixture(glyph_strong=True), "low", 1.5) is True
    # 2) 完整截图比例 + 弱字形（真实抖音截图 ratio ≈ 2.05~2.17，均检出字形）
    assert q(_bounds_fixture(glyph_found=True), "medium", 2.05) is True
    assert q(_bounds_fixture(glyph_found=True, bottom_frac=0.08), "high", 2.16) is True
    # 2b) 完整截图比例 + UI 结构（灰带/状态栏修正）——无字形但有结构证据
    assert q(_bounds_fixture(kind="gray_band", top_frac=0.1), "medium", 2.0) is True
    assert q(_bounds_fixture(kind="status_bar"), "medium", 2.05) is True
    # 3) 完整截图比例但无任何 UI 证据（kind=plain）→ 排除（照片也会达到 2.0）
    assert q(_bounds_fixture(), "medium", 2.05) is False
    assert q(_bounds_fixture(top_frac=0.09), "medium", 2.0) is False
    # 4) 非完整截图 + 弱信号 → 排除：仅实底带 / 仅弱字形 / 仅残留估算
    assert q(_bounds_fixture(bottom_frac=0.08, glyph_found=True), "high", 1.5) is False
    assert q(_bounds_fixture(top_frac=0.19, glyph_found=True), "high", 1.5) is False
    assert q(_bounds_fixture(glyph_found=True), "medium", 1.5) is False
    assert q(_bounds_fixture(residual_top_frac=0.15), "medium", 1.5) is False
    # 5) 低置信且无任何建议：即使比例达到先验也排除（纯噪点图防混入）
    assert q(_bounds_fixture(), "low", 2.2) is False


def test_content_mode_excludes_cleanly_cropped(client):
    """内容边界模式：已裁剪干净（无任何残留建议）的图不再列入扫描候选。

    扫描列表只保留仍有可裁信息的素材（避免被大量已处理图淹没）；
    若用户仍显式勾选执行，apply 侧按「已裁剪过，无需裁剪」跳过兜底。
    """
    # 顶部 3 行灰带 + 全噪点内容：内容占比 ≈99.5%，无有效裁剪区域、无残留建议
    arr = np.zeros((600, 300, 3), dtype=np.uint8)
    arr[:3] = (70, 70, 70)
    rng = np.random.default_rng(9)
    arr[3:] = rng.integers(0, 256, size=arr[3:].shape, dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    insp = _upload_screenshot(client, buf.getvalue(), "image/jpeg")

    # 干净图不出现在候选列表
    body = _scan(client, mode="content")
    assert body["total"] == 0
    assert body["items"] == []

    # 用户显式勾选执行 → apply 跳过并说明原因（不误裁）
    r = client.post(
        "/api/admin/crop-phone-screenshots/apply",
        json={"ids": [insp["id"]], "mode": "content"},
    )
    assert r.status_code == 200, r.text
    body2 = r.json()
    assert body2["processed"] == 0
    reasons = [s.get("reason", "") for s in body2["skipped"]]
    assert any("无需裁剪" in msg for msg in reasons)


def test_scan_excludes_non_vertical_and_non_manual(client):
    """扫描过滤：横图不列、非手动上传的竖屏图不列。"""
    buf = BytesIO()
    Image.new("RGB", (600, 300), (200, 200, 200)).save(buf, "JPEG")
    _upload_screenshot(client, buf.getvalue(), "image/jpeg")

    data, ctype = _make_vertical_screenshot()
    _upload_screenshot(client, data, ctype, source_type="scraper")

    body = _scan(client)
    assert body["total"] == 0


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


def test_content_bounds_ignores_gradient_top(tmp_path):
    """误报回归：照片顶部缓升渐变（暗角/天空）不被判为可裁剪（top_frac=0）。"""

    data, _ = _make_gradient_top_screenshot()
    p = tmp_path / "gradient.jpg"
    p.write_bytes(data)

    r = detect_content_bounds(p)
    assert r["top_frac"] == 0.0
    assert r["bottom_frac"] == 0.0
    assert r["already_cropped"] is True


def test_content_bounds_ignores_narrow_top_band(tmp_path):
    """误报回归：顶部 1~2 行低多样度（0.8% 级噪声）不产出裁剪比例。"""

    data, _ = _make_narrow_top_band_screenshot()
    p = tmp_path / "narrow.jpg"
    p.write_bytes(data)

    r = detect_content_bounds(p)
    assert r["top_frac"] == 0.0


def test_scan_content_mode_excludes_low_confidence_noise(client):
    """自动化口径：无任何状态栏/导航栏特征（confidence=low）且无残留信号的
    竖屏图（大概率普通照片，顶部纯色天空/暗部被误判为边界）静默排除，
    不再以「自动检测失败/疑似非截图请人工确认」占据扫描列表。"""
    width, height = 300, 600
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[:30] = (80, 80, 80)  # 顶部 30px 纯色（可能是照片纯色天空/背景）
    rng = np.random.default_rng(11)
    arr[30:] = rng.integers(0, 256, size=arr[30:].shape, dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    _upload_screenshot(client, buf.getvalue(), "image/jpeg")

    body = _scan(client, mode="content")
    assert body["total"] == 0
    assert body["items"] == []


def test_scan_content_mode_excludes_undetectable_layout(client):
    """自动化口径：内容边界检测失败（全噪点图无内容区边界可检）的素材
    静默排除——此前会带默认裁剪比例混入候选（auto_ok=True），属误列。"""
    width, height = 300, 600
    rng = np.random.default_rng(31)
    arr = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8).astype(np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, "JPEG")
    _upload_screenshot(client, buf.getvalue(), "image/jpeg")

    body = _scan(client, mode="content")
    assert body["total"] == 0
    assert body["items"] == []


def test_scan_content_mode_keeps_ui_feature_cropped(client):
    """回归（漏检）：检出状态栏字形的素材不得被静默过滤。

    修复前 content 模式对 already_cropped 且无残留建议的素材一律静默排除，
    即使它带有明确的系统 UI 特征——用户在扫描列表里看不到这些真实截图，
    顶部状态栏残留无法勾选处理。修复后以字形证据为准：有状态栏字形的
    真实截图必列入。
    """
    data, ctype = _make_status_bar_screenshot(status_bar=70, player_bar=60)
    _upload_screenshot(client, data, ctype)
    body = _scan(client, mode="content")
    assert body["total"] >= 1, body
    item = body["items"][0]
    assert item["confidence"] in ("high", "medium")  # 状态栏+播放器条特征明确
    assert item["boundary_kind"] is not None


def test_scan_vlm_review_marks_and_prioritizes(client, monkeypatch):
    """AI 主判（方案 A）：VLM 阳性候选保留并置顶，阴性候选从列表移除。

    上传两张不同背景色截图（避免内容去重 409）制造两个候选。
    VLM 对第一个返回 True（阳性），第二个返回 False（阴性）→ 保留 1 个。
    """
    data, ctype = _make_vertical_screenshot()
    data2, ctype2 = _make_vertical_screenshot(bg=(200, 200, 240))
    _upload_screenshot(client, data, ctype)
    _upload_screenshot(client, data2, ctype2)

    calls = {"n": 0}

    async def _fake_review(full):
        calls["n"] += 1
        return calls["n"] == 1  # 第一个阳性，第二个阴性（移除）

    monkeypatch.setattr("app.services.crop_scan._vlm_residue_review", _fake_review)
    r = _scan(client, mode="auto", vlm_review=True)

    assert r["vlm_reviewed"] is True
    assert r["vlm_hits"] == 1
    assert r["vlm_removed"] == 1
    assert calls["n"] == 2  # 两个候选都走了复核
    items = r["items"]
    assert len(items) == 1  # 阴性候选已被主判移除
    # 命中候选置顶（列表第一），且 note 追加了 AI 复核标注
    assert items[0].get("vlm_residue") is True
    assert "AI 复核" in (items[0]["note"] or "")


def test_scan_vlm_review_unknown_keeps_candidate(client, monkeypatch):
    """AI 主判（方案 A）：VLM 未知（判定失败）→ 候选保留并标注「不可用」。"""
    data, ctype = _make_vertical_screenshot()
    _upload_screenshot(client, data, ctype)

    async def _fake_review(full):
        return None  # 未知

    monkeypatch.setattr("app.services.crop_scan._vlm_residue_review", _fake_review)
    r = _scan(client, mode="auto", vlm_review=True)

    assert r["vlm_hits"] == 0
    assert r["vlm_removed"] == 0
    assert len(r["items"]) == 1  # 未知 → 保守保留
    item = r["items"][0]
    assert item.get("vlm_residue") is None
    assert "AI 复核不可用" in (item["note"] or "")


def test_scan_vlm_review_can_be_disabled(client, monkeypatch):
    """关闭 AI 复核（vlm_review=false）：不调用 VLM，响应 vlm_reviewed=False、vlm_hits=0。"""
    data, ctype = _make_vertical_screenshot()
    _upload_screenshot(client, data, ctype)

    calls = {"n": 0}

    async def _fake_review(full):
        calls["n"] += 1
        return True

    monkeypatch.setattr("app.services.crop_scan._vlm_residue_review", _fake_review)
    r = _scan(client, mode="auto", vlm_review=False)

    assert r["vlm_reviewed"] is False
    assert r["vlm_hits"] == 0
    assert calls["n"] == 0
    assert all(not c.get("vlm_residue") for c in r["items"])


def test_scan_vlm_review_silent_on_failure(client, monkeypatch):
    """VLM 复核异常（Ollama 不可用等）静默跳过：扫描结果不受影响，命中数归零。"""
    data, ctype = _make_vertical_screenshot()
    _upload_screenshot(client, data, ctype)

    async def _boom(full):
        raise RuntimeError("ollama down")

    monkeypatch.setattr("app.services.crop_scan._vlm_residue_review", _boom)
    r = _scan(client, mode="auto", vlm_review=True)

    assert r["vlm_reviewed"] is True  # 已请求复核（尝试过）
    assert r["vlm_hits"] == 0
    assert len(r["items"]) == 1  # 候选本身不受影响
    assert all(not c.get("vlm_residue") for c in r["items"])
