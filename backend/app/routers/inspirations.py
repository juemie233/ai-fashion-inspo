"""灵感素材 CRUD 的 REST API 路由。"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    analysis_log_filter as _analysis_log_filter,
)
from app.models.tag import InspirationTag, Tag
from app.schemas.inspiration import (
    InspirationCreate,
    InspirationDetailOut,
    InspirationListOut,
    InspirationOut,
    InspirationUpdate,
    InspirationTagOut,
    TagOut,
)
from app.services.file_service import delete_files, save_upload

router = APIRouter(prefix="/api/inspirations", tags=["inspirations"])


@router.post("", response_model=InspirationOut, status_code=status.HTTP_201_CREATED)
async def create_inspiration(
    file: UploadFile = File(...),
    source_type: str = Form(default="manual_upload"),
    source_url: str | None = Form(default=None),
    source_author: str | None = Form(default=None),
    source_platform_id: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    """上传图片并创建灵感素材。AI 分析在后台异步执行。"""
    # 校验文件类型
    allowed_types = ("image/jpeg", "image/png", "image/webp", "image/gif", "video/mp4")
    if file.content_type and file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {file.content_type}。允许: {allowed_types}",
        )

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

    # 判断媒体类型
    media_type = "image"
    if file.content_type and file.content_type.startswith("video/"):
        media_type = "video"

    inspiration = Inspiration(
        source_type=source_type,
        source_url=source_url,
        source_author=source_author,
        source_platform_id=source_platform_id,
        file_path=file_path,
        thumbnail_path=thumb_path,
        media_type=media_type,
    )
    db.add(inspiration)
    await db.flush()
    await db.refresh(inspiration)

    return _to_out(inspiration)


@router.post("/from-url", response_model=InspirationOut, status_code=status.HTTP_201_CREATED)
async def create_from_url(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """从 URL 下载图片并创建素材。

    请求体: {"url": "...", "source_author": "...", "tags": ["..."]}
    """
    import uuid
    import aiofiles
    import httpx

    url = payload.get("url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="请提供图片 URL")

    source_author = payload.get("source_author", "").strip() or None
    tag_names = payload.get("tags", [])

    # 下载图片
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            image_bytes = resp.content
    except httpx.TimeoutException:
        raise HTTPException(status_code=400, detail="下载超时，请检查 URL 是否可访问")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"下载失败: {e}")

    # 从 Content-Type 或 URL 推断扩展名
    content_type = resp.headers.get("content-type", "")
    ext = ".jpg"
    if "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"
    elif "gif" in content_type:
        ext = ".gif"
    elif "mp4" in content_type:
        ext = ".mp4"

    # 保存文件
    filename = f"{uuid.uuid4().hex}{ext}"
    images_dir = settings.images_dir
    images_dir.mkdir(parents=True, exist_ok=True)
    file_path_obj = images_dir / filename
    async with aiofiles.open(file_path_obj, "wb") as f:
        await f.write(image_bytes)

    # 生成缩略图
    from app.services.file_service import generate_thumbnail
    rel_path = f"images/{filename}"
    thumb_path = await generate_thumbnail(file_path_obj)

    media_type = "video" if ext == ".mp4" else "image"

    inspiration = Inspiration(
        source_type="browser_extension",
        source_url=url,
        source_author=source_author,
        file_path=rel_path,
        thumbnail_path=thumb_path,
        media_type=media_type,
    )
    db.add(inspiration)
    await db.flush()
    await db.refresh(inspiration)

    # 关联标签
    if tag_names:
        from app.services.tag_service import get_or_create_tag
        for tname in tag_names:
            tag = await get_or_create_tag(db, tname.strip(), "free")
            link = InspirationTag(inspiration_id=inspiration.id, tag_id=tag.id, confidence=1.0)
            db.add(link)
        await db.flush()

    return _to_out(inspiration)


@router.get("", response_model=InspirationListOut)
async def list_inspirations(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    source_type: str | None = None,
    is_favorite: bool | None = None,
    media_type: str | None = None,
    analysis_status: str | None = None,  # done | pending | error
    tag_status: str | None = None,        # tagged | untagged
    quality_status: str | None = None,    # pending | approved | rejected
    sort: str = "newest",
    db: AsyncSession = Depends(get_db),
):
    """分页获取灵感列表，支持多维筛选和排序。"""
    query = select(Inspiration).options(
        selectinload(Inspiration.tags).selectinload(InspirationTag.tag)
    )

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

    # 分析状态筛选
    if analysis_status == "done":
        query = query.where(
            Inspiration.id.in_(
                select(AIAnalysisLog.inspiration_id).where(
                    _analysis_log_filter(),
                    AIAnalysisLog.error.is_(None),
                ).distinct()
            )
        )
    elif analysis_status == "error":
        query = query.where(
            Inspiration.id.in_(
                select(AIAnalysisLog.inspiration_id).where(
                    _analysis_log_filter(),
                    AIAnalysisLog.error.isnot(None),
                ).distinct()
            )
        )
    elif analysis_status == "pending":
        analyzed_sub = (
            select(AIAnalysisLog.inspiration_id)
            .where(_analysis_log_filter())
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
        "largest": Inspiration.file_path.desc(),  # 按路径排序不够精确，用子查询算文件大小
        "random": func.random(),  # 随机洗牌：每次请求重新随机
    }
    query = query.order_by(sort_map.get(sort, Inspiration.created_at.desc()))

    # 统计总数
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 分页
    query = query.offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    inspirations = result.unique().scalars().all()

    return InspirationListOut(
        items=[_to_out(i) for i in inspirations],
        total=total,
        page=page,
        size=size,
    )


@router.delete("/quality-rejected", status_code=status.HTTP_200_OK)
async def delete_rejected_inspirations(db: AsyncSession = Depends(get_db)):
    """物理删除所有质量审核被拒绝（rejected）的素材，释放磁盘空间。

    删除前写入墓碑表，防止下次采集重复下载相同 URL。
    """
    result = await db.execute(
        select(Inspiration).where(Inspiration.quality_status == "rejected")
    )
    rejected = result.scalars().all()

    if not rejected:
        return {"deleted": 0, "freed_bytes": 0, "message": "没有已拒绝的素材"}

    freed_bytes = 0
    urls_to_seal: list[str] = []
    for insp in rejected:
        if insp.source_url:
            urls_to_seal.append(insp.source_url)
        for p in (insp.file_path, insp.thumbnail_path):
            if p:
                full = settings.storage_root / p
                try:
                    if full.exists():
                        freed_bytes += full.stat().st_size
                        full.unlink()
                except Exception:
                    pass
        await db.delete(insp)

    # 写入墓碑表（防止重复采集）
    if urls_to_seal:
        from app.models.scraper import ScraperSeenURL
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        for url in urls_to_seal:
            await db.execute(
                sqlite_insert(ScraperSeenURL)
                .values(source_url=url)
                .prefix_with("OR IGNORE")
            )

    await db.commit()
    return {"deleted": len(rejected), "freed_bytes": freed_bytes}


@router.get("/{inspiration_id}", response_model=InspirationDetailOut)
async def get_inspiration(inspiration_id: str, db: AsyncSession = Depends(get_db)):
    """获取单个灵感详情（包含完整标签和分析日志）。"""
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

    from app.schemas.inspiration import AnalysisLogOut

    detail = InspirationDetailOut(
        id=inspiration.id,
        source_type=inspiration.source_type,
        source_url=inspiration.source_url,
        source_author=inspiration.source_author,
        source_platform_id=inspiration.source_platform_id,
        file_path=inspiration.file_path,
        thumbnail_path=inspiration.thumbnail_path,
        media_type=inspiration.media_type,
        dominant_colors=inspiration.dominant_colors,
        is_favorite=inspiration.is_favorite,
        quality_status=inspiration.quality_status,
        quality_reason=inspiration.quality_reason,
        created_at=inspiration.created_at,
        updated_at=inspiration.updated_at,
        tags=[
            InspirationTagOut(tag=TagOut.model_validate(t.tag), confidence=t.confidence)
            for t in inspiration.tags
        ],
        analysis_logs=[
            AnalysisLogOut.model_validate(log) for log in inspiration.analysis_logs
        ],
    )

    # 推断分析状态
    logs = inspiration.analysis_logs
    if not logs:
        detail.analysis_status = "none"
    elif any(log.error for log in logs):
        detail.analysis_status = "error"
    else:
        detail.analysis_status = "done"

    return detail


@router.patch("/{inspiration_id}", response_model=InspirationOut)
async def update_inspiration(
    inspiration_id: str,
    data: InspirationUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新灵感（收藏状态、作者等部分字段）。"""
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

    await db.flush()
    await db.refresh(inspiration)
    return _to_out(inspiration)


@router.post("/{inspiration_id}/tags", status_code=status.HTTP_200_OK)
async def add_inspiration_tags(
    inspiration_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """手动给素材关联标签（按名称查找或创建，已关联的自动跳过）。

    请求体: {"names": ["御姐长腿高跟鞋穿搭"], "category": "outfit", "source": "manual"}
    """
    inspiration = await db.get(Inspiration, inspiration_id)
    if not inspiration:
        raise HTTPException(status_code=404, detail="素材未找到")

    names = payload.get("names", [])
    category = payload.get("category", "free")
    source = payload.get("source", "manual")
    if not isinstance(names, list) or not names:
        raise HTTPException(status_code=400, detail="请提供标签名称列表")

    from app.services.tag_service import get_or_create_tag

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
    return {"added": added, "count": len(added)}


@router.delete("/{inspiration_id}/tags/{tag_id}", status_code=status.HTTP_200_OK)
async def remove_inspiration_tag(
    inspiration_id: str,
    tag_id: int,
    db: AsyncSession = Depends(get_db),
):
    """解除素材与某个标签的关联（不删除标签本身）。"""
    result = await db.execute(
        delete(InspirationTag).where(
            InspirationTag.inspiration_id == inspiration_id,
            InspirationTag.tag_id == tag_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到该标签关联")
    return {"removed": 1}


@router.delete("/{inspiration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inspiration(inspiration_id: str, db: AsyncSession = Depends(get_db)):
    """删除灵感素材及其对应的磁盘文件。"""
    result = await db.execute(
        select(Inspiration).where(Inspiration.id == inspiration_id)
    )
    inspiration = result.scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")

    # 写入墓碑表（防止重复采集）
    if inspiration.source_url:
        from app.models.scraper import ScraperSeenURL
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        await db.execute(
            sqlite_insert(ScraperSeenURL)
            .values(source_url=inspiration.source_url)
            .prefix_with("OR IGNORE")
        )

    # 删除磁盘文件
    delete_files(inspiration.file_path, inspiration.thumbnail_path)

    await db.delete(inspiration)
    await db.flush()


def _to_out(inspiration: Inspiration) -> InspirationOut:
    """将 ORM 模型转换为 API 响应模型。"""
    tags_out = [
        InspirationTagOut(
            tag=TagOut.model_validate(t.tag),
            confidence=t.confidence,
        )
        for t in inspiration.tags
    ]

    # 推断分析状态
    if not inspiration.analysis_logs:
        status = "none"
    elif any(log.error for log in inspiration.analysis_logs):
        status = "error"
    else:
        status = "done"

    return InspirationOut(
        id=inspiration.id,
        source_type=inspiration.source_type,
        source_url=inspiration.source_url,
        source_author=inspiration.source_author,
        source_platform_id=inspiration.source_platform_id,
        file_path=inspiration.file_path,
        thumbnail_path=inspiration.thumbnail_path,
        media_type=inspiration.media_type,
        dominant_colors=inspiration.dominant_colors,
        is_favorite=inspiration.is_favorite,
        quality_status=inspiration.quality_status,
        quality_reason=inspiration.quality_reason,
        created_at=inspiration.created_at,
        updated_at=inspiration.updated_at,
        tags=tags_out,
        analysis_status=status,
    )
