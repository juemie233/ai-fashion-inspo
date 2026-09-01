"""手动裁剪（素材详情页单张裁剪）接口与服务测试。

覆盖：成功裁剪替换原图并同步派生数据（缩略图/哈希/phash/主色调/向量回填）、
备份成功后清理、参数校验（比例越界、最小高度）、垃圾箱/非图片/文件缺失拒绝、
异常回滚恢复原图、EXIF 方向裁剪基准、审计留痕。
"""

import json
import sqlite3
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.config import settings


def _upload_image(client, width=300, height=600, color=(220, 60, 60), **overrides):
    """上传一张指定尺寸/颜色的图片素材。"""
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "JPEG")
    r = client.post(
        "/api/inspirations",
        files={"file": ("img.jpg", buf.getvalue(), "image/jpeg")},
        data=overrides,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _crop(client, insp_id, y1=0.1, y2=0.9):
    """调用手动裁剪接口。"""
    return client.post(
        f"/api/inspirations/{insp_id}/crop", json={"y1_ratio": y1, "y2_ratio": y2}
    )


def _db_get(insp_id):
    """同步读取素材记录字段与磁盘文件信息。"""
    db_path = settings.storage_root.parent / "fashion_inspo.db"
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT file_path, thumbnail_path, content_hash, phash, dominant_colors "
        "FROM inspirations WHERE id=?",
        (insp_id,),
    ).fetchone()
    conn.close()
    assert row is not None, f"素材记录不存在: {insp_id}"
    file_path, thumb_path, content_hash, phash, colors = row
    full = settings.storage_root / file_path
    with Image.open(full) as im:
        w, h = im.size
    return {
        "path": full,
        "width": w,
        "height": h,
        "hash": content_hash,
        "phash": phash,
        "colors": colors,
        "thumb": thumb_path,
    }


def _backup_count():
    """返回裁剪备份目录中的文件数（成功后应归零）。"""
    backup_root = settings.storage_root / "_crop_backup"
    if not backup_root.exists():
        return 0
    return sum(len(list(d.glob("*"))) for d in backup_root.iterdir() if d.is_dir())


def _pending_backfill_count():
    """返回待向量回填表行数。"""
    conn = sqlite3.connect(str(settings.storage_root.parent / "fashion_inspo.db"))
    n = conn.execute("SELECT COUNT(*) FROM pending_vector_backfills").fetchone()[0]
    conn.close()
    return n


def _set_fields(insp_id, **fields):
    """直接改库（模拟 AI 分析等前置条件）。"""
    conn = sqlite3.connect(str(settings.storage_root.parent / "fashion_inspo.db"))
    for col, val in fields.items():
        conn.execute(f"UPDATE inspirations SET {col}=? WHERE id=?", (val, insp_id))
    conn.commit()
    conn.close()


# ── 成功链路 ───────────────────────────────────────────────────────────────


def test_crop_success_region_and_derived_data(client):
    """成功裁剪：高度按保留区域缩小，缩略图/哈希/phash/主色调同步更新，备份清理，登记向量回填。"""
    insp = _upload_image(client)  # 300x600
    old = _db_get(insp["id"])
    # 模拟 AI 分析已写入主色调（仅原值非空时裁剪会刷新）
    _set_fields(insp["id"], dominant_colors='["#DC3C3C"]')

    r = _crop(client, insp["id"], y1=0.1, y2=0.9)
    assert r.status_code == 200, r.text
    body = r.json()
    # 就地替换：文件路径不变
    assert body["file_path"] == insp["file_path"]

    info = _db_get(insp["id"])
    # 保留 10%~90% 区域：600 → 480
    assert (info["width"], info["height"]) == (300, 480)
    # 内容哈希（SHA-256）与感知哈希均按新内容重算
    assert info["hash"] and info["hash"] != old["hash"]
    assert info["phash"] and info["phash"] != old["phash"]
    # 主色调按新图刷新（纯色图裁剪后主色不变）
    assert json.loads(info["colors"]) == ["#DC3C3C"]
    # 缩略图重建：新缩略图文件存在；旧缩略图（路径不同时）已删除
    assert info["thumb"]
    assert (settings.storage_root / info["thumb"]).exists()
    if old["thumb"] and old["thumb"] != info["thumb"]:
        assert not (settings.storage_root / old["thumb"]).exists()
    # 裁剪成功后备份已清理（不无限累积）
    assert _backup_count() == 0
    # 向量回填登记：上传时已登记该素材，裁剪重复登记被幂等去重（ID 已在待回填表，
    # worker 执行时按最新文件重建），数量维持 1 条不重复膨胀
    assert _pending_backfill_count() == 1
    # 审计留痕（破坏性操作）
    conn = sqlite3.connect(str(settings.storage_root.parent / "fashion_inspo.db"))
    audit = conn.execute("SELECT action, detail FROM audit_logs ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert audit is not None
    assert audit[0] == "crop"
    assert "手动裁剪" in audit[1]


def test_crop_allows_any_source(client):
    """手动裁剪不限制来源（与批量手机图裁剪仅处理 manual_upload 不同）。"""
    insp = _upload_image(client, source_type="scraper")
    r = _crop(client, insp["id"], 0.1, 0.9)
    assert r.status_code == 200, r.text
    assert _db_get(insp["id"])["height"] == 480


# ── 参数校验 ───────────────────────────────────────────────────────────────


def test_crop_rejects_invalid_ratios(client):
    """比例越界/上下颠倒由 Pydantic 校验拒绝（422），素材不受影响。"""
    insp = _upload_image(client)
    # y1 >= y2
    assert _crop(client, insp["id"], 0.9, 0.2).status_code == 422
    # 越界 [0, 1)
    assert _crop(client, insp["id"], -0.1, 0.9).status_code == 422
    assert _crop(client, insp["id"], 0.1, 1.5).status_code == 422
    assert _crop(client, insp["id"], 1.0, 1.0).status_code == 422
    # 原图未被改动
    assert _db_get(insp["id"])["height"] == 600
    assert _backup_count() == 0


def test_crop_rejects_small_region(client):
    """保留区域高度 < 50px 时拒绝（400），原图不受影响。"""
    insp = _upload_image(client)  # 高 600 → 保留 6px
    r = _crop(client, insp["id"], 0.49, 0.50)
    assert r.status_code == 400
    assert "过小" in r.json()["detail"]
    assert _db_get(insp["id"])["height"] == 600


# ── 业务边界 ───────────────────────────────────────────────────────────────


def test_crop_rejects_trash_and_non_image(client):
    """垃圾桶中的素材 / 非图片素材拒绝裁剪。"""
    insp = _upload_image(client)
    assert client.post(f"/api/inspirations/{insp['id']}/trash").status_code == 200
    r = _crop(client, insp["id"])
    assert r.status_code == 400
    assert "垃圾桶" in r.json()["detail"]

    insp2 = _upload_image(client)
    _set_fields(insp2["id"], media_type="video")
    r2 = _crop(client, insp2["id"])
    assert r2.status_code == 400
    assert "仅支持裁剪图片" in r2.json()["detail"]


def test_crop_missing_file_returns_400(client):
    """磁盘文件缺失时拒绝裁剪（400）。"""
    insp = _upload_image(client)
    (settings.storage_root / insp["file_path"]).unlink()
    r = _crop(client, insp["id"])
    assert r.status_code == 400
    assert "缺失" in r.json()["detail"]


def test_crop_unknown_id_returns_404(client):
    """素材不存在返回 404。"""
    assert _crop(client, "no-such-id").status_code == 404


# ── 异常回滚 ───────────────────────────────────────────────────────────────


def test_crop_rolls_back_when_post_replace_fails(client, monkeypatch):
    """替换原图后生成缩略图抛异常 → 从备份恢复原文件，数据库记录不变。"""
    from app.services import crop_service

    insp = _upload_image(client)
    old = _db_get(insp["id"])

    async def _boom(*args, **kwargs):
        raise RuntimeError("缩略图生成失败")

    monkeypatch.setattr(crop_service, "generate_thumbnail", _boom)

    r = _crop(client, insp["id"], 0.1, 0.9)
    assert r.status_code == 500
    # 磁盘恢复为原始内容（尺寸与哈希均不变）
    info = _db_get(insp["id"])
    assert (info["width"], info["height"]) == (300, 600)
    assert info["hash"] == old["hash"]
    # 备份目录中的备份保留（供人工兜底恢复）
    assert _backup_count() == 1


def test_crop_clears_thumbnail_when_regeneration_fails(client, monkeypatch):
    """缩略图重生成返回 None：thumbnail_path 置空并删除旧缩略图，裁剪本身仍成功。"""
    from app.services import crop_service

    insp = _upload_image(client)
    old = _db_get(insp["id"])
    assert old["thumb"]

    async def _no_thumb(*args, **kwargs):
        return None

    monkeypatch.setattr(crop_service, "generate_thumbnail", _no_thumb)

    r = _crop(client, insp["id"], 0.1, 0.9)
    assert r.status_code == 200, r.text
    info = _db_get(insp["id"])
    assert info["thumb"] is None  # thumbnail_path 已置空
    assert not (settings.storage_root / old["thumb"]).exists()  # 旧缩略图文件已删除
    assert info["height"] == 480  # 裁剪本身成功


# ── EXIF 方向 ──────────────────────────────────────────────────────────────


def test_crop_uses_exif_oriented_height(client):
    """EXIF 方向图片：裁剪比例基准为校正后尺寸（原始 600x300、Orientation=6 → 显示 300x600）。

    裁剪保留上半部分（y1=0, y2=0.5）后输出 300x300；按区域断言输出内容
    与「PIL exif_transpose + crop 上半」一致（红色区域 = 原图左半），
    硬边界附近的 JPEG 重编码伪影不纳入逐像素比较。
    """
    raw = Image.new("RGB", (600, 300), (30, 30, 30))
    for x in range(600):
        for y in range(300):
            raw.putpixel((x, y), (200, 50, 50) if x < 300 else (50, 50, 200))
    exif = Image.Exif()
    exif[0x0112] = 6  # 旋转 90°，宽高互换
    buf = BytesIO()
    raw.save(buf, "JPEG", exif=exif)

    r = client.post(
        "/api/inspirations",
        files={"file": ("rot.jpg", buf.getvalue(), "image/jpeg")},
        data={},
    )
    assert r.status_code == 201, r.text
    insp = r.json()

    r2 = _crop(client, insp["id"], y1=0, y2=0.5)
    assert r2.status_code == 200, r2.text

    full = settings.storage_root / insp["file_path"]
    with Image.open(full) as im:
        assert im.size == (300, 300)
        # 校正后上半区域（原图左半）为红色：红像素占比应接近 100%
        # get_flattened_data：Pillow 11.3+ 新接口（getdata 将于 Pillow 14 移除）
        rgb = im.convert("RGB")
        data = rgb.get_flattened_data() if hasattr(rgb, "get_flattened_data") else rgb.getdata()
        red = blue = 0
        for r_, g, b in data:
            if r_ > 150 and g < 100 and b < 100:
                red += 1
            elif b > 150 and r_ < 100 and g < 100:
                blue += 1
        total = 300 * 300
        assert red / total > 0.99, f"红像素占比过低: {red}/{total}"
        assert blue / total < 0.01, f"蓝像素异常出现: {blue}/{total}"


# ── 回归：带关联素材的响应序列化 ───────────────────────────────────────────


def test_crop_with_existing_tags_returns_full_out(client):
    """素材已带标签/关联时裁剪仍须 200 并返回完整标签（回归 MissingGreenlet→500）。

    裁剪成功后 router 通过同步的 _to_out 转换响应：若关联（tags/tag、
    bloggers/blogger 等）未预加载，访问会触发 async SQLAlchemy 懒加载，
    在同步转换函数中抛 MissingGreenlet，接口 500（新建素材无关联所以
    之前的用例无法覆盖该路径）。修复：裁剪服务改用 load_inspiration_full
    预加载全部嵌套关联。
    """
    insp = _upload_image(client)
    r = client.post(f"/api/inspirations/{insp['id']}/tags", json={"names": ["法式", "白色系"]})
    assert r.status_code == 200, r.text

    r2 = _crop(client, insp["id"], y1=0.1, y2=0.9)
    assert r2.status_code == 200, r2.text
    names = {t["tag"]["name"] for t in r2.json()["tags"]}
    assert "法式" in names and "白色系" in names