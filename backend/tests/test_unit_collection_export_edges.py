"""合集导出服务（collection_export_service）的直连边缘单测。

既有 tests/test_collection_export.py 走 API 覆盖四种格式主链路、空合集/超限/令牌
安全等；本文件直接调服务函数并用 tmp_path 临时存储，补齐：文件名/路径安全、行数据
与 CSV/Markdown 契约、ZIP 缺失跳过、PDF 字体与图片解码降级、封面选择、
``_archive_threshold`` 越界提示、``create_export`` 的格式分支与生成失败清理、
``get_export_file`` 的各类损坏 meta、``_cleanup_expired_exports`` 的残留目录分支。
全部离线，不写真实 storage。
"""

import json
import time
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from app.config import settings
from app.services import collection_export_service as svc
from app.utils.time import utcnow


# ═══════════════════════════════════════════════════════════════
#  fixtures：临时 storage + 假合集服务
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def storage(monkeypatch, tmp_path):
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setattr(settings, "storage_root", root)
    return root


def _write_image(storage: Path, rel_path: str, *, mode: str = "RGB", size=(64, 64)) -> None:
    path = storage / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, "white").save(path, format="PNG")


def _item(
    insp_id: str,
    rel_path: str,
    *,
    media_type: str = "image",
    tags: tuple[str, ...] = (),
    source_url: str | None = None,
    rating: int | None = None,
):
    return SimpleNamespace(
        id=insp_id,
        file_path=rel_path,
        media_type=media_type,
        source_type="douyin",
        source_author="作者A",
        favorited_at=datetime(2026, 1, 1, 8, 0, 0),
        rating=rating,
        source_url=source_url,
        tags=[SimpleNamespace(tag=SimpleNamespace(name=name)) for name in tags],
    )


class _FakeCollection:
    def __init__(self, **overrides) -> None:
        self.id = 7
        self.name = "夏日穿搭"
        self.query_json = None
        self.description = None
        self.cover_inspiration_id = None
        for key, value in overrides.items():
            setattr(self, key, value)


class _FakeDb:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


def _patch_collection(monkeypatch, items, total=None, collection=None):
    async def _get(_db, _collection_id):
        return collection or _FakeCollection()

    async def _list(_db, _collection_id, page, size):
        return items, (len(items) if total is None else total)

    monkeypatch.setattr(svc.collection_service, "get_collection", _get)
    monkeypatch.setattr(svc.collection_service, "list_collection_inspirations", _list)


@pytest.fixture
def cleanup_counter(monkeypatch):
    calls = {"n": 0, "removed": 2}

    def _cleanup() -> int:
        calls["n"] += 1
        return calls["removed"]

    monkeypatch.setattr(svc, "_cleanup_expired_exports", _cleanup)
    return calls


def _props(storage: Path, run_id: str) -> Path:
    return storage / "exports" / run_id


# ═══════════════════════════════════════════════════════════════
#  纯函数：命名 / 路径安全 / 行数据
# ═══════════════════════════════════════════════════════════════


def test_sanitize_name_cases():
    assert svc._sanitize_name(None) == "collection"
    assert svc._sanitize_name("   ") == "collection"
    assert svc._sanitize_name("夏季/穿搭:精选*?") == "夏季_穿搭_精选"
    cleaned = svc._sanitize_name("中文合集名称保留")
    assert cleaned == "中文合集名称保留"
    assert len(svc._sanitize_name("长" * 100)) == 60
    assert svc._sanitize_name("  ._名称_  ") == "名称"


def test_resolve_storage_file_safety(storage, tmp_path):
    assert svc._resolve_storage_file(None) is None
    assert svc._resolve_storage_file("") is None
    assert svc._resolve_storage_file("images/missing.jpg") is None

    _write_image(storage, "images/ok.png")
    resolved = svc._resolve_storage_file("images/ok.png")
    assert resolved is not None and resolved.is_file()

    # 目录不是文件
    (storage / "images").mkdir(exist_ok=True)
    assert svc._resolve_storage_file("images") is None

    # 越界：../ 指向 storage 之外
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")
    assert svc._resolve_storage_file("../outside.png") is None


def test_tags_text_variants():
    assert svc._tags_text(SimpleNamespace(tags=[])) == ""
    assert svc._tags_text(SimpleNamespace()) == ""  # 无 tags 属性
    item = SimpleNamespace(
        tags=[
            SimpleNamespace(tag=SimpleNamespace(name="白色")),
            SimpleNamespace(tag=None),
            SimpleNamespace(tag=SimpleNamespace(name="")),
            SimpleNamespace(tag=SimpleNamespace(name="JK")),
        ]
    )
    assert svc._tags_text(item) == "白色、JK"


def test_build_rows_contract():
    item = _item("i-1", "images/a/图.jpg", tags=("白色",), rating=None)
    item2 = _item("i-2", "", media_type="video", source_url="https://x/1")
    rows = svc._build_rows([item, item2])

    assert [row["序号"] for row in rows] == [1, 2]
    assert rows[0]["文件名"] == "图.jpg"
    assert rows[0]["收藏时间"] == "2026-01-01T08:00:00Z"
    assert rows[0]["评分"] == ""  # None → 空串
    assert rows[0]["标签"] == "白色"
    assert rows[1]["文件名"] == ""
    assert rows[1]["媒体类型"] == "video"


# ═══════════════════════════════════════════════════════════════
#  CSV / Markdown / ZIP 内容契约
# ═══════════════════════════════════════════════════════════════


def test_csv_bytes_bom_header_and_injection_sanitize():
    import csv
    import io

    rows = [
        {
            "序号": 1,
            "素材ID": "i-1",
            "文件名": "a.jpg",
            "媒体类型": "image",
            "来源": "douyin",
            "作者": "=SUM(A1)",
            "收藏时间": "",
            "评分": 5,
            "标签": "白色",
            "原链接": "https://x/1",
            "相对路径": "images/a.jpg",
        }
    ]
    data = svc._csv_bytes(rows)

    assert data.startswith("\ufeff".encode())
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert parsed[0] == svc.CSV_COLUMNS
    assert parsed[1][svc.CSV_COLUMNS.index("作者")] == "'=SUM(A1)"
    assert parsed[1][svc.CSV_COLUMNS.index("评分")] == "5"


def test_markdown_bytes_smart_and_entry_details():
    collection = svc.ExportCollection(id=1, name="智能合集", is_smart=True)
    rows = svc._build_rows(
        [
            _item("i-1", "images/a.jpg", tags=("白色", "JK"), source_url="https://x/1"),
            _item("i-2", "images/b.jpg"),  # 无标签无链接：只出标题
        ]
    )
    text = svc._markdown_bytes(collection, rows).decode("utf-8")

    assert text.startswith("# 智能合集")
    assert "- 合集类型：智能合集" in text
    assert "- 条目数：2" in text
    assert "1. **作者A**" in text
    assert "   - 标签：白色、JK" in text
    assert "   - 原链接：https://x/1" in text
    assert "   - 本地路径：`images/a.jpg`" in text
    assert "2. **作者A**" in text
    assert text.rstrip().endswith("`images/b.jpg`")


def test_zip_bytes_packs_media_and_skips_missing(storage):
    _write_image(storage, "images/ok.png")
    rows = svc._build_rows(
        [
            _item("i-1", "images/ok.png"),
            _item("i-2", "images/missing.png"),
        ]
    )

    blob, added, skipped = svc._zip_bytes(
        rows, b"manifest", svc._markdown_bytes(svc.ExportCollection(1, "c", False), rows)
    )

    assert (added, skipped) == (1, 1)
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        names = set(archive.namelist())
    assert {"manifest.csv", "README.md"} <= names
    assert "0001_作者A.png" in names
    assert not any(name.startswith("0002_") for name in names)


# ═══════════════════════════════════════════════════════════════
#  PDF：字体注册 / 图片降级 / 渲染
# ═══════════════════════════════════════════════════════════════


def test_register_pdf_font_cache_and_fallback(monkeypatch):
    monkeypatch.setattr(svc, "_PDF_FONT_NAME", "Cached")
    calls: list[str] = []
    monkeypatch.setattr(svc.pdfmetrics, "registerFont", lambda *args: calls.append(str(args)))
    assert svc._register_pdf_font() == "Cached"
    assert calls == []  # 已注册过 → 不重复注册

    monkeypatch.setattr(svc, "_PDF_FONT_NAME", None)
    monkeypatch.setattr(svc, "_PDF_FONT_CANDIDATES", ())
    assert svc._register_pdf_font() == "STSong-Light"  # ReportLab 内置 CJK CID

    monkeypatch.setattr(svc, "_PDF_FONT_NAME", None)

    def _boom(*_args):
        raise OSError("字体不可用")

    monkeypatch.setattr(svc.pdfmetrics, "registerFont", _boom)
    assert svc._register_pdf_font() == "Helvetica"  # 最后兜底英文字体


def test_ellipsize_and_wrap_text():
    font = svc._register_pdf_font()
    assert svc._ellipsize("", font, 9, 100) == ""
    assert svc._ellipsize("短", font, 9, 1000) == "短"
    long_text = "这是一个非常非常长的标签名称用于测试截断"
    ellipsized = svc._ellipsize(long_text, font, 9, 40)
    assert ellipsized.endswith("…") and svc._text_width(ellipsized, font, 9) <= 40 + 5

    assert svc._wrap_text("", font, 8, 100, 2) == []
    assert svc._wrap_text("短", font, 8, 1000, 2) == ["短"]
    wrapped = svc._wrap_text("很长的文本" * 10, font, 8, 40, 2)
    assert len(wrapped) == 2


def test_pdf_image_rgba_and_invalid_payload(storage):
    import reportlab.pdfgen.canvas as pdf_canvas

    _write_image(storage, "images/rgba.png", mode="RGBA")
    bad = storage / "images" / "bad.png"
    bad.write_bytes(b"not-a-real-png")
    (storage / "images" / "note.txt").write_text("x", encoding="utf-8")

    buffer = BytesIO()
    canvas = pdf_canvas.Canvas(buffer)
    assert svc._draw_pdf_image(canvas, "images/rgba.png", 0, 0, 100, 100) is True
    assert svc._draw_pdf_image(canvas, "images/bad.png", 0, 0, 100, 100) is False
    assert svc._draw_pdf_image(canvas, "images/note.txt", 0, 0, 100, 100) is False
    assert svc._draw_pdf_image(canvas, None, 0, 0, 100, 100) is False
    canvas.save()
    assert buffer.getvalue().startswith(b"%PDF")


def test_pdf_bytes_renders_grid_and_links(storage):
    _write_image(storage, "images/1.png")
    _write_image(storage, "images/2.png")
    rows = svc._build_rows(
        [
            _item("i-1", "images/1.png", tags=tuple(f"标签{i}" for i in range(7)), source_url="https://x/1"),
            _item("i-2", "images/missing.png"),
            _item("i-3", "images/2.png"),
            _item("i-4", "images/2.png"),
            _item("i-5", "images/2.png", source_url="ftp://bad"),
        ]
    )
    collection = svc.ExportCollection(
        id=1, name="画册", is_smart=False, description="描述", cover_relative_path="images/1.png"
    )

    blob, embedded, skipped = svc._pdf_bytes(collection, rows)

    assert blob.startswith(b"%PDF")
    assert embedded >= 3 and skipped >= 1
    assert len(blob) > 1000


# ═══════════════════════════════════════════════════════════════
#  阈值 / 过期清理 / 下载文件名
# ═══════════════════════════════════════════════════════════════


def test_archive_threshold_clamps_and_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(svc, "_archive_threshold_warned", False)
    monkeypatch.setattr(settings, "media_worker_archive_threshold", svc.MAX_EXPORT_ITEMS + 100)

    with caplog.at_level("WARNING"):
        assert svc._archive_threshold() == svc.MAX_EXPORT_ITEMS
        assert svc._archive_threshold() == svc.MAX_EXPORT_ITEMS
    # 只提示一次，避免每次导出刷屏
    assert sum("超过单次导出上限" in record.message for record in caplog.records) == 1
    assert svc._archive_threshold_warned is True

    monkeypatch.setattr(settings, "media_worker_archive_threshold", 3)
    assert svc._archive_threshold() == 3


def test_cleanup_expired_exports_branches(storage, monkeypatch):
    root = storage / "exports"
    root.mkdir()

    # 1) 无 meta 的残留目录：按 mtime + TTL 判断 → 过期删除
    stale = root / "stale-dir"
    stale.mkdir()
    old = time.time() - (svc.EXPORT_TTL_HOURS + 1) * 3600
    import os

    os.utime(stale, (old, old))

    # 2) 有未来 expires_at 的目录：保留
    fresh = root / "fresh-dir"
    fresh.mkdir()
    (fresh / "meta.json").write_text(
        json.dumps({"expires_at": (utcnow() + timedelta(hours=2)).isoformat()}),
        encoding="utf-8",
    )

    # 3) 已过期 meta：删除
    expired = root / "expired-dir"
    expired.mkdir()
    (expired / "meta.json").write_text(
        json.dumps({"expires_at": (utcnow() - timedelta(hours=1)).isoformat()}),
        encoding="utf-8",
    )

    # 4) 坏 meta + 老 mtime：走 mtime 兜底 → 删除
    broken = root / "broken-dir"
    broken.mkdir()
    (broken / "meta.json").write_text("not-json", encoding="utf-8")
    os.utime(broken, (old, old))

    # 5) 根下普通文件：跳过
    (root / "README.txt").write_text("x", encoding="utf-8")

    assert svc._cleanup_expired_exports() == 3
    remaining = {entry.name for entry in root.iterdir()}
    assert remaining == {"fresh-dir", "README.txt"}


def test_cleanup_expired_exports_tolerates_rmtree_failure(storage, monkeypatch):
    root = storage / "exports"
    stale = root / "cannot-delete"
    stale.mkdir(parents=True)
    import os

    old = time.time() - (svc.EXPORT_TTL_HOURS + 1) * 3600
    os.utime(stale, (old, old))

    def _boom(*_args, **_kwargs):
        raise OSError("占用中")

    monkeypatch.setattr(svc.shutil, "rmtree", _boom)
    assert svc._cleanup_expired_exports() == 0
    assert stale.exists()


def test_display_and_download_filename():
    collection = svc.ExportCollection(id=9, name="夏季/穿搭", is_smart=False)
    display = svc._display_filename(collection, "csv")
    assert display.startswith("夏季_穿搭_") and display.endswith(".csv")
    download = svc._download_filename(collection, "pdf")
    assert download == f"collection-9-pdf-{utcnow().strftime('%Y%m%d')}.pdf"

    unnamed = svc.ExportCollection(id=3, name="", is_smart=False)
    assert svc._display_filename(unnamed, "zip").startswith("collection-3_")


# ═══════════════════════════════════════════════════════════════
#  create_export：校验 / 四格式 / 封面 / 失败清理
# ═══════════════════════════════════════════════════════════════


async def test_create_export_validation_errors(client, storage, cleanup_counter, monkeypatch):
    db = _FakeDb()
    with pytest.raises(HTTPException) as bad_format:
        await svc.create_export(db, 1, "docx")
    assert bad_format.value.status_code == 400

    _patch_collection(monkeypatch, [], total=0)
    with pytest.raises(HTTPException) as empty:
        await svc.create_export(db, 1, "csv")
    assert empty.value.status_code == 400 and "合集为空" in empty.value.detail

    _patch_collection(monkeypatch, [], total=svc.MAX_EXPORT_ITEMS + 1)
    with pytest.raises(HTTPException) as too_many:
        await svc.create_export(db, 1, "csv")
    assert too_many.value.status_code == 400
    assert str(svc.MAX_EXPORT_ITEMS) in too_many.value.detail
    # 校验失败也要先跑清理（懒清理语义）
    assert cleanup_counter["n"] == 2


@pytest.mark.parametrize("fmt,stored_name", [("csv", "export.csv"), ("markdown", "export.md")])
async def test_create_export_text_formats(client, storage, cleanup_counter, monkeypatch, fmt, stored_name):
    items = [_item("i-1", "images/a.jpg", tags=("白色",)), _item("i-2", "images/b.jpg")]
    _patch_collection(monkeypatch, items)
    db = _FakeDb()

    result = await svc.create_export(db, 7, fmt)

    assert result["format"] == fmt
    assert result["item_count"] == 2 and result["skipped_count"] == 0
    assert result["download_url"] == f"/api/exports/{result['token']}"
    assert svc.TOKEN_RE.fullmatch(result["token"])
    assert result["size_bytes"] > 0
    assert cleanup_counter["n"] == 1
    assert db.rollbacks == 1  # 生成前结束只读事务
    export_dir = storage / "exports" / result["token"]
    assert (export_dir / stored_name).is_file()
    meta = json.loads((export_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["format"] == fmt and meta["item_count"] == 2
    assert meta["filename"].endswith(f".{svc.FORMAT_EXT[fmt]}")


async def test_create_export_zip_skips_missing(client, storage, cleanup_counter, monkeypatch):
    _write_image(storage, "images/a.png")
    items = [_item("i-1", "images/a.png"), _item("i-2", "images/missing.png")]
    _patch_collection(monkeypatch, items)

    result = await svc.create_export(_FakeDb(), 7, "zip")

    assert result["item_count"] == 1 and result["skipped_count"] == 1
    archive_path = storage / "exports" / result["token"] / "export.zip"
    with zipfile.ZipFile(archive_path) as archive:
        assert {"manifest.csv", "README.md"} <= set(archive.namelist())


async def test_create_export_pdf_embeds_images(client, storage, cleanup_counter, monkeypatch):
    _write_image(storage, "images/a.png")
    items = [_item("i-1", "images/a.png")]
    _patch_collection(monkeypatch, items)

    result = await svc.create_export(_FakeDb(), 7, "pdf")

    assert result["item_count"] == 1
    pdf_path = storage / "exports" / result["token"] / "export.pdf"
    assert pdf_path.read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize(
    "cover_id,items,expected",
    [
        ("i-2", ["i-1", "i-2"], "images/2.png"),  # 指定封面（图片素材）
        (None, ["i-1", "i-2"], "images/1.png"),  # 未指定 → 第一张图片
        (None, ["i-3", "i-5"], "videos/v.mp4"),  # 没有图片素材 → 回退首行路径
    ],
)
async def test_create_export_cover_selection(
    client, storage, cleanup_counter, monkeypatch, cover_id, items, expected
):
    item_objs = [
        _item(insp_id, "videos/v.mp4", media_type="video")
        if insp_id in {"i-3", "i-5"}
        else _item(insp_id, "images/1.png" if insp_id == "i-1" else "images/2.png")
        for insp_id in items
    ]
    _patch_collection(
        monkeypatch, item_objs, collection=_FakeCollection(cover_inspiration_id=cover_id)
    )
    captured: dict = {}

    def _fake_pdf(snapshot, _rows):
        captured["snapshot"] = snapshot
        return b"%PDF-fake", 0, 0

    monkeypatch.setattr(svc, "_pdf_bytes", _fake_pdf)
    await svc.create_export(_FakeDb(), 7, "pdf")

    assert captured["snapshot"].cover_relative_path == expected
    # 失败路径不应触发：文件存在且 meta 正常
    assert captured["snapshot"].is_smart is False


async def test_create_export_generation_failure_cleans_half_product(
    client, storage, cleanup_counter, monkeypatch
):
    _patch_collection(monkeypatch, [_item("i-1", "images/a.jpg")])

    def _boom(_rows):
        raise RuntimeError("CSV 生成炸了")

    monkeypatch.setattr(svc, "_csv_bytes", _boom)
    with pytest.raises(HTTPException) as exc:
        await svc.create_export(_FakeDb(), 7, "csv")

    assert exc.value.status_code == 500
    assert "导出生成失败" in exc.value.detail
    assert list((storage / "exports").iterdir()) == []  # 半成品目录已清理


# ═══════════════════════════════════════════════════════════════
#  get_export_file
# ═══════════════════════════════════════════════════════════════


def _make_export(storage: Path, token: str = "a" * 20, **meta_overrides) -> Path:
    export_dir = storage / "exports" / token
    export_dir.mkdir(parents=True)
    (export_dir / "export.csv").write_text("data", encoding="utf-8")
    meta = {
        "stored_file": "export.csv",
        "expires_at": (utcnow() + timedelta(hours=1)).isoformat(),
    }
    meta.update(meta_overrides)
    (export_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return export_dir


def test_get_export_file_invalid_tokens(storage):
    for bad_token in ("", "short", "包含/斜杠的token-123456", "a" * 80):
        with pytest.raises(HTTPException) as exc:
            svc.get_export_file(bad_token)
        assert exc.value.status_code == 404


def test_get_export_file_missing_and_broken_meta(storage):
    with pytest.raises(HTTPException) as missing:
        svc.get_export_file("b" * 20)
    assert missing.value.status_code == 404

    for index, stored_file in enumerate(
        (None, 123, ".hidden", "dir/export.csv", r"..\escape.csv")
    ):
        token = f"c{index:019d}"  # 每个坏 meta 用独立 token 目录
        _make_export(storage, token, stored_file=stored_file)
        with pytest.raises(HTTPException) as exc:
            svc.get_export_file(token)
        assert exc.value.status_code == 404

    token = "d" * 20
    export_dir = storage / "exports" / token
    export_dir.mkdir(parents=True)
    (export_dir / "meta.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(HTTPException):
        svc.get_export_file(token)


def test_get_export_file_expired_removes_dir(storage):
    token = "e" * 20
    export_dir = _make_export(
        storage, token, expires_at=(utcnow() - timedelta(minutes=1)).isoformat()
    )

    with pytest.raises(HTTPException) as exc:
        svc.get_export_file(token)

    assert exc.value.status_code == 404
    assert not export_dir.exists()  # 过期即清目录


def test_get_export_file_success(storage):
    token = "f" * 20
    export_dir = _make_export(storage, token)

    file_path, meta = svc.get_export_file(token)

    assert file_path == (export_dir / "export.csv").resolve()
    assert file_path.read_text(encoding="utf-8") == "data"
    assert meta["stored_file"] == "export.csv"


def test_cleanup_missing_exports_root_returns_zero(storage):
    """导出根目录不存在（从未导出过）：直接返回 0，不建目录。"""
    assert svc._cleanup_expired_exports() == 0
    assert not (storage / "exports").exists()


def test_get_export_file_missing_stored_file(storage):
    """meta 正常但导出文件已被删：404（不暴露服务器路径）。"""
    token = "g" * 20
    export_dir = _make_export(storage, token)
    (export_dir / "export.csv").unlink()

    with pytest.raises(HTTPException) as exc:
        svc.get_export_file(token)
    assert exc.value.status_code == 404


def test_register_pdf_font_candidate_load_error_falls_back(monkeypatch):
    """系统 TTF 存在但注册失败：继续尝试下一个，最终回退 Helvetica。"""
    monkeypatch.setattr(svc, "_PDF_FONT_NAME", None)
    monkeypatch.setattr(svc, "_PDF_FONT_CANDIDATES", ("C:/Windows/Fonts/simhei.ttf",))
    monkeypatch.setattr(svc.Path, "is_file", lambda _self: True)

    def _boom(*_args):
        raise OSError("字体文件损坏")

    monkeypatch.setattr(svc.pdfmetrics, "registerFont", _boom)
    assert svc._register_pdf_font() == "Helvetica"
