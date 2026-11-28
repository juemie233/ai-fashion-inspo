"""``/api/files`` 静态文件服务的安全与降级边界测试。

这个路由是存储文件唯一读出口：
- 路径越界（../ 或绝对路径）必须 403，不能把 storage 之外的文件送出去；
- 只允许图片/视频白名单扩展名并强制 MIME + nosniff，防止误入库的 HTML/SVG 被当网页执行；
- 视频关键帧接口对未知素材 404、非视频返回空列表、视频按缓存/懒提取返回 URL，
  提取失败时降级为空列表（详情页不能因为 ffmpeg 失败而 500）。
"""

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.routers import files as files_router
from app.services import video_service


def _write(rel_path: str, data: bytes = b"x") -> Path:
    path = settings.storage_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _headers_lower(response) -> dict[str, str]:
    return {k.lower(): v for k, v in dict(response.headers).items()}


async def _add_inspiration(media_type: str = "image", file_path: str = "images/a.jpg") -> str:
    async with async_session() as db:
        insp = Inspiration(
            source_type="manual_upload",
            file_path=file_path,
            media_type=media_type,
        )
        db.add(insp)
        await db.commit()
        await db.refresh(insp)
        return insp.id


async def test_serve_file_returns_media_type_and_nosniff(client):
    full = _write("images/2026-11/ok.jpg")

    response = await files_router.serve_file("images/2026-11/ok.jpg")

    assert response.media_type == "image/jpeg"
    assert _headers_lower(response)["x-content-type-options"] == "nosniff"
    assert Path(response.path) == full


async def test_serve_file_allows_uppercase_extension(client):
    _write("images/2026-11/UPPER.JPG")

    response = await files_router.serve_file("images/2026-11/UPPER.JPG")

    assert response.media_type == "image/jpeg"


@pytest.mark.parametrize(
    ("rel_path", "expected_media"),
    [
        ("videos/clip.mp4", "video/mp4"),
        ("videos/clip.mov", "video/quicktime"),
        ("images/pic.webp", "image/webp"),
    ],
)
async def test_serve_file_whitelist_variants(client, rel_path, expected_media):
    _write(rel_path)

    response = await files_router.serve_file(rel_path)

    assert response.media_type == expected_media


async def test_serve_file_rejects_parent_directory_escape(client):
    """../ 越界必须 403（同前缀兄弟目录也不能被 startswith 放行）。"""
    sibling = settings.storage_root.parent / "storage_backup" / "secret.jpg"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_bytes(b"secret")
    outside = settings.storage_root.parent / "outside.jpg"
    outside.write_bytes(b"secret")

    for rel in ("../storage_backup/secret.jpg", "../outside.jpg"):
        with pytest.raises(HTTPException) as exc:
            await files_router.serve_file(rel)
        assert exc.value.status_code == 403


async def test_serve_file_rejects_absolute_path(client):
    absolute = str(settings.storage_root.parent / "outside.jpg")
    (settings.storage_root.parent / "outside.jpg").write_bytes(b"secret")

    with pytest.raises(HTTPException) as exc:
        await files_router.serve_file(absolute)

    assert exc.value.status_code == 403


async def test_serve_file_missing_returns_404(client):
    with pytest.raises(HTTPException) as exc:
        await files_router.serve_file("images/2026-11/nope.jpg")
    assert exc.value.status_code == 404


async def test_serve_file_non_media_extension_returns_404(client):
    _write("images/evil.html", b"<script>alert(1)</script>")
    _write("images/evil.svg", b"<svg/>")

    for rel in ("images/evil.html", "images/evil.svg"):
        with pytest.raises(HTTPException) as exc:
            await files_router.serve_file(rel)
        assert exc.value.status_code == 404


async def test_serve_file_directory_returns_404(client):
    (settings.storage_root / "images" / "a-directory").mkdir(parents=True, exist_ok=True)

    with pytest.raises(HTTPException) as exc:
        await files_router.serve_file("images/a-directory")

    assert exc.value.status_code == 404


async def test_list_keyframes_unknown_inspiration_returns_404(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await files_router.list_keyframes("00000000-0000-0000-0000-000000000000", db)
    assert exc.value.status_code == 404


async def test_list_keyframes_non_video_returns_empty_frames(client):
    insp_id = await _add_inspiration(media_type="image", file_path="images/a.jpg")

    async with async_session() as db:
        data = await files_router.list_keyframes(insp_id, db)

    assert data["inspiration_id"] == insp_id
    assert data["media_type"] == "image"
    assert data["frames"] == []
    assert "count" not in data


async def test_list_keyframes_video_uses_cached_frames(client, monkeypatch):
    insp_id = await _add_inspiration(media_type="video", file_path="videos/clip.mp4")
    calls: list[str] = []

    async def _cached(_insp_id: str):
        calls.append(_insp_id)
        return [Path("0001.jpg"), Path("0002.jpg")]

    monkeypatch.setattr(video_service, "get_keyframes", _cached)
    async with async_session() as db:
        data = await files_router.list_keyframes(insp_id, db)

    assert calls == [insp_id]
    assert data["media_type"] == "video"
    assert data["count"] == 2
    assert data["frames"] == [
        f"/api/files/keyframes/{insp_id}/0001.jpg",
        f"/api/files/keyframes/{insp_id}/0002.jpg",
    ]


async def test_list_keyframes_video_lazy_extract_when_cache_empty(client, monkeypatch):
    insp_id = await _add_inspiration(media_type="video", file_path="videos/clip.mp4")
    extracted: list[str] = []

    async def _none(_insp_id: str):
        return []

    async def _extract(insp):
        extracted.append(insp.id)
        return [Path("0001.jpg")]

    monkeypatch.setattr(video_service, "get_keyframes", _none)
    monkeypatch.setattr(video_service, "extract_keyframes", _extract)
    async with async_session() as db:
        data = await files_router.list_keyframes(insp_id, db)

    assert extracted == [insp_id]
    assert data["count"] == 1
    assert data["frames"] == [f"/api/files/keyframes/{insp_id}/0001.jpg"]


async def test_list_keyframes_video_extract_failure_degrades_to_empty(client, monkeypatch):
    """视频提取失败（服务内部已降级为空）时接口返回空列表，不能 500。"""
    insp_id = await _add_inspiration(media_type="video", file_path="videos/clip.mp4")

    async def _none(_insp_id: str):
        return []

    async def _extract(_insp):
        return []

    monkeypatch.setattr(video_service, "get_keyframes", _none)
    monkeypatch.setattr(video_service, "extract_keyframes", _extract)
    async with async_session() as db:
        data = await files_router.list_keyframes(insp_id, db)

    assert data["frames"] == []
    assert data["count"] == 0
