"""灵感素材创建：上传入库、URL 下载导入。

入库前的平台 ID 查重、墓碑检查、任务校验与内容哈希去重统一委托给
inspiration_dedupe，两条创建路径共用同一套「先查重后落盘」语义。
"""

import asyncio
import ipaddress
import logging
import socket
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag
from app.services.file_service import (
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


def _validate_download_url(url: str) -> None:
    """校验下载 URL 目标地址，拒绝 SSRF 高危目标（回环/私网/链路本地等）。

    URL 导入与浏览器插件采集链路会向本接口投递任意 URL，若不拦截内网地址，
    恶意 URL 可诱导服务端访问内网服务或云元数据接口（169.254.169.254 等）。
    DNS 解析为阻塞操作，调用方应放入线程池执行。

    异常:
        HTTPException(400): 地址非法、无法解析或指向内网/保留地址
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="无效的下载地址")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="下载地址无法解析")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise HTTPException(
                status_code=400,
                detail="下载地址指向内网/保留地址，已拒绝（SSRF 防护）",
            )


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
    # 检查重复（按平台 ID）—— 先查重，避免保存文件后再发现重复留下孤儿文件
    await check_platform_id_duplicate(db, source_platform_id)

    # 关联采集任务校验：插件采集链路传 task_id，避免产生指向不存在任务的孤儿记录
    await check_scraper_task_exists(db, scraper_task_id)

    # 墓碑检查：来源 URL 曾被删除过（如采集结果删除），不再重新入库
    await check_tombstone(db, [source_url])

    # 保存文件
    file_path, thumb_path = await save_upload(file)

    # 内容去重：计算 SHA-256 并全库比对，避免同一素材重复入库
    # 哈希大文件是阻塞 I/O（500MB 视频需数秒），放线程池执行
    content_hash = await asyncio.to_thread(
        file_sha256, settings.storage_root / file_path
    )
    if content_hash and await find_duplicate_by_hash(db, content_hash):
        delete_files(file_path, thumb_path)  # 清理刚保存的重复文件与缩略图
        raise HTTPException(status_code=409, detail="该素材已存在（内容重复）")

    # 判断媒体类型
    media_type = "image"
    if file.content_type and file.content_type.startswith("video/"):
        media_type = "video"

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
        # 并发下撞唯一约束（如平台 ID 部分唯一索引）：清理已落盘文件后转 409，
        # 避免残留孤儿文件与 500
        delete_files(file_path, thumb_path)
        raise HTTPException(status_code=409, detail="该素材已存在（平台 ID 冲突）")
    await db.refresh(inspiration)

    # 入库后登记向量回填（攒批）：素材 ID 进入待回填队列，累计达到阈值（100）后
    # 统一创建批量任务，避免「每上传一个素材就创建一个 total=1 小任务」。
    # 文本向量需等标签生成后才有内容，无标签时由任务内部自动跳过（后续 AI 分析
    # 完成时再重建）。登记失败（如任务表不可用）不影响上传主流程，仅记日志降级。
    # enqueue 不内部提交：登记行与素材行随 get_db 统一提交
    try:
        from app.services.task_runners.vector_backfill import enqueue_vector_backfills

        await enqueue_vector_backfills(db, [inspiration.id])
    except Exception as e:
        logger.warning(f"登记向量回填失败（忽略，不影响上传）: {e}")

    return inspiration


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

    from app.services.file_service import resolve_size_limit, validate_media

    tag_names = tag_names or []

    # 检查重复（按平台 ID）：先查重，避免下载文件后再发现重复留下孤儿文件
    # （与 create_inspiration 的语义一致：垃圾桶素材释放平台 ID）
    await check_platform_id_duplicate(db, source_platform_id)

    # 关联采集任务校验：插件采集链路传 task_id，避免指向不存在任务的孤儿记录
    await check_scraper_task_exists(db, scraper_task_id)

    # 墓碑检查：来源 URL 曾被删除过（如采集结果删除），不再重新入库。
    # source_url 为显式来源页地址（优先）、url 为图片地址，两者任一命中即拒绝
    await check_tombstone(db, [source_url, url])

    # 下载图片：流式落盘 + 大小限制（按响应 Content-Type 区分图片/视频上限）
    images_dir = settings.images_dir
    images_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m")
    day_dir = images_dir / today
    day_dir.mkdir(parents=True, exist_ok=True)

    # SSRF 防护：下载前解析目标地址，拒绝回环/私网/链路本地等内网目标
    # （插件采集链路会投递任意 URL，不拦截会让服务端请求内网/云元数据接口）
    await asyncio.to_thread(_validate_download_url, url)

    filename: str | None = None
    file_path_obj: Path | None = None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                is_video_content = content_type.startswith("video/")

                # 从 Content-Type 推断扩展名（视频统一 .mp4，避免非 mp4 视频
                # Content-Type 落成 .jpg 扩展名与 media_type 错位）
                ext = ".mp4" if is_video_content else ".jpg"
                if not is_video_content:
                    if "png" in content_type:
                        ext = ".png"
                    elif "webp" in content_type:
                        ext = ".webp"
                    elif "gif" in content_type:
                        ext = ".gif"

                size_limit = resolve_size_limit(content_type)
                filename = f"{uuid.uuid4().hex}{ext}"
                file_path_obj = day_dir / filename

                # 先按 Content-Length 预检，再流式写入并实时校验
                content_length = resp.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > size_limit:
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
    except HTTPException:
        # 大小超限等业务异常：清理残留文件后原样抛出
        if file_path_obj and file_path_obj.exists():
            file_path_obj.unlink(missing_ok=True)
        raise
    except httpx.TimeoutException:
        # 流式读取中途超时：清理半写文件，避免残留孤儿文件
        if file_path_obj and file_path_obj.exists():
            file_path_obj.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="下载超时，请检查 URL 是否可访问")
    except httpx.HTTPStatusError:
        if file_path_obj and file_path_obj.exists():
            file_path_obj.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="下载失败：目标地址返回非 2xx 状态码")
    except Exception:
        # 网络/磁盘等其它异常：清理半写文件；内部细节只记日志，不泄露给客户端
        logger.exception(f"URL 下载失败: {url[:100]}")
        if file_path_obj and file_path_obj.exists():
            file_path_obj.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="下载失败，请检查 URL 是否可访问")

    # 校验真实文件类型（PIL 解码是阻塞 I/O，放线程池执行）
    try:
        await asyncio.to_thread(validate_media, file_path_obj, content_type)
    except HTTPException:
        if file_path_obj.exists():
            file_path_obj.unlink(missing_ok=True)
        raise

    # 生成缩略图
    rel_path = f"images/{today}/{filename}"
    thumb_path = await generate_thumbnail(file_path_obj)

    # 内容去重：计算 SHA-256 并全库比对，避免同一素材重复入库（线程池执行）
    content_hash = await asyncio.to_thread(file_sha256, file_path_obj)
    if not content_hash:
        # 哈希失败（磁盘读取异常）：记日志便于发现，不阻断入库
        logger.warning(f"URL 导入内容哈希计算失败（文件可能损坏）: {rel_path}")
    elif await find_duplicate_by_hash(db, content_hash):
        delete_files(rel_path, thumb_path)
        raise HTTPException(status_code=409, detail="该素材已存在（内容重复）")

    media_type = "video" if is_video_content else "image"

    inspiration = Inspiration(
        source_type=source_type,
        # 显式传入来源页面地址优先，否则回退为图片 URL
        source_url=source_url or url,
        source_author=source_author,
        source_platform_id=source_platform_id,
        file_path=rel_path,
        thumbnail_path=thumb_path,
        content_hash=content_hash,
        media_type=media_type,
        scraper_task_id=scraper_task_id,
    )
    db.add(inspiration)
    try:
        await db.flush()
    except IntegrityError:
        # 并发下撞唯一约束（如平台 ID 部分唯一索引）：清理已落盘文件后转 409，
        # 避免残留孤儿文件与 500
        delete_files(rel_path, thumb_path)
        raise HTTPException(status_code=409, detail="该素材已存在（平台 ID 冲突）")
    await db.refresh(inspiration)

    # 关联标签
    if tag_names:
        for tname in tag_names:
            tag = await get_or_create_tag(db, tname.strip(), "free")
            link = InspirationTag(
                inspiration_id=inspiration.id, tag_id=tag.id, confidence=1.0,
                source="manual",
            )
            db.add(link)
        await db.flush()

    # 入库后登记向量回填（攒批，含 URL 导入时携带的标签 → 文本向量一并生成）。
    # 登记失败不影响导入主流程，仅记日志降级。
    try:
        from app.services.task_runners.vector_backfill import enqueue_vector_backfills

        await enqueue_vector_backfills(db, [inspiration.id])
    except Exception as e:
        logger.warning(f"登记向量回填失败（忽略，不影响导入）: {e}")

    return inspiration
