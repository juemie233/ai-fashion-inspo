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
            # kill 后必须回收子进程，否则留下僵尸进程
            try:
                await proc.wait()
            except Exception:
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
    if _is_video(image_path):
        return await _generate_video_thumbnail(image_path)

    # PIL 解码/缩放/保存是阻塞 I/O（大图需数百毫秒），放线程池执行避免卡事件循环
    return await asyncio.to_thread(_generate_image_thumbnail_sync, image_path)


def _generate_image_thumbnail_sync(image_path: Path) -> str | None:
    """同步生成图片缩略图（线程池内执行），失败返回 None。"""
    from datetime import datetime

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

    # 校验真实文件类型：PIL 完整解码是阻塞 I/O（大图耗时数百毫秒），放线程池执行
    await asyncio.to_thread(validate_media, file_path, file.content_type)

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


def move_to_trash(rel_path: str, inspiration_id: str, suffix: str = "") -> str | None:
    """将单个文件移动到垃圾桶目录，返回新的相对路径；文件不存在时返回 None。

    参数:
        rel_path: 待移动文件的存储相对路径（如 ``images/2026-01/abc.jpg``）
        inspiration_id: 素材 UUID（作为垃圾桶内文件名，便于按素材追溯）
        suffix: 文件名后缀（如 ``_thumb`` 用于区分缩略图，避免与主文件同名覆盖）

    说明:
        移动后文件位于 ``storage/trash/{inspiration_id}{suffix}{ext}``，
        与原始目录解耦，保证完整性检查（只扫描 images/thumbnails/videos）不会
        把垃圾桶文件误判为孤立文件。
    """
    if not rel_path:
        return None
    src = settings.storage_root / rel_path
    if not src.exists():
        return None

    settings.trash_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower() or ".bin"
    dst = settings.trash_dir / f"{inspiration_id}{suffix}{ext}"
    # 目标已存在（罕见：同 id 重复移入）时加随机后缀避免覆盖
    if dst.exists():
        dst = settings.trash_dir / f"{inspiration_id}{suffix}_{uuid.uuid4().hex[:6]}{ext}"

    try:
        src.rename(dst)
    except OSError:
        # 跨卷 rename 可能失败（不同磁盘/权限），回退 copy + 删除
        import shutil

        shutil.copy2(src, dst)
        src.unlink(missing_ok=True)

    return dst.relative_to(settings.storage_root).as_posix()


def restore_from_trash(rel_path: str) -> str | None:
    """将垃圾桶目录中的文件移回对应媒体目录，返回新的相对路径。

    根据文件名后缀与扩展名推断目标目录：
    - ``{id}_thumb{ext}`` → ``thumbnails/{月份}/``
    - 视频扩展名 → ``videos/{月份}/``
    - 其余（图片） → ``images/{月份}/``

    文件不存在时返回 None（调用方据此容错，不阻断恢复流程）。
    """
    if not rel_path:
        return None
    src = settings.storage_root / rel_path
    if not src.exists():
        return None

    stem = src.stem  # 不含扩展名
    ext = src.suffix.lower() or ".bin"
    # 缩略图命名为 ``{id}_thumb{ext}``；撞名兜底时带随机后缀 ``{id}_thumb_{hex}{ext}``，
    # 用「包含 _thumb」判断（素材 id 为纯 hex 不含下划线，不会误判主文件）
    is_thumb = "_thumb" in stem

    if is_thumb:
        base = _ensure_date_dir(settings.thumbnails_dir)
        prefix = "thumbnails"
    elif _is_video(src):
        base = _ensure_date_dir(settings.videos_dir)
        prefix = "videos"
    else:
        base = _ensure_date_dir(settings.images_dir)
        prefix = "images"

    dst = base / f"{stem}{ext}"
    # 目标已存在时加随机后缀，避免覆盖同月已恢复的同名文件
    if dst.exists():
        dst = base / f"{stem}_{uuid.uuid4().hex[:6]}{ext}"

    try:
        src.rename(dst)
    except OSError:
        import shutil

        shutil.copy2(src, dst)
        src.unlink(missing_ok=True)

    today = datetime.now().strftime("%Y-%m")
    return f"{prefix}/{today}/{dst.name}"
