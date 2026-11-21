"""合集导出测试（2026-11-20 P2）：CSV / Markdown / ZIP / PDF。

覆盖：四格式生成与下载、CSV BOM/列契约、ZIP 清单与缺失文件跳过、PDF 文件头、
空合集/超 500 条 400、智能合集、垃圾桶排除、token 非法/路径穿越/过期 404、
生成前清理过期导出。
"""

import io
import json
import zipfile

from app.config import settings
from app.database import async_session
from app.models.collection import CollectionItem
from app.models.inspiration import Inspiration
from app.services import collection_export_service
from app.utils.time import utcnow


def _upload(client, upload) -> str:
    resp = upload()
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_collection(client, name: str, query_json: dict | None = None) -> int:
    body: dict = {"name": name}
    if query_json is not None:
        body["query_json"] = query_json
    resp = client.post("/api/collections", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_members(client, collection_id: int, inspiration_ids: list[str]) -> None:
    resp = client.post(
        f"/api/collections/{collection_id}/inspirations",
        json={"inspiration_ids": inspiration_ids},
    )
    assert resp.status_code == 200, resp.text


async def _seed_missing_member(collection_id: int, insp_id: str) -> None:
    """造一条「文件不存在」的素材并加入合集（用于 skipped 统计）。"""
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="manual_upload",
                file_path=f"images/{insp_id}-missing.jpg",
                media_type="image",
                quality_status="approved",
            )
        )
        db.add(
            CollectionItem(
                collection_id=collection_id,
                inspiration_id=insp_id,
                position=999,
            )
        )
        await db.commit()


async def _seed_bulk_members(collection_id: int, count: int) -> None:
    """直接建库造 count 条素材并挂到合集（用于 >500 上限测试）。"""
    async with async_session() as db:
        for index in range(count):
            insp_id = f"bulk-{index:04d}"
            db.add(
                Inspiration(
                    id=insp_id,
                    source_type="manual_upload",
                    file_path=f"images/{insp_id}.jpg",
                    media_type="image",
                    quality_status="approved",
                )
            )
            db.add(
                CollectionItem(
                    collection_id=collection_id,
                    inspiration_id=insp_id,
                    position=index,
                )
            )
        await db.commit()


def _export(client, collection_id: int, fmt: str):
    return client.post(
        f"/api/collections/{collection_id}/exports", json={"format": fmt}
    )


def _export_ok(client, collection_id: int, fmt: str) -> dict:
    resp = _export(client, collection_id, fmt)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_export_csv(client, upload):
    first = _upload(client, upload)
    second = _upload(client, upload)
    collection_id = _create_collection(client, "CSV 合集")
    _add_members(client, collection_id, [first, second])

    data = _export_ok(client, collection_id, "csv")
    assert data["format"] == "csv"
    assert data["token"] and data["download_url"] == f"/api/exports/{data['token']}"
    assert data["expires_at"].endswith("Z")
    assert data["filename"].endswith(".csv")
    assert data["item_count"] == 2
    assert data["skipped_count"] == 0

    download = client.get(data["download_url"])
    assert download.status_code == 200, download.text
    content = download.content
    assert content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text = content.decode("utf-8-sig")
    header = text.splitlines()[0]
    for column in [
        "序号",
        "素材ID",
        "文件名",
        "媒体类型",
        "来源",
        "作者",
        "收藏时间",
        "评分",
        "标签",
        "原链接",
        "相对路径",
    ]:
        assert column in header
    assert first in text and second in text
    assert data["size_bytes"] == len(content)
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "collection-" in download.headers["content-disposition"]


async def test_export_markdown(client, upload):
    insp_id = _upload(client, upload)
    collection_id = _create_collection(client, "Markdown 合集")
    _add_members(client, collection_id, [insp_id])

    data = _export_ok(client, collection_id, "markdown")
    assert data["filename"].endswith(".md")
    download = client.get(data["download_url"])
    assert download.status_code == 200
    text = download.content.decode("utf-8")
    assert text.startswith("# Markdown 合集")
    assert "## 合集信息" in text and "## 条目" in text
    assert "images/" in text
    assert "本地路径" in text


async def test_export_zip_with_manifest_and_missing_skipped(client, upload):
    present = _upload(client, upload)
    collection_id = _create_collection(client, "ZIP 合集")
    _add_members(client, collection_id, [present])
    await _seed_missing_member(collection_id, "missing-file")

    data = _export_ok(client, collection_id, "zip")
    assert data["item_count"] == 1
    assert data["skipped_count"] == 1
    download = client.get(data["download_url"])
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = archive.namelist()
        assert "manifest.csv" in names
        assert "README.md" in names
        media = [name for name in names if name not in ("manifest.csv", "README.md")]
        assert len(media) == 1
        assert media[0].startswith("0001_")
        manifest = archive.read("manifest.csv").decode("utf-8-sig")
        assert "序号" in manifest and "missing-file" in manifest


async def test_export_pdf(client, upload):
    collection_id = _create_collection(client, "PDF 合集")
    _add_members(
        client, collection_id, [_upload(client, upload), _upload(client, upload)]
    )

    data = _export_ok(client, collection_id, "pdf")
    assert data["format"] == "pdf"
    assert data["filename"].endswith(".pdf")
    assert data["item_count"] == 2
    download = client.get(data["download_url"])
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF")
    assert data["size_bytes"] == len(download.content)


async def test_export_empty_and_limit(client):
    empty = _create_collection(client, "空合集")
    resp = _export(client, empty, "csv")
    assert resp.status_code == 400
    assert "合集为空" in resp.json()["detail"]

    over_limit = _create_collection(client, "超限合集")
    await _seed_bulk_members(over_limit, 501)
    resp = _export(client, over_limit, "csv")
    assert resp.status_code == 400
    assert "500" in resp.json()["detail"]
    assert "分批" in resp.json()["detail"]


async def test_export_smart_collection_and_trash_exclusion(client, upload):
    favorite = _upload(client, upload)
    plain = _upload(client, upload)
    client.patch(
        f"/api/inspirations/{favorite}", json={"is_favorite": True}
    ).raise_for_status()

    smart = _create_collection(client, "智能导出", {"is_favorite": True})
    data = _export_ok(client, smart, "csv")
    assert data["item_count"] == 1
    text = client.get(data["download_url"]).content.decode("utf-8-sig")
    assert favorite in text
    assert plain not in text

    # 手动合集里被移入垃圾桶的素材不导出（保留一条正常素材，避免合集变空）
    manual = _create_collection(client, "含垃圾桶")
    kept = _upload(client, upload)
    trashed = _upload(client, upload)
    _add_members(client, manual, [kept, trashed])
    client.post(f"/api/inspirations/{trashed}/trash")
    manual_data = _export_ok(client, manual, "csv")
    assert manual_data["item_count"] == 1
    manual_text = client.get(manual_data["download_url"]).content.decode("utf-8-sig")
    assert kept in manual_text
    assert trashed not in manual_text


async def test_export_bad_format_and_unknown_collection(client, upload):
    collection_id = _create_collection(client, "格式校验")
    _add_members(client, collection_id, [_upload(client, upload)])
    assert _export(client, collection_id, "docx").status_code == 422
    assert _export(client, 999999, "csv").status_code == 404


async def test_export_token_safety_and_expiry(client, upload):
    # 路径穿越/非法 token → 404（不会读到 storage_root 之外）
    for token in (
        "..%2F..%2Fetc%2Fpasswd",
        "..%2Fmeta.json",
        "short",
        "bad token",
        "x" * 100,
    ):
        assert client.get(f"/api/exports/{token}").status_code == 404, token
    assert client.get("/api/exports/does-not-exist-token-123").status_code == 404

    collection_id = _create_collection(client, "过期测试")
    _add_members(client, collection_id, [_upload(client, upload)])
    data = _export_ok(client, collection_id, "csv")

    # 手动把 meta 的过期时间改到过去 → 下载 404 且目录被清理
    meta_path = settings.storage_root / "exports" / data["token"] / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["expires_at"] = utcnow().replace(year=2020).isoformat()
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    assert client.get(data["download_url"]).status_code == 404
    assert not meta_path.parent.exists()


async def test_export_cleanup_old_exports_on_create(client, upload):
    collection_id = _create_collection(client, "清理测试")
    _add_members(client, collection_id, [_upload(client, upload)])
    old = _export_ok(client, collection_id, "csv")
    old_meta = settings.storage_root / "exports" / old["token"] / "meta.json"
    meta = json.loads(old_meta.read_text(encoding="utf-8"))
    meta["expires_at"] = utcnow().replace(year=2020).isoformat()
    old_meta.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    _export_ok(client, collection_id, "csv")
    assert not old_meta.parent.exists()


async def test_export_pdf_skips_unreadable_and_keeps_valid(client, upload):
    """PDF 逐张渲染：不可读素材计入 skipped，不影响其余图片嵌入（不再整批解码）。"""
    present = _upload(client, upload)
    collection_id = _create_collection(client, "PDF 跳过")
    _add_members(client, collection_id, [present])
    await _seed_missing_member(collection_id, "missing-pdf")

    data = _export_ok(client, collection_id, "pdf")
    assert data["item_count"] == 1
    assert data["skipped_count"] == 1
    download = client.get(data["download_url"])
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF")


async def test_export_zip_falls_back_when_rust_sidecar_fails(
    client, upload, monkeypatch
):
    """Rust ZIP 失败/不可用时必须删除半成品并回退 Python，不能影响导出可用性。"""
    collection_id = _create_collection(client, "Rust 回退")
    _add_members(
        client, collection_id, [_upload(client, upload), _upload(client, upload)]
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("模拟 sidecar 崩溃")

    monkeypatch.setattr(collection_export_service, "_zip_bytes_via_media_worker", boom)
    monkeypatch.setattr(settings, "media_worker_bin", "fake-media-worker.exe")
    monkeypatch.setattr(settings, "media_worker_archive_threshold", 1)

    data = _export_ok(client, collection_id, "zip")
    assert data["item_count"] == 2
    assert data["skipped_count"] == 0
    download = client.get(data["download_url"])
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.namelist()[:2] == ["manifest.csv", "README.md"]
        assert len(archive.namelist()) == 4


async def test_export_broken_meta_does_not_break_download_or_cleanup(client, upload):
    """meta.json 字段类型损坏：下载 404（不是 500），之后的生成导出也不受影响。"""
    collection_id = _create_collection(client, "坏 meta")
    _add_members(client, collection_id, [_upload(client, upload)])
    data = _export_ok(client, collection_id, "csv")

    meta_path = settings.storage_root / "exports" / data["token"] / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["expires_at"] = 123  # 非字符串：fromisoformat 会抛 TypeError
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    assert client.get(data["download_url"]).status_code == 404
    # 清理函数扫到坏 meta 也不能抛错，否则之后每次生成导出都 500
    assert _export(client, collection_id, "csv").status_code == 200
