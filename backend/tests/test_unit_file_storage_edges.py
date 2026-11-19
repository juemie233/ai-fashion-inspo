"""文件服务边界分支测试：上传超限/校验失败清理、视频缩略图降级、垃圾桶搬移与恢复。

补充 ``test_files.py`` / ``test_unit_image_utils.py`` 未覆盖的降级与异常分支。
垃圾桶文件搬移/恢复是软删除主链路的一环：搬移失败会导致恢复后图片 404，
因此这里重点锁住「移动/恢复 + 目标撞名 + 跨卷回退」三类行为。
"""

from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from app.config import settings
from app.services import file_service, video_service


class _FakeUpload:
    """最小 UploadFile 替身：按块返回内容，避免构造 Starlette 的 SpooledTemporaryFile。"""

    def __init__(
        self,
        chunks: list[bytes],
        filename: str = "photo.jpg",
        content_type: str = "image/jpeg",
    ):
        self._chunks = list(chunks)
        self.filename = filename
        self.content_type = content_type

    async def read(self, size: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


def _png_bytes(tmp_path: Path) -> bytes:
    """生成一张真实 PNG（save_upload 会走 PIL 完整解码校验）。"""
    src = tmp_path / "src.png"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(src, "PNG")
    return src.read_bytes()


def _patch_storage(monkeypatch, tmp_path: Path) -> Path:
    """把 settings 的存储路径整体指向 tmp_path，避免污染会话级临时存储。"""
    storage = tmp_path / "storage"
    mapping = {
        "storage_root": storage,
        "images_dir": storage / "images",
        "thumbnails_dir": storage / "thumbnails",
        "videos_dir": storage / "videos",
        "trash_dir": storage / "trash",
    }
    for name, value in mapping.items():
        monkeypatch.setattr(settings, name, value)
    return storage


# ── save_upload：超限与校验失败的残留清理 ──


async def test_save_upload_rejects_oversize_and_cleans_residue(tmp_path, monkeypatch):
    """超过大小上限：抛 400 且不残留半截文件（否则每次失败都留孤儿文件）。"""
    images = tmp_path / "images"
    monkeypatch.setattr(file_service, "resolve_size_limit", lambda content_type: 4)

    upload = _FakeUpload([b"0123456789"])
    with pytest.raises(HTTPException) as exc:
        await file_service.save_upload(upload, images_dir=images, thumbs_dir=tmp_path / "thumbs")

    assert exc.value.status_code == 400
    assert "大小限制" in exc.value.detail
    assert [p for p in images.rglob("*") if p.is_file()] == []


async def test_save_upload_cleans_file_when_media_validation_fails(tmp_path, monkeypatch):
    """真实类型校验失败：抛 400 且删除已落盘文件，不留孤儿。"""
    images = tmp_path / "images"

    def _reject(path, content_type=None):  # noqa: ANN001, ARG001
        raise HTTPException(status_code=400, detail="文件不是有效的图片")

    monkeypatch.setattr(file_service, "validate_media", _reject)
    upload = _FakeUpload([_png_bytes(tmp_path)])
    with pytest.raises(HTTPException) as exc:
        await file_service.save_upload(upload, images_dir=images, thumbs_dir=tmp_path / "thumbs")

    assert exc.value.status_code == 400
    assert [p for p in images.rglob("*") if p.is_file()] == []


async def test_save_upload_success_writes_file_and_thumbnail(tmp_path):
    """成功路径：返回 images/ 与 thumbnails/ 相对路径，主文件与缩略图都真实落盘。"""
    images, thumbs = tmp_path / "images", tmp_path / "thumbs"
    upload = _FakeUpload([_png_bytes(tmp_path)])

    rel, thumb = await file_service.save_upload(upload, images_dir=images, thumbs_dir=thumbs)

    assert rel.startswith("images/")
    assert thumb is not None and thumb.startswith("thumbnails/")
    stored = images / Path(rel).relative_to("images")
    assert stored.is_file()
    thumb_file = thumbs / Path(thumb).relative_to("thumbnails")
    assert thumb_file.is_file()
    with Image.open(thumb_file) as im:
        im.verify()  # 有效 JPEG


# ── 缩略图：RGBA 转 RGB、损坏文件降级、视频 ffmpeg 超时/失败 ──


def test_image_thumbnail_sync_converts_rgba_to_rgb(tmp_path):
    """RGBA/P 模式图必须转 RGB 再存 JPEG（否则 PIL 保存抛错、缩略图缺失）。"""
    src = tmp_path / "rgba.png"
    Image.new("RGBA", (16, 16), (1, 2, 3, 128)).save(src, "PNG")

    rel = file_service._generate_image_thumbnail_sync(src, tmp_path / "thumbs")

    assert rel is not None and rel.startswith("thumbnails/")
    thumb = tmp_path / "thumbs" / Path(rel).relative_to("thumbnails")
    with Image.open(thumb) as im:
        assert im.mode == "RGB"


def test_image_thumbnail_sync_returns_none_on_corrupt_file(tmp_path):
    """损坏文件生成缩略图返回 None（静默降级，不影响上传主流程）。"""
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not-an-image")

    assert file_service._generate_image_thumbnail_sync(bad, tmp_path / "thumbs") is None


class _FakeProc:
    """ffmpeg 子进程替身：可模拟「首次 wait 超时」。"""

    def __init__(self, timeout_first: bool = False):
        self.timeout_first = timeout_first
        self.killed = False
        self.wait_calls = 0

    async def wait(self) -> int:
        self.wait_calls += 1
        if self.timeout_first and self.wait_calls == 1:
            raise TimeoutError
        return 0

    def kill(self) -> None:
        self.killed = True


async def test_video_thumbnail_timeout_kills_and_reaps_process(tmp_path, monkeypatch):
    """ffmpeg 超时要 kill 并回收子进程（否则残留僵尸进程），返回 None。"""
    proc = _FakeProc(timeout_first=True)

    async def _fake_exec(*cmd, **kwargs):  # noqa: ANN002, ANN003, ARG001
        return proc

    monkeypatch.setattr(file_service.asyncio, "create_subprocess_exec", _fake_exec)

    rel = await file_service._generate_video_thumbnail(tmp_path / "clip.mp4", tmp_path / "thumbs")

    assert rel is None
    assert proc.killed is True
    assert proc.wait_calls == 2  # wait_for 一次 + kill 后回收一次


async def test_video_thumbnail_success_returns_relative_path(tmp_path, monkeypatch):
    """ffmpeg 成功且产物非空时返回相对路径（前缀可由调用方指定）。"""
    proc = _FakeProc()

    async def _fake_exec(*cmd, **kwargs):  # noqa: ANN002, ANN003, ARG001
        Path(cmd[-1]).write_bytes(b"thumb-bytes")
        return proc

    monkeypatch.setattr(file_service.asyncio, "create_subprocess_exec", _fake_exec)

    rel = await file_service._generate_video_thumbnail(
        tmp_path / "clip.mp4", tmp_path / "thumbs", thumb_prefix="person_thumbnails"
    )

    assert rel is not None and rel.startswith("person_thumbnails/")
    assert (tmp_path / "thumbs" / Path(rel).relative_to("person_thumbnails")).is_file()


async def test_video_thumbnail_returns_none_when_spawn_fails(tmp_path, monkeypatch):
    """ffmpeg 都起不来（未安装/无权限）时静默降级返回 None。"""

    async def _boom(*cmd, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("ffmpeg 不可用")

    monkeypatch.setattr(file_service.asyncio, "create_subprocess_exec", _boom)

    assert await file_service._generate_video_thumbnail(tmp_path / "clip.mp4", tmp_path / "thumbs") is None


# ── delete_files_counting / get_full_path ──


def test_delete_files_counting_counts_freed_bytes_and_skips_missing(tmp_path):
    """只统计真实存在的文件；空/None/不存在路径跳过，不抛异常。"""
    (tmp_path / "a.bin").write_bytes(b"12345")
    (tmp_path / "b.bin").write_bytes(b"12")

    freed = file_service.delete_files_counting(
        "a.bin", "", None, "missing.bin", storage_root=tmp_path
    )

    assert freed == 5
    assert not (tmp_path / "a.bin").exists()
    assert (tmp_path / "b.bin").exists()


def test_delete_files_counting_ignores_unlink_errors(tmp_path):
    """目录 unlink 会抛 OSError：只能记日志，不能打断删除主流程。"""
    stubborn = tmp_path / "dir.jpg"
    stubborn.mkdir()

    file_service.delete_files_counting("dir.jpg", storage_root=tmp_path)

    assert stubborn.is_dir()  # 未被删除，但调用方没有收到异常


def test_get_full_path_joins_storage_root():
    assert file_service.get_full_path("images/a.jpg") == settings.storage_root / "images/a.jpg"


# ── 垃圾桶搬移（软删除物理文件移动）──


def test_move_to_trash_moves_file_with_suffix(monkeypatch, tmp_path):
    """主文件/缩略图后缀区分：垃圾桶内文件名 = {id}{suffix}{ext}。"""
    storage = _patch_storage(monkeypatch, tmp_path)
    src = storage / "images" / "2026-01" / "a.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"img-bytes")

    rel = file_service.move_to_trash("images/2026-01/a.jpg", "abc123", "_thumb")

    assert rel == "trash/abc123_thumb.jpg"
    assert (storage / rel).read_bytes() == b"img-bytes"
    assert not src.exists()


def test_move_to_trash_avoids_overwriting_existing_target(monkeypatch, tmp_path):
    """目标已存在（同 id 重复移入）时加随机后缀，绝不覆盖既有垃圾桶文件。"""
    storage = _patch_storage(monkeypatch, tmp_path)
    src = storage / "images" / "a.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"new")
    existing = storage / "trash" / "abc.jpg"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old")

    rel = file_service.move_to_trash("images/a.jpg", "abc")

    assert rel is not None and rel != "trash/abc.jpg"
    assert Path(rel).name.startswith("abc_")
    assert existing.read_bytes() == b"old"  # 原文件未被覆盖
    assert (storage / rel).read_bytes() == b"new"


def test_move_to_trash_returns_none_for_empty_or_missing(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    assert file_service.move_to_trash("", "abc") is None
    assert file_service.move_to_trash("images/none.jpg", "abc") is None


def test_move_to_trash_falls_back_to_copy_on_cross_volume(monkeypatch, tmp_path):
    """跨卷 rename 抛 OSError 时回退 copy2 + unlink，文件不能丢。"""
    storage = _patch_storage(monkeypatch, tmp_path)
    src = storage / "images" / "a.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"payload")

    def _rename_raises(self, target):  # noqa: ANN001, ARG001
        raise OSError("跨卷移动失败")

    monkeypatch.setattr(Path, "rename", _rename_raises)

    rel = file_service.move_to_trash("images/a.jpg", "abc")

    assert rel is not None
    assert (storage / rel).read_bytes() == b"payload"
    assert not src.exists()


# ── 垃圾桶恢复（软删除回滚的物理文件侧）──


def test_restore_from_trash_routes_by_file_kind(monkeypatch, tmp_path):
    """缩略图 → thumbnails/、视频 → videos/、图片 → images/。"""
    storage = _patch_storage(monkeypatch, tmp_path)
    trash = storage / "trash"
    trash.mkdir(parents=True)
    for name in ("abc_thumb.jpg", "clip.mp4", "pic.jpg"):
        (trash / name).write_bytes(b"x")

    thumb_rel = file_service.restore_from_trash("trash/abc_thumb.jpg")
    video_rel = file_service.restore_from_trash("trash/clip.mp4")
    image_rel = file_service.restore_from_trash("trash/pic.jpg")

    assert thumb_rel is not None and thumb_rel.startswith("thumbnails/")
    assert video_rel is not None and video_rel.startswith("videos/")
    assert image_rel is not None and image_rel.startswith("images/")
    assert not (trash / "abc_thumb.jpg").exists()


def test_restore_from_trash_avoids_overwriting_same_name(monkeypatch, tmp_path):
    """同月恢复同名文件时加随机后缀，不覆盖已有素材。"""
    storage = _patch_storage(monkeypatch, tmp_path)
    trash = storage / "trash"
    trash.mkdir(parents=True)
    (trash / "pic.jpg").write_bytes(b"restored")
    today = datetime.now().strftime("%Y-%m")
    target_dir = storage / "images" / today
    target_dir.mkdir(parents=True)
    (target_dir / "pic.jpg").write_bytes(b"existing")

    rel = file_service.restore_from_trash("trash/pic.jpg")

    assert rel is not None and rel.startswith("images/")
    assert Path(rel).name != "pic.jpg"
    assert "_" in Path(rel).name
    assert (storage / "images" / today / "pic.jpg").read_bytes() == b"existing"
    assert (storage / rel).read_bytes() == b"restored"


def test_restore_from_trash_returns_none_for_empty_or_missing(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    assert file_service.restore_from_trash("") is None
    assert file_service.restore_from_trash("trash/none.jpg") is None


def test_restore_from_trash_falls_back_to_copy_on_cross_volume(monkeypatch, tmp_path):
    storage = _patch_storage(monkeypatch, tmp_path)
    trash = storage / "trash"
    trash.mkdir(parents=True)
    (trash / "pic.jpg").write_bytes(b"payload")

    def _rename_raises(self, target):  # noqa: ANN001, ARG001
        raise OSError("跨卷移动失败")

    monkeypatch.setattr(Path, "rename", _rename_raises)

    rel = file_service.restore_from_trash("trash/pic.jpg")

    assert rel is not None and (storage / rel).read_bytes() == b"payload"
    assert not (trash / "pic.jpg").exists()


# ── /api/files/keyframes 路由（懒提取与降级）──


def _fake_mp4_bytes() -> bytes:
    """构造带 MP4 ftyp 魔数的假视频（validate_media 只校验魔数，解码交给 ffmpeg）。"""
    import uuid

    return b"\x00\x00\x00\x18ftypmp42" + uuid.uuid4().hex.encode()


def _upload_video(client, monkeypatch):
    """上传一个视频素材；屏蔽真实 ffmpeg 缩略图与关键帧预热。"""
    from app.services import inspiration_create

    async def _noop_thumbnail(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        return None

    monkeypatch.setattr(file_service, "generate_thumbnail", _noop_thumbnail)
    monkeypatch.setattr(inspiration_create, "generate_thumbnail", _noop_thumbnail)
    monkeypatch.setattr(video_service, "prewarm_keyframes", lambda insp: None)

    r = client.post(
        "/api/inspirations",
        files={"file": ("clip.mp4", _fake_mp4_bytes(), "video/mp4")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["media_type"] == "video"
    return body


def test_keyframes_video_lazy_extract_returns_urls(client, make_image, monkeypatch):
    """首次访问视频：get_keyframes 为空 → 懒提取 → 返回帧 URL 列表。"""
    body = _upload_video(client, monkeypatch)

    async def _empty(inspiration_id):  # noqa: ANN001, ARG001
        return []

    async def _extract(insp):  # noqa: ANN001, ARG001
        return [Path("frame_007.jpg")]

    monkeypatch.setattr(video_service, "get_keyframes", _empty)
    monkeypatch.setattr(video_service, "extract_keyframes", _extract)

    r = client.get(f"/api/files/keyframes/{body['id']}")

    assert r.status_code == 200
    payload = r.json()
    assert payload["media_type"] == "video"
    assert payload["count"] == 1
    assert payload["frames"] == [f"/api/files/keyframes/{body['id']}/frame_007.jpg"]


def test_keyframes_video_skips_extract_when_already_present(client, make_image, monkeypatch):
    """已有关键帧时不再触发 ffmpeg 提取（避免详情页反复重跑）。"""
    body = _upload_video(client, monkeypatch)
    extract_called = {"n": 0}

    async def _present(inspiration_id):  # noqa: ANN001, ARG001
        return [Path("frame_001.jpg")]

    async def _extract(insp):  # noqa: ANN001, ARG001
        extract_called["n"] += 1
        return []

    monkeypatch.setattr(video_service, "get_keyframes", _present)
    monkeypatch.setattr(video_service, "extract_keyframes", _extract)

    r = client.get(f"/api/files/keyframes/{body['id']}")

    assert r.status_code == 200
    assert extract_called["n"] == 0
    assert r.json()["frames"] == [f"/api/files/keyframes/{body['id']}/frame_001.jpg"]


def test_keyframes_empty_for_non_video(client, upload):
    """非视频素材返回空 frames 而不报错（前端无需特判）。"""
    insp = upload().json()
    r = client.get(f"/api/files/keyframes/{insp['id']}")
    assert r.status_code == 200
    assert r.json()["frames"] == []
    assert r.json()["media_type"] != "video"


def test_keyframes_unknown_id_returns_404(client):
    r = client.get("/api/files/keyframes/not-a-real-id")
    assert r.status_code == 404
