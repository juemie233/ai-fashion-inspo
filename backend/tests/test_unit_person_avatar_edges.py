"""人物头像（`services/person/avatar.py`）边界补测。

既有 `tests/test_blogger_avatar.py` 走 API 主路径；这里直接调服务函数，补齐：
图片预处理/编码降级、删文件失败容忍、404/400 错误分支、人脸特写与整图兜底、
清除头像、文件落盘覆盖，以及 `avatar_file_exists` / `avatar_dir` 等小工具。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from app.config import settings
from app.database import async_session
from app.models.person import Blogger
from app.services.person import avatar as avatar_module
from app.services.person.avatar import (
    AVATAR_DIR,
    AVATAR_MAX_SIDE,
    AVATAR_QUALITY,
    avatar_dir,
    avatar_file_exists,
    avatar_rel_path,
    clear_blogger_avatar,
    delete_avatar_file,
    prepare_avatar_image,
    save_avatar_bytes,
    set_blogger_avatar,
)


def _jpeg_bytes(size=(1000, 800), color=(120, 60, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


async def _add_blogger(name: str = "头像博主") -> int:
    async with async_session() as db:
        row = Blogger(name=name, platform="xiaohongshu")
        db.add(row)
        await db.commit()
        return row.id


# ── 图片预处理与文件工具 ──


def test_prepare_avatar_image_resizes_and_reencodes():
    out = prepare_avatar_image(_jpeg_bytes(size=(1000, 800)))
    assert out is not None
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "JPEG"
        assert max(img.size) == AVATAR_MAX_SIDE


def test_prepare_avatar_image_keeps_small_image():
    out = prepare_avatar_image(_jpeg_bytes(size=(120, 80)))
    assert out is not None
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (120, 80)


def test_prepare_avatar_image_returns_none_for_undecodable():
    assert prepare_avatar_image(b"definitely-not-an-image") is None


def test_prepare_avatar_image_returns_none_on_encode_error(monkeypatch):
    """归一化通过但 PIL 打不开（编码异常）：返回 None 而不是抛异常。"""
    monkeypatch.setattr(avatar_module, "normalize_image", lambda _data: b"broken")
    assert prepare_avatar_image(b"whatever") is None


def test_avatar_path_helpers():
    assert AVATAR_DIR == "avatars"
    assert AVATAR_QUALITY == 92
    assert avatar_rel_path(7) == "avatars/avatar_7.jpg"
    assert avatar_dir() == settings.storage_root / AVATAR_DIR
    assert avatar_file_exists(7) is False

    path = settings.storage_root / avatar_rel_path(7)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    assert avatar_file_exists(7) is True


def test_delete_avatar_file_is_idempotent_and_swallows_oserror(monkeypatch):
    person_id = 31
    path = settings.storage_root / avatar_rel_path(person_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"avatar")
    delete_avatar_file(person_id)
    assert not path.exists()
    delete_avatar_file(person_id)  # 再删一次不抛

    def _boom(*_args, **_kwargs):
        raise OSError("文件被占用")

    monkeypatch.setattr(Path, "unlink", _boom)
    delete_avatar_file(person_id)  # 仅记日志，不向上抛


async def test_save_avatar_bytes_writes_and_overwrites():
    blogger_id = await _add_blogger("落盘博主")
    async with async_session() as db:
        blogger = await db.get(Blogger, blogger_id)
        rel = await save_avatar_bytes(db, blogger, b"first-bytes")
        assert rel == avatar_rel_path(blogger_id)
        target = settings.storage_root / rel
        assert target.read_bytes() == b"first-bytes"

        # 第二次写入同名文件（覆盖式，不产生孤儿文件）
        await save_avatar_bytes(db, blogger, b"second-bytes")
        assert target.read_bytes() == b"second-bytes"
        assert blogger.avatar_path == rel


# ── set_blogger_avatar 的错误与成功分支 ──


async def test_set_avatar_blogger_not_found():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await set_blogger_avatar(db, 999999, _jpeg_bytes())
    assert exc.value.status_code == 404
    assert exc.value.detail == "博主未找到"


async def test_set_avatar_rejects_undecodable_image():
    blogger_id = await _add_blogger("坏图博主")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await set_blogger_avatar(db, blogger_id, b"not-an-image")
    assert exc.value.status_code == 400
    assert "无法解析" in exc.value.detail


async def test_set_avatar_400_when_both_face_and_fallback_fail(monkeypatch):
    blogger_id = await _add_blogger("兜底失败博主")

    async def _no_face(_bytes, size=None):
        return None

    monkeypatch.setattr("app.services.face_crop.face_avatar", _no_face)
    monkeypatch.setattr(avatar_module, "prepare_avatar_image", lambda _data: None)

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await set_blogger_avatar(db, blogger_id, _jpeg_bytes())
    assert exc.value.status_code == 400
    assert "处理失败" in exc.value.detail


async def test_set_avatar_uses_face_crop_when_available(monkeypatch):
    blogger_id = await _add_blogger("人脸特写博主")

    async def _fake_face_avatar(_bytes, size=None):
        return b"FACE-CROP-JPEG"

    monkeypatch.setattr("app.services.face_crop.face_avatar", _fake_face_avatar)

    async with async_session() as db:
        out = await set_blogger_avatar(db, blogger_id, _jpeg_bytes(), source="upload")

    assert out["face_cropped"] is True
    assert out["avatar_path"] == avatar_rel_path(blogger_id)
    assert out["message"] == "已提取照片里的人脸作为头像"
    assert out["blogger"]["name"] == "人脸特写博主"
    assert (settings.storage_root / out["avatar_path"]).read_bytes() == b"FACE-CROP-JPEG"

    async with async_session() as db:
        assert (await db.get(Blogger, blogger_id)).avatar_path == out["avatar_path"]


async def test_set_avatar_falls_back_to_whole_photo(monkeypatch):
    blogger_id = await _add_blogger("整图头像博主")

    async def _no_face(_bytes, size=None):
        return None

    monkeypatch.setattr("app.services.face_crop.face_avatar", _no_face)

    async with async_session() as db:
        out = await set_blogger_avatar(db, blogger_id, _jpeg_bytes(size=(900, 300)))

    assert out["face_cropped"] is False
    assert out["message"] == "照片里没检出人脸，已用整张照片作为头像"
    with Image.open(settings.storage_root / out["avatar_path"]) as img:
        assert img.format == "JPEG"
        assert max(img.size) <= AVATAR_MAX_SIDE


# ── clear_blogger_avatar ──


async def test_clear_avatar_removes_file_and_column():
    blogger_id = await _add_blogger("清空头像博主")
    rel = avatar_rel_path(blogger_id)
    target = settings.storage_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"avatar")
    async with async_session() as db:
        row = await db.get(Blogger, blogger_id)
        row.avatar_path = rel
        await db.commit()

    async with async_session() as db:
        out = await clear_blogger_avatar(db, blogger_id)

    assert out == {
        "avatar_path": None,
        "face_cropped": False,
        "message": "已清除头像，列表与详情将显示名字首字占位",
    }
    assert not target.exists()
    async with async_session() as db:
        assert (await db.get(Blogger, blogger_id)).avatar_path is None


async def test_clear_avatar_blogger_not_found():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await clear_blogger_avatar(db, 888888)
    assert exc.value.status_code == 404
