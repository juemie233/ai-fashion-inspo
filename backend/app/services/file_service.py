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


# 允许落盘的扩展名白名单（防存储型 XSS：多态文件若以 .html/.svg 等命名，
# 被 /api/files 按扩展名返回后可在前端源执行，窃取 localStorage 中的密钥）
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _generate_filename(original_filename: str) -> str:
    """生成唯一文件名，扩展名仅允许图片/视频白名单（其余回退 .jpg）。"""
    ext = Path(original_filename or "").suffix.lower()
    if ext not in _IMAGE_EXTS and ext not in _VIDEO_EXTS:
        ext = ".jpg"
    return f"{uuid.uuid4().hex}{ext}"


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


async def _generate_video_thumbnail(
    video_path: Path, thumbs_dir: Path | None = None, thumb_prefix: str = "thumbnails"
) -> str | None:
    """用 ffmpeg 提取视频首帧作为缩略图，失败返回 None。"""
    thumbs_dir = _ensure_date_dir(thumbs_dir or settings.thumbnails_dir)
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
            return f"{thumb_prefix}/{today}/{thumb_filename}"
    except Exception:
        pass
    return None


async def generate_thumbnail(
    image_path: Path, thumbs_dir: Path | None = None, thumb_prefix: str = "thumbnails"
) -> str | None:
    """为图片或视频生成缩略图，返回相对路径。图片用 PIL，视频用 ffmpeg 提取首帧。

    参数:
        thumbs_dir: 缩略图输出根目录（缺省 settings.thumbnails_dir）
        thumb_prefix: 返回相对路径的首段（缺省 "thumbnails"，人物照片用 "person_thumbnails"）
    """
    if _is_video(image_path):
        return await _generate_video_thumbnail(image_path, thumbs_dir, thumb_prefix)

    # PIL 解码/缩放/保存是阻塞 I/O（大图需数百毫秒），放线程池执行避免卡事件循环
    return await asyncio.to_thread(
        _generate_image_thumbnail_sync, image_path, thumbs_dir, thumb_prefix
    )


def _generate_image_thumbnail_sync(
    image_path: Path, thumbs_dir: Path | None = None, thumb_prefix: str = "thumbnails"
) -> str | None:
    """同步生成图片缩略图（线程池内执行），失败返回 None。"""
    from datetime import datetime

    try:
        from PIL import Image

        thumbs_dir = _ensure_date_dir(thumbs_dir or settings.thumbnails_dir)
        img = Image.open(image_path)
        img.thumbnail(settings.thumbnail_size, Image.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        thumb_filename = f"thumb_{image_path.stem}.jpg"
        full_thumb_path = thumbs_dir / thumb_filename
        img.save(full_thumb_path, "JPEG", quality=settings.thumbnail_quality)
        today = datetime.now().strftime("%Y-%m")
        return f"{thumb_prefix}/{today}/{thumb_filename}"
    except Exception:
        return None


async def save_upload(
    file: UploadFile,
    images_dir: Path | None = None,
    thumbs_dir: Path | None = None,
    image_prefix: str = "images",
    thumb_prefix: str = "thumbnails",
) -> tuple[str, str | None]:
    """
    保存上传文件到图片目录，并生成缩略图。

    分块流式写入磁盘（避免大文件整体驻留内存），超限或类型校验失败时
    抛 400 并清理已写入的残留文件。

    参数:
        images_dir: 图片输出根目录（缺省 settings.images_dir）
        thumbs_dir: 缩略图输出根目录（缺省 settings.thumbnails_dir）
        image_prefix: 返回相对路径的首段（缺省 "images"，人物照片用 "person_photos"）
        thumb_prefix: 缩略图相对路径首段（缺省 "thumbnails"）

    返回:
        (文件相对路径, 缩略图相对路径或None)
    """
    images_dir = _ensure_date_dir(images_dir or settings.images_dir)

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

    # 校验真实文件类型（PIL 完整解码是阻塞 I/O，放线程池执行）并生成缩略图。
    # 校验失败会抛 400，此时清理已落盘文件，避免每次失败都残留孤儿文件。
    try:
        await asyncio.to_thread(validate_media, file_path, file.content_type)
        thumb_path = await generate_thumbnail(file_path, thumbs_dir, thumb_prefix)
    except Exception:
        try:
            if file_path.exists():
                file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    today = datetime.now().strftime("%Y-%m")
    rel_file_path = f"{image_prefix}/{today}/{filename}"

    return rel_file_path, thumb_path


def delete_files(file_path: str, thumbnail_path: str | None = None) -> None:
    """从磁盘删除文件及其缩略图（带错误日志，不抛异常）。

    收敛实现：委托 delete_files_counting 完成物理删除（不统计返回值）。
    """
    delete_files_counting(file_path, thumbnail_path)


def delete_files_counting(*paths: str | None, storage_root: Path | None = None) -> int:
    """删除多个存储相对路径对应的文件，返回释放的字节数（失败仅记日志）。

    统一「物理删除 + 统计释放空间」的重复实现（此前散落在 delete_rejected /
    purge_trash / deduplicate / batch_delete 等 4 处，且部分静默吞异常）。
    storage_root 缺省为 settings.storage_root，测试可传入临时目录。
    """
    root = storage_root or settings.storage_root
    freed = 0
    for rel in paths:
        if not rel:
            continue
        full = root / rel
        try:
            if full.exists():
                freed += full.stat().st_size
                full.unlink()
        except Exception as e:
            _log.warning(f"删除文件失败（忽略）: {full} — {e}")
    return freed


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
