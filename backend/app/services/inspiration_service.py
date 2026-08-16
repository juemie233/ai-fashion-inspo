"""灵感素材服务：素材 CRUD、批量标签、去重与向量同步。"""

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.inspiration import AIAnalysisLog, Inspiration, analysis_log_filter, utcnow
from app.models.scraper import ScraperSeenURL
from app.models.tag import InspirationTag
from app.schemas.inspiration import InspirationUpdate
from app.services.file_service import (
    delete_files,
    generate_thumbnail,
    move_to_trash,
    restore_from_trash,
    save_upload,
)
from app.services.tag_service import get_or_create_tag
from app.utils.file_hash import file_sha256

# 垃圾桶删除原因合法取值（负样本学习只用「质量差」子集保证语义纯净）
TRASH_REASONS = ("质量差", "重复", "不喜欢", "隐私", "其他")

# 未删除素材的统一过滤条件（软删除后所有正常查询都应排除垃圾桶素材）
_NOT_DELETED = Inspiration.deleted_at.is_(None)


async def find_duplicate_by_hash(db: AsyncSession, content_hash: str) -> str | None:
    """按文件内容哈希查找重复素材，返回重复素材 ID（无重复返回 None）。

    优先走 content_hash 索引列（快路径）；存量素材尚未回填哈希（列为空）时，
    回退全量扫描磁盘文件，并顺手把哈希回填入库，一次扫描后后续全部走索引。
    """
    # 快路径：哈希列命中（垃圾桶素材视为「可重新入库」，不参与去重）
    result = await db.execute(
        select(Inspiration.id).where(
            Inspiration.content_hash == content_hash,
            _NOT_DELETED,
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
                _NOT_DELETED,
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
            _NOT_DELETED,
        )
    )
    for insp_id, fpath in result.all():
        h = file_sha256(storage_root / fpath) if fpath else None
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
) -> Inspiration:
    """上传图片并创建灵感素材，含平台 ID 查重与内容哈希去重。"""
    # 检查重复（按平台 ID）—— 先查重，避免保存文件后再发现重复留下孤儿文件
    if source_platform_id:
        result = await db.execute(
            select(Inspiration).where(
                Inspiration.source_platform_id == source_platform_id
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"平台ID '{source_platform_id}' 的素材已存在",
            )

    # 保存文件
    file_path, thumb_path = await save_upload(file)

    # 内容去重：计算 SHA-256 并全库比对，避免同一素材重复入库
    content_hash = file_sha256(settings.storage_root / file_path)
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
    )
    db.add(inspiration)
    await db.flush()
    await db.refresh(inspiration)

    # 入库后异步回填向量：保证新素材进入详情页「相似推荐」/ 语义搜索时已有向量，
    # 避免请求链路内现场 CLIP 编码造成卡顿。文本向量需等标签生成后才有内容，
    # 无标签时由任务内部自动跳过（后续 AI 分析完成时再重建）。
    # 入队失败（如任务表不可用）不影响上传主流程，静默降级。
    try:
        from app.services.task_runners.vector_backfill import create_vector_backfill_task

        await create_vector_backfill_task(db, [inspiration.id])
    except Exception:
        pass

    return inspiration


async def create_inspiration_from_url(
    db: AsyncSession,
    url: str,
    source_author: str | None = None,
    tag_names: list[str] | None = None,
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

    # 校验真实文件类型（图片需能通过 PIL 解码，视频需带 MP4 魔数）
    try:
        validate_media(file_path_obj, content_type)
    except HTTPException:
        if file_path_obj.exists():
            file_path_obj.unlink(missing_ok=True)
        raise

    # 生成缩略图
    rel_path = f"images/{today}/{filename}"
    thumb_path = await generate_thumbnail(file_path_obj)

    # 内容去重：计算 SHA-256 并全库比对，避免同一素材重复入库
    content_hash = file_sha256(file_path_obj)
    if content_hash and await find_duplicate_by_hash(db, content_hash):
        delete_files(rel_path, thumb_path)
        raise HTTPException(status_code=409, detail="该素材已存在（内容重复）")

    media_type = "video" if ext == ".mp4" else "image"

    inspiration = Inspiration(
        source_type="browser_extension",
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
    # 入队失败不影响导入主流程，静默降级。
    try:
        from app.services.task_runners.vector_backfill import create_vector_backfill_task

        await create_vector_backfill_task(db, [inspiration.id])
    except Exception:
        pass

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
    sort: str = "newest",
) -> tuple[list[Inspiration], int]:
    """分页查询灵感列表，支持多维筛选和排序。

    返回:
        (素材列表, 总数)
    """
    query = select(Inspiration).options(
        selectinload(Inspiration.tags).selectinload(InspirationTag.tag)
    ).where(_NOT_DELETED)

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

    # 分析状态筛选
    if analysis_status == "done":
        query = query.where(
            Inspiration.id.in_(
                select(AIAnalysisLog.inspiration_id).where(
                    analysis_log_filter(),
                    AIAnalysisLog.error.is_(None),
                ).distinct()
            )
        )
    elif analysis_status == "error":
        query = query.where(
            Inspiration.id.in_(
                select(AIAnalysisLog.inspiration_id).where(
                    analysis_log_filter(),
                    AIAnalysisLog.error.isnot(None),
                ).distinct()
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

        size_rows = (await db.execute(
            select(Inspiration.id, Inspiration.file_path).where(_NOT_DELETED)
        )).all()

        def _file_size(row) -> int:
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


async def delete_rejected_inspirations(db: AsyncSession) -> dict:
    """物理删除所有质量审核被拒绝（rejected）的素材，释放磁盘空间。

    删除前写入墓碑表，防止下次采集重复下载相同 URL。
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    result = await db.execute(
        select(Inspiration).where(
            Inspiration.quality_status == "rejected",
            _NOT_DELETED,
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
    if urls_to_seal:
        for url in urls_to_seal:
            await db.execute(
                sqlite_insert(ScraperSeenURL)
                .values(source_url=url)
                .prefix_with("OR IGNORE")
            )
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
                except Exception:
                    pass

    # 同步删除向量库中的文本/图像向量（LanceDB 未安装时静默跳过），
    # 避免批量删除后产生孤儿向量
    from app.services import vector_store

    await vector_store.delete_inspiration_vectors_batch(deleted_ids)

    return {"deleted": len(rejected), "freed_bytes": freed_bytes}


async def get_inspiration(db: AsyncSession, inspiration_id: str) -> Inspiration:
    """获取单个灵感详情（包含完整标签和分析日志），不存在则抛出 404。"""
    result = await db.execute(
        select(Inspiration)
        .options(
            selectinload(Inspiration.tags).selectinload(InspirationTag.tag),
            selectinload(Inspiration.analysis_logs),
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

    # 批量插入新关联，跳过已存在的；记录实际变更的素材
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
            db.add(
                InspirationTag(
                    inspiration_id=inspiration_id, tag_id=tag_id, confidence=1.0
                )
            )
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
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from app.services import vector_store

    result = await db.execute(
        select(Inspiration).where(Inspiration.id == inspiration_id)
    )
    inspiration = result.scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")

    # 写入墓碑表（防止重复采集）
    if inspiration.source_url:
        await db.execute(
            sqlite_insert(ScraperSeenURL)
            .values(source_url=inspiration.source_url)
            .prefix_with("OR IGNORE")
        )

    # 先删除数据库记录并 flush（提交由外层事务完成），成功后再删磁盘文件，
    # 避免「文件已删但事务失败」产生指向不存在文件的记录
    await db.delete(inspiration)
    await db.flush()

    # 同步删除向量库中的文本/图像向量（LanceDB 未安装时静默跳过）
    if vector_store.is_lancedb_available():
        await vector_store.delete_inspiration_vectors(inspiration.id)

    # 提交成功后物理删除文件（删除失败仅记日志，不抛异常）
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
    恢复时无需重建、清空时才删除向量。
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

    # 移动主文件与缩略图到垃圾桶，更新 DB 路径；文件缺失时返回 None 保留原路径
    new_file = move_to_trash(inspiration.file_path, inspiration.id)
    if new_file:
        inspiration.file_path = new_file
    new_thumb = move_to_trash(inspiration.thumbnail_path, inspiration.id, suffix="_thumb")
    if new_thumb:
        inspiration.thumbnail_path = new_thumb

    inspiration.deleted_at = utcnow()
    inspiration.trash_reason = resolved
    await db.flush()
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

    new_file = restore_from_trash(inspiration.file_path)
    if new_file:
        inspiration.file_path = new_file
    new_thumb = restore_from_trash(inspiration.thumbnail_path)
    if new_thumb:
        inspiration.thumbnail_path = new_thumb

    inspiration.deleted_at = None
    inspiration.trash_reason = None
    await db.flush()
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
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
    if urls_to_seal:
        for url in urls_to_seal:
            await db.execute(
                sqlite_insert(ScraperSeenURL).values(source_url=url).prefix_with("OR IGNORE")
            )
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
                except Exception:
                    pass

    # 删除向量库中的文本/图像向量（垃圾桶素材向量在清空时一并清理）
    await vector_store.delete_inspiration_vectors_batch(deleted_ids)

    return {"deleted": len(items), "freed_bytes": freed_bytes}
