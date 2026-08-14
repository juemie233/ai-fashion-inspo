"""文件服务：上传保存、缩略图生成、静态文件管理。"""

import asyncio
import uuid
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import UploadFile

from app.config import settings


def _ensure_date_dir(base: Path) -> Path:
    """确保存在按月份命名的子目录，返回该路径。"""
    today = datetime.now().strftime("%Y-%m")
    dir_path = base / today
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def _generate_filename(original_filename: str) -> str:
    """生成唯一文件名，保留原始扩展名。"""
    ext = Path(original_filename).suffix.lower() or ".jpg"
    return f"{uuid.uuid4().hex}{ext}"


_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _is_video(path: Path) -> bool:
    """判断文件是否为视频（按扩展名）。"""
    return path.suffix.lower() in _VIDEO_EXTS


async def _generate_video_thumbnail(video_path: Path) -> str | None:
    """用 ffmpeg 提取视频首帧作为缩略图，失败返回 None。"""
    thumbs_dir = _ensure_date_dir(settings.thumbnails_dir)
    thumb_filename = f"thumb_{video_path.stem}.jpg"
    full_thumb_path = thumbs_dir / thumb_filename
    cmd = [
        "ffmpeg", "-y",
        "-ss", "1",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", "scale=400:-2",
        str(full_thumb_path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if full_thumb_path.exists() and full_thumb_path.stat().st_size > 0:
            today = datetime.now().strftime("%Y-%m")
            return f"thumbnails/{today}/{thumb_filename}"
    except Exception:
        pass
    return None


async def generate_thumbnail(image_path: Path) -> str | None:
    """为图片或视频生成缩略图，返回相对路径。图片用 PIL，视频用 ffmpeg 提取首帧。"""
    from datetime import datetime

    if _is_video(image_path):
        return await _generate_video_thumbnail(image_path)

    try:
        from PIL import Image

        thumbs_dir = _ensure_date_dir(settings.thumbnails_dir)
        img = Image.open(image_path)
        img.thumbnail(settings.thumbnail_size, Image.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        thumb_filename = f"thumb_{image_path.stem}.jpg"
        full_thumb_path = thumbs_dir / thumb_filename
        img.save(full_thumb_path, "JPEG", quality=settings.thumbnail_quality)
        today = datetime.now().strftime("%Y-%m")
        return f"thumbnails/{today}/{thumb_filename}"
    except Exception:
        return None


async def save_upload(file: UploadFile) -> tuple[str, str | None]:
    """
    保存上传文件到图片目录，并生成缩略图。

    返回:
        (文件相对路径, 缩略图相对路径或None)
    """
    images_dir = _ensure_date_dir(settings.images_dir)

    filename = _generate_filename(file.filename or "upload.jpg")
    file_path = images_dir / filename

    # 保存原始文件
    content = await file.read()
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    # 生成缩略图（图片用 PIL，视频用 ffmpeg 提取首帧）
    thumb_path = await generate_thumbnail(file_path)

    today = datetime.now().strftime("%Y-%m")
    rel_file_path = f"images/{today}/{filename}"

    return rel_file_path, thumb_path


def delete_files(file_path: str, thumbnail_path: str | None = None):
    """从磁盘删除文件及其缩略图（带错误日志，不抛异常）。"""
    import logging
    _log = logging.getLogger(__name__)

    if file_path:
        full_path = settings.storage_root / file_path
        try:
            if full_path.exists():
                full_path.unlink()
        except Exception as e:
            _log.warning(f"删除文件失败: {full_path} — {e}")

    if thumbnail_path:
        full_thumb = settings.storage_root / thumbnail_path
        try:
            if full_thumb.exists():
                full_thumb.unlink()
        except Exception as e:
            _log.warning(f"删除缩略图失败: {full_thumb} — {e}")


def get_full_path(relative_path: str) -> Path:
    """将存储相对路径转换为绝对文件系统路径。"""
    return settings.storage_root / relative_path


async def save_from_url(url: str) -> tuple[str, str | None] | None:
    """
    从 URL 下载图片并保存到本地。

    返回:
        (文件相对路径, 缩略图相对路径)，失败则返回 None
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()

        images_dir = _ensure_date_dir(settings.images_dir)

        # 根据 Content-Type 确定扩展名
        content_type = response.headers.get("content-type", "")
        ext_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        ext = ext_map.get(content_type, Path(url).suffix or ".jpg")
        filename = f"{uuid.uuid4().hex}{ext}"
        file_path = images_dir / filename

        async with aiofiles.open(file_path, "wb") as f:
            await f.write(response.content)

        # 生成缩略图（图片用 PIL，视频用 ffmpeg 提取首帧）
        thumb_path = await generate_thumbnail(file_path)

        today = datetime.now().strftime("%Y-%m")
        rel_file_path = f"images/{today}/{filename}"
        return rel_file_path, thumb_path
    except Exception:
        return None
