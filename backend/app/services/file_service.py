"""文件服务：上传保存、缩略图生成、静态文件管理。"""

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile

from app.config import settings

_log = logging.getLogger(__name__)

# 分块读取大小：大文件分块流式落盘，避免整体驻留内存
_CHUNK_SIZE = 1024 * 1024  # 1MB

# ffmpeg 缩略图提取超时（秒）：损坏/怪异视频可能让 ffmpeg 永久挂起
_FFMPEG_TIMEOUT = 30


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


def resolve_size_limit(content_type: str | None) -> int:
    """根据媒体类型返回上传字节数上限（视频/图片分别配置）。"""
    if content_type and content_type.startswith("video/"):
        return settings.max_video_upload_mb * 1024 * 1024
    return settings.max_image_upload_mb * 1024 * 1024


def validate_media(path: Path, content_type: str | None = None) -> None:
    """校验文件真实类型：图片需能被 PIL 解码，视频需带 MP4 魔数。

    与声明/扩展名不符时抛 400，由调用方负责清理文件。
    校验失败的文件不入库，避免下游 PIL/ffmpeg/AI 分析链路连环报错。
    """
    is_video = bool(content_type and content_type.startswith("video/")) or _is_video(path)

    if is_video:
        # 视频：粗检 ftyp box（MP4/MOV 通用头），完整解码交给 ffmpeg
        try:
            with open(path, "rb") as f:
                head = f.read(12)
        except OSError as e:
            _log.warning(f"视频魔数读取失败: {path} — {e}")
            raise HTTPException(status_code=400, detail="文件不是有效的视频（无法读取）")
        if b"ftyp" not in head:
            raise HTTPException(status_code=400, detail="文件不是有效的视频（缺少 MP4 头）")
        return

    # 图片：PIL 完整解码校验（能识别损坏/截断/伪装文件）
    try:
        from PIL import Image

        with Image.open(path) as img:
            img.verify()
    except Exception as e:
        _log.warning(f"图片校验失败: {path} — {e}")
        raise HTTPException(status_code=400, detail="文件不是有效的图片，请检查文件是否损坏")


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
        # 超时强制终止，避免损坏视频让上传请求永久挂起
        try:
            await asyncio.wait_for(proc.wait(), timeout=_FFMPEG_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            _log.warning(f"ffmpeg 缩略图提取超时，已终止: {video_path}")
            return None
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

    分块流式写入磁盘（避免大文件整体驻留内存），超限或类型校验失败时
    抛 400 并清理已写入的残留文件。

    返回:
        (文件相对路径, 缩略图相对路径或None)
    """
    images_dir = _ensure_date_dir(settings.images_dir)

    filename = _generate_filename(file.filename or "upload.jpg")
    file_path = images_dir / filename

    size_limit = resolve_size_limit(file.content_type)
    total = 0
    try:
        # 保存原始文件（流式分块）
        async with aiofiles.open(file_path, "wb") as f:
            while chunk := await file.read(_CHUNK_SIZE):
                total += len(chunk)
                if total > size_limit:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"文件超过大小限制"
                            f"（{size_limit // (1024 * 1024)}MB，按媒体类型配置）"
                        ),
                    )
                await f.write(chunk)
    except Exception:
        # 写入失败或超限时清理残留文件
        try:
            if file_path.exists():
                file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    # 校验真实文件类型（图片需能通过 PIL 解码，视频需带 MP4 魔数）
    validate_media(file_path, file.content_type)

    # 生成缩略图（图片用 PIL，视频用 ffmpeg 提取首帧）
    thumb_path = await generate_thumbnail(file_path)

    today = datetime.now().strftime("%Y-%m")
    rel_file_path = f"images/{today}/{filename}"

    return rel_file_path, thumb_path


def delete_files(file_path: str, thumbnail_path: str | None = None):
    """从磁盘删除文件及其缩略图（带错误日志，不抛异常）。"""
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
