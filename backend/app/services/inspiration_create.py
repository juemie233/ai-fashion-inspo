"""灵感素材创建：上传入库、URL 下载导入。

入库前的平台 ID 查重、墓碑检查、任务校验与内容哈希去重统一委托给
inspiration_dedupe，两条创建路径共用同一套「先查重后落盘」语义。
"""

import asyncio
import ipaddress
import logging
import shutil
import socket
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import ValidationException, validation_error
from app.services import file_service
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag
from app.services.file_service import (
    _ensure_date_dir,
    _generate_filename,
    delete_files,
    generate_thumbnail,
    save_upload,
)
from app.services.inspiration_dedupe import (
    check_platform_id_duplicate,
    check_scraper_task_exists,
    check_tombstone,
    find_duplicate_by_hash,
)
from app.services.tag_service import get_or_create_tag
from app.utils.file_hash import file_sha256

logger = logging.getLogger(__name__)

def _file_size(path: Path) -> int | None:
    """返回文件字节数；文件缺失/无权限返回 None（不阻塞入库）。"""
    try:
        return path.stat().st_size
    except OSError as exc:
        logger.debug("_file_size: 忽略异常（预期降级）：%s", exc)
        return None


# 下载图片用的浏览器 UA：平台图片 CDN（小红书/抖音 xhscdn 等）对非浏览器
# 请求（默认 httpx UA）常返回 403 或占位图，右键保存/URL 导入依赖此头
DOWNLOAD_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


_ALLOWED_DOWNLOAD_SCHEMES = frozenset({"http", "https"})
_MAX_DOWNLOAD_REDIRECTS = 5
_DOWNLOAD_TOTAL_TIMEOUT = 300.0


def _validate_download_url(url: str) -> None:
    """校验下载 URL 目标地址，拒绝 SSRF 高危目标（回环/私网/链路本地等）。

    这里校验的是**当前一跳**的 URL；下载时必须关闭自动重定向，对每一跳重新
    调用本函数（见 ``_open_checked``），否则公网 URL 302 到元数据地址即可绕过。
    DNS 解析为阻塞操作，调用方应放入线程池执行。

    异常:
        HTTPException(400): 地址非法、无法解析或指向内网/保留地址
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise validation_error("url", "无效的下载地址")
    if parsed.scheme not in _ALLOWED_DOWNLOAD_SCHEMES:
        raise validation_error("url", "只支持 http/https 下载地址")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise validation_error("url", "下载地址无法解析") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # IPv4-mapped IPv6（::ffff:127.0.0.1）：Python 3.13 之前直接判断属性可能漏
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        # is_global 覆盖 CGNAT 100.64.0.0/10（阿里云元数据 100.100.100.200）
        if not ip.is_global:
            raise validation_error(
                "url",
                "下载地址指向内网/保留地址，已拒绝（SSRF 防护）"
            )


async def _open_checked(client, url: str):
    """手动跟随重定向，并对每一跳重新做 SSRF 校验。

    返回 (stream_context, response)；调用方负责在读完响应后关闭 stream context。
    使用 ``client.stream`` 而不是 ``client.get``，保持下载的流式边界。
    """
    for _ in range(_MAX_DOWNLOAD_REDIRECTS + 1):
        await asyncio.to_thread(_validate_download_url, url)
        stream_ctx = client.stream("GET", url)
        resp = await stream_ctx.__aenter__()
        if not getattr(resp, "is_redirect", False):
            return stream_ctx, resp
        next_request = getattr(resp, "next_request", None)
        await stream_ctx.__aexit__(None, None, None)
        if next_request is None:
            raise validation_error("url", "下载地址重定向缺少目标")
        url = str(next_request.url)
    raise validation_error("url", "下载地址重定向次数过多")


async def create_inspiration_from_file(
    db: AsyncSession,
    *,
    src_path: Path,
    source_type: str = "generated",
    generation_run_id: int | None = None,
    is_ai_generated: bool = False,
    tag_ids: list[int] | None = None,
    caption: str | None = None,
) -> Inspiration:
    """把**磁盘上已有的图片**入库（本地出图的确认入库走这里）。

    与 :func:`create_inspiration` 的区别只在入口：后者从 ``UploadFile`` 流式落盘，
    这里直接复制已有文件。入库之后的五件事（缩略图 / sha256 / 内容去重 / 质量状态 /
    向量回填登记）**完全共用**，避免两条入口各自踩一遍同样的坑：

    1. 先复制再校验：源文件在候选区，复制失败/校验失败时清理副本，候选文件不受影响
       （用户可以重试或只丢副本，不该因为入库失败把生成的图也弄丢）；
    2. 内容哈希去重：同一张图重复确认入库 → 抛 409（前端按「已存在，跳过」处理）；
    3. 生成图默认 ``quality_status='pending'``：质量参差，必须过审核，不自动通过；
    4. 登记向量回填：与上传一致，等打标后由任务重建文本/图像向量；
    5. 标签：可选的 ``tag_ids`` 直接挂上（从 prompt 反推时顺便带过来）。

    Args:
        src_path: 源图片绝对路径（必须在 storage_root 内，候选目录下的 png）。
        source_type: 素材来源类型，默认 ``generated``。
        generation_run_id: 出图批次 id（写入 ``inspirations.generation_run_id``）。
        is_ai_generated: 是否标记「疑似 AI 生成」。
        tag_ids: 可选，入库时直接关联的标签。
        caption: 可选，正文/描述（生成图可存 prompt，便于后续检索）。

    Raises:
        HTTPException(409): 内容已存在。
        HTTPException(400): 文件不是有效图片或来源路径越界。
    """
    storage_root = settings.storage_root.resolve()
    try:
        src_resolved = src_path.resolve()
        src_resolved.relative_to(storage_root)
    except (OSError, ValueError):
        raise validation_error("file", "源文件路径不在素材存储目录内") from None

    dest_dir = _ensure_date_dir(settings.images_dir)
    filename = _generate_filename(src_path.name)
    dest = dest_dir / filename
    rel_path = dest.relative_to(settings.storage_root).as_posix()
    thumb_path: str | None = None
    ok = False
    try:
        await asyncio.to_thread(shutil.copy2, src_resolved, dest)
        await asyncio.to_thread(file_service.validate_media, dest, None)
        thumb_path = await generate_thumbnail(dest, settings.thumbnails_dir)
        file_size = await asyncio.to_thread(_file_size, dest)

        content_hash = await asyncio.to_thread(file_sha256, dest)
        if content_hash and await find_duplicate_by_hash(db, content_hash):
            raise HTTPException(status_code=409, detail="该素材已存在（内容重复）")

        clean_tag_ids = list(dict.fromkeys(tag_ids or []))
        inspiration = Inspiration(
            source_type=source_type,
            file_path=rel_path,
            file_size=file_size,
            thumbnail_path=thumb_path,
            content_hash=content_hash,
            media_type="image",
            quality_status="pending",
            is_ai_generated=is_ai_generated,
            generation_run_id=generation_run_id,
            caption=caption,
        )
        db.add(inspiration)
        try:
            await db.flush()
        except IntegrityError:
            raise HTTPException(status_code=409, detail="该素材已存在") from None
        await db.refresh(inspiration)

        if clean_tag_ids:
            for tag_id in clean_tag_ids:
                db.add(
                    InspirationTag(
                        inspiration_id=inspiration.id,
                        tag_id=tag_id,
                        source="manual",
                    )
                )
            try:
                await db.flush()
            except IntegrityError:
                raise HTTPException(
                    status_code=400, detail="标签不存在或重复，请刷新后重试"
                ) from None

        try:
            from app.services.task_runners.vector_backfill import (
                enqueue_vector_backfills,
            )

            await enqueue_vector_backfills(db, [inspiration.id])
        except Exception as e:
            logger.warning("登记向量回填失败（忽略，不影响入库）: %s", e)

        ok = True
        return inspiration
    finally:
        if not ok:
            # 复制中途失败 / 校验 / 哈希 / 去重 / DB flush / 打标签任一失败，
            # 都清掉副本与缩略图；CancelledError 也会经过这里。
            delete_files(rel_path, thumb_path)


async def create_inspiration(
    db: AsyncSession,
    file: UploadFile,
    source_type: str = "manual_upload",
    source_url: str | None = None,
    source_author: str | None = None,
    source_platform_id: str | None = None,
    scraper_task_id: int | None = None,
) -> Inspiration:
    """上传图片并创建灵感素材，含平台 ID 查重与内容哈希去重。"""
    await check_platform_id_duplicate(db, source_platform_id)
    await check_scraper_task_exists(db, scraper_task_id)
    await check_tombstone(db, [source_url])

    file_path, thumb_path = await save_upload(file)
    ok = False
    try:
        content_hash = await asyncio.to_thread(
            file_sha256, settings.storage_root / file_path
        )
        file_size = await asyncio.to_thread(
            _file_size, settings.storage_root / file_path
        )
        if content_hash and await find_duplicate_by_hash(db, content_hash):
            raise HTTPException(status_code=409, detail="该素材已存在（内容重复）")

        # 以落盘后的真实扩展名判断媒体类型，不信任客户端声明的 content_type
        media_type = (
            "video"
            if Path(file_path).suffix.lower()
            in {".mp4", ".mov", ".avi", ".mkv", ".webm"}
            else "image"
        )

        # 手动上传默认免审核：按配置直接标记为已通过，跳过质量审核队列
        quality_status = (
            "approved"
            if source_type == "manual_upload" and settings.manual_upload_auto_approve
            else "pending"
        )

        inspiration = Inspiration(
            source_type=source_type,
            source_url=source_url,
            source_author=source_author,
            source_platform_id=source_platform_id,
            file_path=file_path,
            file_size=file_size,
            thumbnail_path=thumb_path,
            content_hash=content_hash,
            media_type=media_type,
            quality_status=quality_status,
            scraper_task_id=scraper_task_id,
        )
        db.add(inspiration)
        try:
            await db.flush()
        except IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="该素材已存在（平台 ID 冲突或其它唯一约束冲突）",
            ) from None
        await db.refresh(inspiration)

        if media_type == "video":
            from app.services.video_service import prewarm_keyframes

            prewarm_keyframes(inspiration)

        try:
            from app.services.task_runners.vector_backfill import (
                enqueue_vector_backfills,
            )

            await enqueue_vector_backfills(db, [inspiration.id])
        except Exception as e:
            logger.warning("登记向量回填失败（忽略，不影响上传）: %s", e)

        ok = True
        return inspiration
    finally:
        if not ok:
            # 哈希/去重/DB flush/登记等任一后续步骤失败，清理已保存文件；
            # CancelledError 也会经过这里。
            delete_files(file_path, thumb_path)


async def create_inspiration_from_url(
    db: AsyncSession,
    url: str,
    source_author: str | None = None,
    tag_names: list[str] | None = None,
    source_type: str = "url_import",
    source_url: str | None = None,
    source_platform_id: str | None = None,
    scraper_task_id: int | None = None,
) -> Inspiration:
    """从 URL 下载图片并创建素材，支持关联标签。

    浏览器插件采集链路复用本函数：服务端直接下载平台图片，
    规避浏览器扩展跨域下载图片的 CORS/授权限制。
    """
    import aiofiles
    import httpx

    from app.services.file_service import resolve_size_limit

    # 标签入口统一去重/去空，避免同一个 tag 生成两条 InspirationTag
    # 撞联合唯一约束，也避免创建空名称标签
    raw_tag_names = tag_names if isinstance(tag_names, list) else []
    clean_tag_names = list(
        dict.fromkeys(
            name.strip()
            for name in raw_tag_names
            if isinstance(name, str) and name.strip()
        )
    )

    # 先查重，避免下载文件后再发现重复留下孤儿文件
    await check_platform_id_duplicate(db, source_platform_id)
    await check_scraper_task_exists(db, scraper_task_id)
    await check_tombstone(db, [source_url, url])

    file_path_obj: Path | None = None
    rel_path: str | None = None
    thumb_path: str | None = None
    is_video_content = False
    ok = False
    try:
        headers = {"User-Agent": DOWNLOAD_USER_AGENT}
        if source_url:
            headers["Referer"] = source_url
        # 目录在校验之后创建；被 SSRF 拒绝的请求不再留下空目录
        async with asyncio.timeout(_DOWNLOAD_TOTAL_TIMEOUT):
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=False, headers=headers
            ) as client:
                stream_ctx, resp = await _open_checked(client, url)
                try:
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "")
                    is_video_content = content_type.startswith("video/")

                    ext = ".mp4" if is_video_content else ".jpg"
                    if not is_video_content:
                        if "png" in content_type:
                            ext = ".png"
                        elif "webp" in content_type:
                            ext = ".webp"
                        elif "gif" in content_type:
                            ext = ".gif"

                    day_dir = _ensure_date_dir(settings.images_dir)
                    filename = f"{uuid.uuid4().hex}{ext}"
                    file_path_obj = day_dir / filename
                    rel_path = file_path_obj.relative_to(
                        settings.storage_root
                    ).as_posix()
                    size_limit = resolve_size_limit(content_type)

                    content_length = resp.headers.get("content-length")
                    if (
                        content_length
                        and content_length.isdigit()
                        and int(content_length) > size_limit
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=f"文件超过大小限制（{size_limit // (1024 * 1024)}MB）",
                        )

                    total = 0
                    async with aiofiles.open(file_path_obj, "wb") as f:
                        async for chunk in resp.aiter_bytes(1024 * 1024):
                            total += len(chunk)
                            if total > size_limit:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"文件超过大小限制（{size_limit // (1024 * 1024)}MB）",
                                )
                            await f.write(chunk)
                finally:
                    try:
                        close = getattr(resp, "aclose", None)
                        if close is not None:
                            await close()
                    finally:
                        await stream_ctx.__aexit__(None, None, None)

        # 校验真实文件类型（PIL 解码是阻塞 I/O，放线程池执行）
        await asyncio.to_thread(file_service.validate_media, file_path_obj, content_type)
        thumb_path = await generate_thumbnail(file_path_obj)

        content_hash = await asyncio.to_thread(file_sha256, file_path_obj)
        file_size = await asyncio.to_thread(_file_size, file_path_obj)
        if not content_hash:
            logger.warning("URL 导入内容哈希计算失败（文件可能损坏）: %s", rel_path)
        elif await find_duplicate_by_hash(db, content_hash):
            raise HTTPException(status_code=409, detail="该素材已存在（内容重复）")

        media_type = "video" if is_video_content else "image"
        inspiration = Inspiration(
            source_type=source_type,
            source_url=source_url or url,
            source_author=source_author,
            source_platform_id=source_platform_id,
            file_path=rel_path,
            file_size=file_size,
            thumbnail_path=thumb_path,
            content_hash=content_hash,
            media_type=media_type,
            quality_status="pending",
            scraper_task_id=scraper_task_id,
        )
        db.add(inspiration)
        try:
            await db.flush()
        except IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="该素材已存在（平台 ID 冲突或其它唯一约束冲突）",
            ) from None
        await db.refresh(inspiration)

        if clean_tag_names:
            linked_tag_ids: set[int] = set()
            for tname in clean_tag_names:
                tag = await get_or_create_tag(db, tname, "free")
                # 归一化（DB 别名/大小写）后可能落到同一个 tag，避免重复关联
                if tag.id in linked_tag_ids:
                    continue
                linked_tag_ids.add(tag.id)
                db.add(
                    InspirationTag(
                        inspiration_id=inspiration.id,
                        tag_id=tag.id,
                        confidence=1.0,
                        source="manual",
                    )
                )
            try:
                await db.flush()
            except IntegrityError:
                raise HTTPException(status_code=400, detail="标签关联冲突，请重试") from None

        if media_type == "video":
            from app.services.video_service import prewarm_keyframes

            prewarm_keyframes(inspiration)

        try:
            from app.services.task_runners.vector_backfill import (
                enqueue_vector_backfills,
            )

            await enqueue_vector_backfills(db, [inspiration.id])
        except Exception as e:
            logger.warning("登记向量回填失败（忽略，不影响导入）: %s", e)

        ok = True
        return inspiration
    except (HTTPException, ValidationException):
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise HTTPException(
            status_code=400, detail="下载超时，请检查 URL 是否可访问"
        ) from None
    except httpx.HTTPStatusError:
        raise HTTPException(
            status_code=400, detail="下载失败：目标地址返回非 2xx 状态码"
        ) from None
    except Exception:
        logger.exception("URL 下载/入库失败: %s", urlparse(url).hostname or "unknown")
        raise HTTPException(
            status_code=400, detail="下载失败，请检查 URL 是否可访问"
        ) from None
    finally:
        if not ok and rel_path:
            # 覆盖缩略图/哈希/DB flush/标签关联等所有失败路径；CancelledError
            # 也会经过 here，避免半写文件残留。
            delete_files(rel_path, thumb_path)
