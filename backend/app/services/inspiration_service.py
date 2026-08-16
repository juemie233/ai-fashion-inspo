"""灵感素材服务：素材 CRUD、批量标签、去重与向量同步。"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    NOT_DELETED,
    analysis_log_filter,
    latest_analysis_log_subquery,
    utcnow,
)
from app.models.person import InspirationPerson
from app.models.tag import InspirationTag, Tag
from app.schemas.inspiration import TRASH_REASONS, InspirationUpdate
from app.services.file_service import (
    delete_files,
    generate_thumbnail,
    move_to_trash,
    restore_from_trash,
    save_upload,
)
from app.services.scraper_seen_service import seal_urls
from app.services.tag_service import get_or_create_tag
from app.services.audit_service import record_audit_log
from app.utils.file_hash import file_sha256

logger = logging.getLogger(__name__)


async def find_duplicate_by_hash(db: AsyncSession, content_hash: str) -> str | None:
    """按文件内容哈希查找重复素材，返回重复素材 ID（无重复返回 None）。

    优先走 content_hash 索引列（快路径）；存量素材尚未回填哈希（列为空）时，
    回退全量扫描磁盘文件，并顺手把哈希回填入库，一次扫描后后续全部走索引。
    """
    # 快路径：哈希列命中（垃圾桶素材视为「可重新入库」，不参与去重）
    result = await db.execute(
        select(Inspiration.id).where(
            Inspiration.content_hash == content_hash,
            NOT_DELETED,
        )
    )
    dup_id = result.scalars().first()
    if dup_id:
        return dup_id

    # 存量回退：库中仍有未回填哈希的行时才全量扫描（避免无谓磁盘 I/O）
    unfilled = (
        await db.execute(
            select(func.count(Inspiration.id)).where(
                Inspiration.content_hash.is_(None),
                Inspiration.file_path.isnot(None),
                NOT_DELETED,
            )
        )
    ).scalar() or 0
    if not unfilled:
        return None

    storage_root = settings.storage_root
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path).where(
            Inspiration.content_hash.is_(None),
            Inspiration.file_path.isnot(None),
            NOT_DELETED,
        )
    )
    for insp_id, fpath in result.all():
        # 全库磁盘哈希是阻塞 I/O，放线程池执行，避免卡住事件循环
        h = (
            await asyncio.to_thread(file_sha256, storage_root / fpath)
            if fpath
            else None
        )
        if h:
            # 回填哈希（含命中场景），随外层事务一并提交
            await db.execute(
                update(Inspiration)
                .where(Inspiration.id == insp_id)
                .values(content_hash=h)
            )
            if h == content_hash:
                return insp_id
    return None


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
    # 仅统计未删除素材：垃圾桶素材释放平台 ID，允许「删除后重新采集」
    # （与 content_hash 去重、部分唯一索引的语义一致）
    if source_platform_id:
        result = await db.execute(
            select(Inspiration).where(
                Inspiration.source_platform_id == source_platform_id,
                NOT_DELETED,
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"平台ID '{source_platform_id}' 的素材已存在",
            )

    # 关联采集任务校验：插件采集链路传 task_id，避免产生指向不存在任务的孤儿记录
    if scraper_task_id is not None:
        from app.models.scraper import ScraperTask

        task = await db.get(ScraperTask, scraper_task_id)
        if not task:
            raise HTTPException(status_code=400, detail="关联的采集任务不存在")

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
    await db.flush()
    await db.refresh(inspiration)

    # 入库后异步回填向量：保证新素材进入详情页「相似推荐」/ 语义搜索时已有向量，
    # 避免请求链路内现场 CLIP 编码造成卡顿。文本向量需等标签生成后才有内容，
    # 无标签时由任务内部自动跳过（后续 AI 分析完成时再重建）。
    # 入队失败（如任务表不可用）不影响上传主流程，仅记日志降级。
    try:
        from app.services.task_runners.vector_backfill import create_vector_backfill_task

        await create_vector_backfill_task(db, [inspiration.id])
    except Exception as e:
        logger.warning(f"入队向量回填任务失败（忽略，不影响上传）: {e}")

    return inspiration


async def create_inspiration_from_url(
    db: AsyncSession,
    url: str,
    source_author: str | None = None,
    tag_names: list[str] | None = None,
    source_type: str = "url_import",
) -> Inspiration:
    """从 URL 下载图片并创建素材，支持关联标签。"""
    import aiofiles
    import httpx

    from app.services.file_service import resolve_size_limit, validate_media

    tag_names = tag_names or []

    # 下载图片：流式落盘 + 大小限制（按响应 Content-Type 区分图片/视频上限）
    images_dir = settings.images_dir
    images_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m")
    day_dir = images_dir / today
    day_dir.mkdir(parents=True, exist_ok=True)

    filename: str | None = None
    file_path_obj: Path | None = None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")

                # 从 Content-Type 推断扩展名
                ext = ".jpg"
                if "png" in content_type:
                    ext = ".png"
                elif "webp" in content_type:
                    ext = ".webp"
                elif "gif" in content_type:
                    ext = ".gif"
                elif "mp4" in content_type:
                    ext = ".mp4"

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
    except httpx.TimeoutException:
        raise HTTPException(status_code=400, detail="下载超时，请检查 URL 是否可访问")
    except HTTPException:
        # 大小超限等：清理残留文件后原样抛出
        if file_path_obj and file_path_obj.exists():
            file_path_obj.unlink(missing_ok=True)
        raise
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=400, detail="下载失败：目标地址返回非 2xx 状态码")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"下载失败: {e}")

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
    if content_hash and await find_duplicate_by_hash(db, content_hash):
        delete_files(rel_path, thumb_path)
        raise HTTPException(status_code=409, detail="该素材已存在（内容重复）")

    media_type = "video" if ext == ".mp4" else "image"

    inspiration = Inspiration(
        source_type=source_type,
        source_url=url,
        source_author=source_author,
        file_path=rel_path,
        thumbnail_path=thumb_path,
        content_hash=content_hash,
        media_type=media_type,
    )
    db.add(inspiration)
    await db.flush()
    await db.refresh(inspiration)

    # 关联标签
    if tag_names:
        for tname in tag_names:
            tag = await get_or_create_tag(db, tname.strip(), "free")
            link = InspirationTag(inspiration_id=inspiration.id, tag_id=tag.id, confidence=1.0)
            db.add(link)
        await db.flush()

    # 入库后异步回填向量（含 URL 导入时携带的标签 → 文本向量一并生成）。
    # 入队失败不影响导入主流程，仅记日志降级。
    try:
        from app.services.task_runners.vector_backfill import create_vector_backfill_task

        await create_vector_backfill_task(db, [inspiration.id])
    except Exception as e:
        logger.warning(f"入队向量回填任务失败（忽略，不影响导入）: {e}")

    return inspiration


async def list_inspirations(
    db: AsyncSession,
    page: int = 1,
    size: int = 50,
    source_type: str | None = None,
    is_favorite: bool | None = None,
    media_type: str | None = None,
    analysis_status: str | None = None,  # done | pending | error
    tag_status: str | None = None,        # tagged | untagged
    quality_status: str | None = None,    # pending | approved | rejected
    is_ai_generated: bool | None = None,  # 仅筛选疑似 AI 生成素材
    include_tags: list[str] | None = None,  # 需同时包含的标签名（AND 语义）
    dominant_color: str | None = None,      # 主色调（hex 子串匹配）
    date_from: str | None = None,           # 上传日期下限（ISO 日期）
    date_to: str | None = None,             # 上传日期上限（ISO 日期）
    sort: str = "newest",
) -> tuple[list[Inspiration], int]:
    """分页查询灵感列表，支持多维筛选和排序。

    返回:
        (素材列表, 总数)
    """
    query = select(Inspiration).options(
        selectinload(Inspiration.tags).selectinload(InspirationTag.tag),
        selectinload(Inspiration.persons).selectinload(InspirationPerson.person),
    ).where(NOT_DELETED)

    if source_type:
        query = query.where(Inspiration.source_type == source_type)
    if is_favorite is not None:
        query = query.where(Inspiration.is_favorite == is_favorite)
    if media_type:
        query = query.where(Inspiration.media_type == media_type)
    if quality_status:
        query = query.where(
            func.coalesce(Inspiration.quality_status, "pending") == quality_status
        )
    if is_ai_generated is not None:
        query = query.where(Inspiration.is_ai_generated == is_ai_generated)
    if dominant_color:
        query = query.where(Inspiration.dominant_colors.contains(dominant_color))
    if date_from:
        query = query.where(Inspiration.created_at >= date_from)
    if date_to:
        query = query.where(Inspiration.created_at <= date_to)

    # 标签筛选（AND 语义：素材须同时包含所有给定标签）
    if include_tags:
        for name in include_tags:
            tag_id_sub = select(Tag.id).where(Tag.name == name)
            query = query.where(
                Inspiration.id.in_(
                    select(InspirationTag.inspiration_id).where(
                        InspirationTag.tag_id.in_(tag_id_sub)
                    )
                )
            )

    # 分析状态筛选（done/error 基于「最新一条」标签分析日志，与卡片状态一致）
    if analysis_status in ("done", "error"):
        latest = latest_analysis_log_subquery()
        error_cond = (
            (AIAnalysisLog.error.isnot(None)) & (AIAnalysisLog.error != "")
            if analysis_status == "error"
            else AIAnalysisLog.error.is_(None)
        )
        query = query.where(
            Inspiration.id.in_(
                select(AIAnalysisLog.inspiration_id)
                .join(
                    latest,
                    (AIAnalysisLog.inspiration_id == latest.c.inspiration_id)
                    & (AIAnalysisLog.id == latest.c.max_id),
                )
                .where(error_cond)
                .distinct()
            )
        )
    elif analysis_status == "pending":
        analyzed_sub = (
            select(AIAnalysisLog.inspiration_id)
            .where(analysis_log_filter())
            .distinct()
        )
        query = query.where(Inspiration.id.notin_(analyzed_sub))

    # 标签状态筛选
    if tag_status == "tagged":
        query = query.where(
            Inspiration.id.in_(
                select(InspirationTag.inspiration_id).distinct()
            )
        )
    elif tag_status == "untagged":
        query = query.where(
            Inspiration.id.notin_(
                select(InspirationTag.inspiration_id).distinct()
            )
        )

    # 排序
    sort_map = {
        "newest": Inspiration.created_at.desc(),
        "oldest": Inspiration.created_at.asc(),
        "updated": Inspiration.updated_at.desc(),
        "random": func.random(),  # 随机洗牌：每次请求重新随机
    }

    # largest 排序：SQLite 无法按磁盘文件大小排序，改为取全量 (id, file_path)
    # 在 Python 中按文件实际大小降序取本页（个人库规模可接受）。
    # 注意：须先统计总数，再按大小排序取页内 ID，最后回查对象并恢复页内顺序。
    if sort == "largest":
        total = (
            await db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar() or 0

        # 复用与主查询完全一致的筛选条件（含 NOT_DELETED 与用户筛选），
        # 否则取出的 size_rows 与筛选结果错位，导致翻页错乱/空页
        where_clause = query.whereclause
        size_query = select(Inspiration.id, Inspiration.file_path)
        if where_clause is not None:
            size_query = size_query.where(where_clause)
        size_rows = (await db.execute(size_query)).all()

        def _file_size(row: Any) -> int:
            """返回素材文件字节数（文件缺失按 0 处理）。"""
            if not row[1]:
                return 0
            try:
                p = settings.storage_root / row[1]
                return p.stat().st_size if p.exists() else 0
            except OSError:
                return 0

        ordered = sorted(size_rows, key=_file_size, reverse=True)
        page_ids = [r[0] for r in ordered[(page - 1) * size : page * size]]

        if not page_ids:
            return [], total

        result = await db.execute(query.where(Inspiration.id.in_(page_ids)))
        inspirations = result.unique().scalars().all()
        # 恢复按文件大小的页内顺序（in_ 查询不保证顺序）
        id_order = {insp_id: idx for idx, insp_id in enumerate(page_ids)}
        inspirations = sorted(inspirations, key=lambda i: id_order.get(i.id, 0))
        return inspirations, total

    # 按标签数量降序：标签丰富的素材排前（并列时按创建时间倒序保持稳定）
    if sort == "tag_count":
        tag_count_sub = (
            select(
                InspirationTag.inspiration_id,
                func.count(InspirationTag.tag_id).label("cnt"),
            )
            .group_by(InspirationTag.inspiration_id)
            .subquery()
        )
        query = query.outerjoin(
            tag_count_sub, Inspiration.id == tag_count_sub.c.inspiration_id
        ).order_by(
            func.coalesce(tag_count_sub.c.cnt, 0).desc(),
            Inspiration.created_at.desc(),
        )
    else:
        query = query.order_by(sort_map.get(sort, Inspiration.created_at.desc()))

    # 统计总数
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 分页
    query = query.offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    inspirations = result.unique().scalars().all()
    return inspirations, total


async def list_dominant_colors(db: AsyncSession, limit: int = 30) -> list[dict]:
    """统计库内实际出现的主色调（hex）及其出现次数，供颜色筛选使用。

    从 dominant_colors 的 JSON 数组字符串解析去重计数，仅统计未删除素材；
    返回按出现次数降序的颜色列表，避免前端硬编码可能不存在的色板。
    """
    import json

    result = await db.execute(
        select(Inspiration.dominant_colors).where(
            NOT_DELETED, Inspiration.dominant_colors.isnot(None)
        )
    )
    counter: dict[str, int] = {}
    for (raw,) in result.all():
        try:
            colors = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(colors, list):
            for c in colors:
                if isinstance(c, str) and c:
                    counter[c] = counter.get(c, 0) + 1

    top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [{"color": c, "count": n} for c, n in top]


async def delete_rejected_inspirations(db: AsyncSession) -> dict:
    """物理删除所有质量审核被拒绝（rejected）的素材，释放磁盘空间。

    删除前写入墓碑表，防止下次采集重复下载相同 URL。
    """
    result = await db.execute(
        select(Inspiration).where(
            Inspiration.quality_status == "rejected",
            NOT_DELETED,
        )
    )
    rejected = result.scalars().all()

    if not rejected:
        return {"deleted": 0, "freed_bytes": 0, "message": "没有已拒绝的素材"}

    deleted_ids = [insp.id for insp in rejected]
    urls_to_seal: list[str] = [insp.source_url for insp in rejected if insp.source_url]

    # 先删除数据库记录并写入墓碑表（同一事务），提交成功后再物理删除文件，
    # 避免「文件已删但事务失败」产生指向不存在文件的记录
    for insp in rejected:
        await db.delete(insp)
    await seal_urls(db, urls_to_seal)
    await db.commit()

    # 提交成功后物理删除文件，并统计释放空间（删除失败仅记日志，不抛异常）
    freed_bytes = 0
    for insp in rejected:
        for p in (insp.file_path, insp.thumbnail_path):
            if p:
                full = settings.storage_root / p
                try:
                    if full.exists():
                        freed_bytes += full.stat().st_size
                        full.unlink()
                except Exception as e:
                    logger.warning(f"删除文件失败（忽略）: {full} — {e}")

    # 同步删除向量库中的文本/图像向量（LanceDB 未安装时静默跳过），
    # 避免批量删除后产生孤儿向量
    from app.services import vector_store

    await vector_store.delete_inspiration_vectors_batch(deleted_ids)

    # 记录审计：删除已拒绝素材属于破坏性批量操作，留痕便于追溯
    await record_audit_log(
        action="delete_rejected",
        count=len(rejected),
        freed_bytes=freed_bytes,
        detail="物理删除所有质量审核被拒绝的素材",
    )

    return {"deleted": len(rejected), "freed_bytes": freed_bytes}


async def get_inspiration(db: AsyncSession, inspiration_id: str) -> Inspiration:
    """获取单个灵感详情（包含完整标签和分析日志），不存在则抛出 404。"""
    result = await db.execute(
        select(Inspiration)
        .options(
            selectinload(Inspiration.tags).selectinload(InspirationTag.tag),
            selectinload(Inspiration.analysis_logs),
            selectinload(Inspiration.persons).selectinload(InspirationPerson.person),
        )
        .where(Inspiration.id == inspiration_id)
    )
    inspiration = result.unique().scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    return inspiration


async def update_inspiration(
    db: AsyncSession,
    inspiration_id: str,
    data: InspirationUpdate,
) -> Inspiration:
    """更新灵感（收藏状态、作者等部分字段），不存在则抛出 404。"""
    result = await db.execute(
        select(Inspiration)
        .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
        .where(Inspiration.id == inspiration_id)
    )
    inspiration = result.unique().scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")

    if data.is_favorite is not None:
        inspiration.is_favorite = data.is_favorite
    if data.source_author is not None:
        inspiration.source_author = data.source_author
    if data.quality_status is not None:
        # 人工复核翻案：修改审核状态，同时处理原因
        inspiration.quality_status = data.quality_status
        if data.quality_status in ("approved", "pending"):
            inspiration.quality_reason = None
        elif data.quality_reason is not None:
            inspiration.quality_reason = data.quality_reason

    if data.is_ai_generated is not None:
        # 人工复核翻案：标记或取消「疑似 AI」标记
        inspiration.is_ai_generated = data.is_ai_generated

    await db.flush()
    await db.refresh(inspiration)
    return inspiration


async def batch_add_tags(
    db: AsyncSession,
    inspiration_ids: list[str],
    names: list[str],
    category: str = "free",
    source: str = "manual",
) -> dict:
    """批量给多个素材关联标签（按名称查找或创建，已关联的自动跳过）。

    仅对实际新增了标签的素材重建文本向量，避免无谓调用 Ollama。
    """
    from app.services.vector_service import rebuild_text_vector

    # 去重（保留顺序），避免重复 ID/名称虚增统计与重复查询
    inspiration_ids = list(dict.fromkeys(inspiration_ids))
    raw_names = [n.strip() for n in names if n.strip()]

    if not raw_names:
        raise HTTPException(status_code=400, detail="请提供有效的标签名称")

    # 先解析标签（批量 get_or_create，避免每个素材重复查询同名标签）
    tags = []
    for name in raw_names:
        tags.append(await get_or_create_tag(db, name, category, source))

    tag_ids = [t.id for t in tags]

    # 一次性校验素材存在性，避免逐个 db.get
    found_result = await db.execute(
        select(Inspiration.id).where(Inspiration.id.in_(inspiration_ids))
    )
    found_ids = set(found_result.scalars().all())
    not_found_ids = [i for i in inspiration_ids if i not in found_ids]

    # 一次性查出已存在的关联，避免 M×N 逐条查询
    existing_result = await db.execute(
        select(InspirationTag.inspiration_id, InspirationTag.tag_id).where(
            InspirationTag.inspiration_id.in_(inspiration_ids),
            InspirationTag.tag_id.in_(tag_ids),
        )
    )
    existing_pairs = {(r[0], r[1]) for r in existing_result.all()}

    # 批量插入新关联，跳过已存在的；记录实际变更的素材。
    # 逐条用 SAVEPOINT flush 并捕获 IntegrityError：并发请求插入同一关联时，
    # 仅回滚该条 SAVEPOINT 而非整个事务，避免 500。
    total_added = 0
    affected_ids: list[str] = []
    skipped_existing = 0
    for inspiration_id in inspiration_ids:
        if inspiration_id not in found_ids:
            continue
        added_for_this = 0
        for tag_id in tag_ids:
            if (inspiration_id, tag_id) in existing_pairs:
                skipped_existing += 1
                continue
            link = InspirationTag(
                inspiration_id=inspiration_id, tag_id=tag_id, confidence=1.0
            )
            db.add(link)
            try:
                async with db.begin_nested():
                    await db.flush()
            except IntegrityError:
                # 并发下同一关联已被其他请求插入：回滚 SAVEPOINT，移除失败对象后跳过
                db.expunge(link)
                skipped_existing += 1
                continue
            added_for_this += 1
        if added_for_this:
            affected_ids.append(inspiration_id)
            total_added += added_for_this

    await db.commit()

    # 标签变更后重建文本向量，保持语义搜索结果最新（LanceDB/Ollama 不可用时静默降级）
    for inspiration_id in affected_ids:
        await rebuild_text_vector(db, inspiration_id)

    return {
        "added": total_added,
        "affected": len(affected_ids),
        # 向后兼容：skipped 仍为「未实际变更的素材数」（含不存在与已全部关联）
        "skipped": len(inspiration_ids) - len(affected_ids),
        # 明确拆分两个跳过维度
        "not_found": len(not_found_ids),
        "skipped_existing": skipped_existing,
        "missing_ids": not_found_ids,
    }


async def batch_favorite_inspirations(
    db: AsyncSession,
    inspiration_ids: list[str],
    is_favorite: bool,
) -> int:
    """批量设置素材收藏状态，返回实际更新的行数。

    仅作用于未删除素材；已删除/不存在的 ID 被静默忽略。
    """
    result = await db.execute(
        update(Inspiration)
        .where(Inspiration.id.in_(inspiration_ids), NOT_DELETED)
        .values(is_favorite=is_favorite, updated_at=utcnow())
    )
    await db.commit()
    return result.rowcount


async def batch_trash_inspirations(
    db: AsyncSession,
    inspiration_ids: list[str],
    reason: str | None = None,
) -> dict:
    """批量将素材移入垃圾桶（逐个复用单条软删除逻辑，容忍部分失败）。

    单条素材的文件移动与软删除已在 trash_inspiration 内完成并各自提交；
    此处逐条调用，不存在/已在垃圾桶中的 ID 计入 skipped，不影响其余素材。
    """
    trashed = 0
    skipped = 0
    for inspiration_id in inspiration_ids:
        try:
            await trash_inspiration(db, inspiration_id, reason)
            trashed += 1
        except HTTPException:
            skipped += 1

    # 记录审计：批量移入垃圾桶（软删除）也纳入审计，便于追溯批量整理动作
    if trashed > 0:
        await record_audit_log(
            action="batch_trash",
            count=trashed,
            detail=f"跳过 {skipped} 个（不存在或已在垃圾桶）" if skipped else None,
        )

    return {"trashed": trashed, "skipped": skipped}


async def batch_update_inspirations(
    db: AsyncSession,
    inspiration_ids: list[str],
    *,
    source_type: str | None = None,
    is_favorite: bool | None = None,
    quality_status: str | None = None,
    is_ai_generated: bool | None = None,
) -> int:
    """批量编辑素材元数据，仅更新显式提供的字段，返回实际更新行数。

    审核状态翻案为 approved/pending 时清空拒绝原因（与单条更新语义一致）。
    """
    values: dict = {"updated_at": utcnow()}
    if source_type is not None:
        values["source_type"] = source_type
    if is_favorite is not None:
        values["is_favorite"] = is_favorite
    if is_ai_generated is not None:
        values["is_ai_generated"] = is_ai_generated
    if quality_status is not None:
        values["quality_status"] = quality_status
        if quality_status in ("approved", "pending"):
            values["quality_reason"] = None

    if len(values) == 1:  # 仅 updated_at，无任何业务字段
        return 0

    result = await db.execute(
        update(Inspiration)
        .where(Inspiration.id.in_(inspiration_ids), NOT_DELETED)
        .values(**values)
    )
    await db.commit()
    return result.rowcount


async def add_inspiration_tags(
    db: AsyncSession,
    inspiration_id: str,
    names: list[str],
    category: str = "free",
    source: str = "manual",
) -> dict:
    """手动给素材关联标签（按名称查找或创建，已关联的自动跳过）。"""
    from app.services.vector_service import rebuild_text_vector

    inspiration = await db.get(Inspiration, inspiration_id)
    if not inspiration:
        raise HTTPException(status_code=404, detail="素材未找到")

    if not isinstance(names, list) or not names:
        raise HTTPException(status_code=400, detail="请提供标签名称列表")

    added = []
    for raw in names:
        name = str(raw).strip()
        if not name:
            continue
        tag = await get_or_create_tag(db, name, category, source)
        existing = await db.execute(
            select(InspirationTag).where(
                InspirationTag.inspiration_id == inspiration_id,
                InspirationTag.tag_id == tag.id,
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(
            InspirationTag(
                inspiration_id=inspiration_id, tag_id=tag.id, confidence=1.0
            )
        )
        added.append({"id": tag.id, "name": tag.name, "category": tag.category})

    await db.commit()

    # 标签变更后重建文本向量，保持语义搜索结果最新（LanceDB/Ollama 不可用时静默降级）
    await rebuild_text_vector(db, inspiration_id)

    return {"added": added, "count": len(added)}


async def remove_inspiration_tag(
    db: AsyncSession,
    inspiration_id: str,
    tag_id: int,
) -> dict:
    """解除素材与某个标签的关联（不删除标签本身）。"""
    from app.services.vector_service import rebuild_text_vector

    result = await db.execute(
        delete(InspirationTag).where(
            InspirationTag.inspiration_id == inspiration_id,
            InspirationTag.tag_id == tag_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到该标签关联")

    # 标签变更后重建文本向量，保持语义搜索结果最新（LanceDB/Ollama 不可用时静默降级）
    await rebuild_text_vector(db, inspiration_id)
    return {"removed": 1}


async def delete_inspiration(db: AsyncSession, inspiration_id: str) -> None:
    """删除灵感素材及其对应的磁盘文件，不存在则抛出 404。"""
    from app.services import vector_store

    result = await db.execute(
        select(Inspiration).where(Inspiration.id == inspiration_id)
    )
    inspiration = result.scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")

    # 写入墓碑表（防止重复采集）
    await seal_urls(db, [inspiration.source_url])

    # 先删除数据库记录并提交（与 purge_trash / batch_delete 等路径一致：DB
    # 落库成功后再删磁盘文件/向量），避免「文件已删但事务提交失败」产生
    # 指向不存在文件的悬空记录
    await db.delete(inspiration)
    await db.commit()

    # 提交成功后同步删除向量库中的文本/图像向量（LanceDB 未安装时静默跳过）
    if vector_store.is_lancedb_available():
        await vector_store.delete_inspiration_vectors(inspiration.id)

    # 物理删除文件（删除失败仅记日志，不抛异常）
    delete_files(inspiration.file_path, inspiration.thumbnail_path)


def _resolve_trash_reason(reason: str | None, inspiration: Inspiration) -> str:
    """解析删除原因：显式传入的合法值优先；否则按素材状态自动推断。

    - 质量审核被拒绝（rejected）→ 「质量差」（负样本学习用）
    - 其余 → 「不喜欢」
    """
    if reason in TRASH_REASONS:
        return reason
    return "质量差" if inspiration.quality_status == "rejected" else "不喜欢"


async def trash_inspiration(
    db: AsyncSession, inspiration_id: str, reason: str | None
) -> Inspiration:
    """将素材移入垃圾桶（软删除）：文件移入 trash/，标记 deleted_at 与 trash_reason。

    向量保留不删除（阶段 2 负样本学习依赖垃圾桶素材的 CLIP 图像向量），
    恢复时无需重建、清空时才删除向量。软删除即写入来源 URL 墓碑，采集器
    后续遇到该 URL 会直接跳过，不再重复采集。
    """
    result = await db.execute(
        select(Inspiration)
        .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
        .where(Inspiration.id == inspiration_id)
    )
    inspiration = result.unique().scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    if inspiration.deleted_at is not None:
        raise HTTPException(status_code=409, detail="素材已在垃圾桶中")

    resolved = _resolve_trash_reason(reason, inspiration)

    # 先提交软删除标记（DB 落库），提交成功后再移动文件，避免「文件已移走但事务
    # 回滚/失败」导致 DB 仍指向原路径的悬空记录（与 delete_inspiration 的顺序一致）。
    inspiration.deleted_at = utcnow()
    inspiration.trash_reason = resolved
    # 进垃圾桶视为「垃圾素材」，立即写入来源 URL 墓碑，防止采集器重复采集
    await seal_urls(db, [inspiration.source_url])
    await db.commit()

    # 移动文件失败时记录日志但不回滚软删除：文件仍在原目录，DB 路径未变仍指向
    # 实际位置；恢复时 restore_from_trash 找不到 trash/ 下的文件会自动保持原路径，
    # 形成自愈。文件缺失时 move_to_trash 返回 None，同样保留原路径。
    paths_changed = False
    try:
        new_file = move_to_trash(inspiration.file_path, inspiration.id)
        if new_file:
            inspiration.file_path = new_file
            paths_changed = True
    except OSError as e:
        logger.warning(f"移动主文件到垃圾桶失败 {inspiration.id}: {e}")
    try:
        new_thumb = move_to_trash(inspiration.thumbnail_path, inspiration.id, suffix="_thumb")
        if new_thumb:
            inspiration.thumbnail_path = new_thumb
            paths_changed = True
    except OSError as e:
        logger.warning(f"移动缩略图到垃圾桶失败 {inspiration.id}: {e}")

    if paths_changed:
        await db.commit()
    await db.refresh(inspiration)
    return inspiration


async def restore_inspiration(db: AsyncSession, inspiration_id: str) -> Inspiration:
    """从垃圾桶恢复素材：文件移回媒体目录，清除 deleted_at 与 trash_reason。"""
    result = await db.execute(
        select(Inspiration)
        .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
        .where(Inspiration.id == inspiration_id)
    )
    inspiration = result.unique().scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    if inspiration.deleted_at is None:
        raise HTTPException(status_code=409, detail="素材不在垃圾桶中")

    # 恢复会重新占用 source_platform_id 的唯一性（部分唯一索引仅约束未删除素材）。
    # 若垃圾桶期间同平台 ID 已被新素材重新入库，恢复将触发唯一索引 IntegrityError，
    # 这里前置查重并返回 409，避免落成 500。
    if inspiration.source_platform_id:
        dup = await db.execute(
            select(Inspiration.id)
            .where(
                Inspiration.source_platform_id == inspiration.source_platform_id,
                Inspiration.deleted_at.is_(None),
                Inspiration.id != inspiration.id,
            )
            .limit(1)
        )
        if dup.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail="该平台 ID 已被新素材占用，无法恢复（请先删除新素材后再试）",
            )

    # 先提交恢复标记（DB 落库），提交成功后再移动文件；移动失败仅记日志，
    # 文件留在 trash/ 由后续清空任务兜底清理，恢复本身不受阻断。
    inspiration.deleted_at = None
    inspiration.trash_reason = None
    await db.commit()

    paths_changed = False
    try:
        new_file = restore_from_trash(inspiration.file_path)
        if new_file:
            inspiration.file_path = new_file
            paths_changed = True
    except OSError as e:
        logger.warning(f"恢复主文件失败 {inspiration.id}: {e}")
    try:
        new_thumb = restore_from_trash(inspiration.thumbnail_path)
        if new_thumb:
            inspiration.thumbnail_path = new_thumb
            paths_changed = True
    except OSError as e:
        logger.warning(f"恢复缩略图失败 {inspiration.id}: {e}")

    if paths_changed:
        await db.commit()
    await db.refresh(inspiration)
    return inspiration


async def list_trash(
    db: AsyncSession,
    page: int = 1,
    size: int = 50,
    reason: str | None = None,
) -> tuple[list[Inspiration], int]:
    """分页查询垃圾桶中的素材（按删除时间倒序），支持按删除原因筛选。"""
    query = (
        select(Inspiration)
        .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
        .where(Inspiration.deleted_at.isnot(None))
    )
    if reason:
        query = query.where(Inspiration.trash_reason == reason)

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar() or 0

    query = query.order_by(Inspiration.deleted_at.desc()).offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    return result.unique().scalars().all(), total


async def purge_trash(db: AsyncSession, only_expired: bool = False) -> dict:
    """彻底清空垃圾桶（物理删除文件与数据库记录，释放磁盘空间）。

    参数:
        only_expired: 为 True 时仅清理已超过保留期（trash_retention_days）的素材，
            用于 30 天自动清理；为 False 时清空全部垃圾桶素材。

    返回:
        {"deleted": 删除数量, "freed_bytes": 释放字节数}
    """
    from app.services import vector_store

    conds = [Inspiration.deleted_at.isnot(None)]
    if only_expired:
        cutoff = utcnow() - timedelta(days=settings.trash_retention_days)
        conds.append(Inspiration.deleted_at < cutoff)

    result = await db.execute(select(Inspiration).where(*conds))
    items = result.scalars().all()
    if not items:
        return {"deleted": 0, "freed_bytes": 0, "message": "垃圾桶已空"}

    deleted_ids = [insp.id for insp in items]
    urls_to_seal = [insp.source_url for insp in items if insp.source_url]

    # 先删数据库记录并写墓碑（同一事务），提交成功后再物理删除文件
    for insp in items:
        await db.delete(insp)
    await seal_urls(db, urls_to_seal)
    await db.commit()

    freed_bytes = 0
    for insp in items:
        for p in (insp.file_path, insp.thumbnail_path):
            if p:
                full = settings.storage_root / p
                try:
                    if full.exists():
                        freed_bytes += full.stat().st_size
                        full.unlink()
                except Exception as e:
                    logger.warning(f"删除文件失败（忽略）: {full} — {e}")

    # 删除向量库中的文本/图像向量（垃圾桶素材向量在清空时一并清理）
    await vector_store.delete_inspiration_vectors_batch(deleted_ids)

    # 记录审计：清空垃圾桶（含定时自动清理）属于不可恢复的破坏性操作
    await record_audit_log(
        action="empty_trash",
        count=len(items),
        freed_bytes=freed_bytes,
        detail="仅清理过期素材" if only_expired else "清空全部垃圾桶",
    )

    return {"deleted": len(items), "freed_bytes": freed_bytes}
