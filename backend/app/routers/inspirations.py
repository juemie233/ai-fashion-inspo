"""灵感素材 CRUD 的 REST API 路由。"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.inspiration import Inspiration
from app.schemas.inspiration import (
    BatchAddTagsRequest,
    BatchFavoriteRequest,
    BatchTrashRequest,
    BatchUpdateRequest,
    InspirationDetailOut,
    InspirationListOut,
    InspirationOut,
    InspirationUpdate,
    InspirationTagOut,
    MoveToTrashRequest,
    TagOut,
    analysis_status_from_logs,
    inspiration_to_out,
)
from app.schemas.person import PersonBriefOut, PersonLinkRequest
from app.services import inspiration_service, person_service

router = APIRouter(prefix="/api/inspirations", tags=["inspirations"])


@router.post("", response_model=InspirationOut, status_code=status.HTTP_201_CREATED)
async def create_inspiration(
    file: UploadFile = File(...),
    source_type: str = Form(default="manual_upload"),
    source_url: str | None = Form(default=None),
    source_author: str | None = Form(default=None),
    source_platform_id: str | None = Form(default=None),
    scraper_task_id: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    """上传图片并创建灵感素材。AI 分析在后台异步执行。

    scraper_task_id 用于浏览器插件采集：将素材关联到采集任务记录，
    便于在采集管理页按任务查看结果与统计。
    """
    # 校验文件类型
    allowed_types = ("image/jpeg", "image/png", "image/webp", "image/gif", "video/mp4")
    if file.content_type and file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {file.content_type}。允许: {allowed_types}",
        )

    inspiration = await inspiration_service.create_inspiration(
        db,
        file,
        source_type=source_type,
        source_url=source_url,
        source_author=source_author,
        source_platform_id=source_platform_id,
        scraper_task_id=scraper_task_id,
    )
    return _to_out(inspiration)


@router.post("/from-url", response_model=InspirationOut, status_code=status.HTTP_201_CREATED)
async def create_from_url(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """从 URL 下载图片并创建素材。

    请求体: {"url": "...", "source_author": "...", "tags": ["..."]}
    """
    url = payload.get("url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="请提供图片 URL")

    source_author = payload.get("source_author", "").strip() or None
    tag_names = payload.get("tags", [])
    # 通用「从 URL 导入」默认标记为 url_import；插件采集链路可显式传 browser_extension
    source_type = payload.get("source_type") or "url_import"
    if not isinstance(source_type, str) or len(source_type) > 32:
        raise HTTPException(status_code=400, detail="source_type 非法")

    inspiration = await inspiration_service.create_inspiration_from_url(
        db,
        url,
        source_author=source_author,
        tag_names=tag_names,
        source_type=source_type,
    )
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
    is_ai_generated: bool | None = None,  # 仅筛选疑似 AI 生成素材
    include_tags: str | None = Query(None, description="逗号分隔的标签名（需同时包含）"),
    dominant_color: str | None = Query(None, description="主色调 hex 值（子串匹配）"),
    date_from: str | None = Query(None, description="上传日期下限，ISO 日期"),
    date_to: str | None = Query(None, description="上传日期上限，ISO 日期"),
    sort: str = "newest",
    db: AsyncSession = Depends(get_db),
):
    """分页获取灵感列表，支持多维筛选和排序。"""
    include_list = (
        [t.strip() for t in include_tags.split(",") if t.strip()]
        if include_tags else None
    )
    inspirations, total = await inspiration_service.list_inspirations(
        db,
        page=page,
        size=size,
        source_type=source_type,
        is_favorite=is_favorite,
        media_type=media_type,
        analysis_status=analysis_status,
        tag_status=tag_status,
        quality_status=quality_status,
        is_ai_generated=is_ai_generated,
        include_tags=include_list,
        dominant_color=dominant_color,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
    )
    return InspirationListOut(
        items=[_to_out(i) for i in inspirations],
        total=total,
        page=page,
        size=size,
    )


@router.get("/dominant-colors", status_code=status.HTTP_200_OK)
async def dominant_colors(
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """返回库内实际出现的主色调（hex）及出现次数，供颜色筛选下拉展示。"""
    return await inspiration_service.list_dominant_colors(db, limit=limit)


@router.delete("/quality-rejected", status_code=status.HTTP_200_OK)
async def delete_rejected_inspirations(db: AsyncSession = Depends(get_db)):
    """物理删除所有质量审核被拒绝（rejected）的素材，释放磁盘空间。"""
    return await inspiration_service.delete_rejected_inspirations(db)


# ── 垃圾桶（软删除）──
# 注意：GET /trash、DELETE /trash 必须声明在 /{inspiration_id} 之前，避免被动态路由吞掉


@router.get("/trash", response_model=InspirationListOut)
async def list_trash(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    reason: str | None = Query(None, description="按删除原因筛选（质量差/重复/不喜欢/隐私/其他）"),
    db: AsyncSession = Depends(get_db),
):
    """分页获取垃圾桶中的素材（软删除，30 天内可恢复）。"""
    items, total = await inspiration_service.list_trash(db, page=page, size=size, reason=reason)
    return InspirationListOut(
        items=[_to_out(i) for i in items],
        total=total,
        page=page,
        size=size,
        trash_retention_days=settings.trash_retention_days,
    )


@router.delete("/trash", status_code=status.HTTP_200_OK)
async def empty_trash(
    only_expired: bool = Query(False, description="为 true 时仅清理超过保留期的过期素材"),
    db: AsyncSession = Depends(get_db),
):
    """彻底清空垃圾桶（物理删除文件与数据库记录）。"""
    return await inspiration_service.purge_trash(db, only_expired=only_expired)


@router.post("/{inspiration_id}/trash", response_model=InspirationOut)
async def move_to_trash(
    inspiration_id: str,
    payload: MoveToTrashRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """将素材移入垃圾桶（软删除），文件移入 storage/trash/，30 天内可恢复。"""
    inspiration = await inspiration_service.trash_inspiration(
        db, inspiration_id, payload.reason if payload else None
    )
    return _to_out(inspiration)


@router.post("/{inspiration_id}/restore", response_model=InspirationOut)
async def restore_inspiration(inspiration_id: str, db: AsyncSession = Depends(get_db)):
    """从垃圾桶恢复素材（移回媒体目录，清除软删除标记）。"""
    inspiration = await inspiration_service.restore_inspiration(db, inspiration_id)
    return _to_out(inspiration)


@router.get("/{inspiration_id}", response_model=InspirationDetailOut)
async def get_inspiration(inspiration_id: str, db: AsyncSession = Depends(get_db)):
    """获取单个灵感详情（包含完整标签和分析日志）。"""
    inspiration = await inspiration_service.get_inspiration(db, inspiration_id)

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
        is_ai_generated=inspiration.is_ai_generated,
        deleted_at=inspiration.deleted_at,
        trash_reason=inspiration.trash_reason,
        created_at=inspiration.created_at,
        updated_at=inspiration.updated_at,
        tags=[
            InspirationTagOut(tag=TagOut.model_validate(t.tag), confidence=t.confidence)
            for t in inspiration.tags
        ],
        persons=[
            PersonBriefOut.model_validate(t.person) for t in inspiration.persons
        ],
        analysis_logs=[
            AnalysisLogOut.model_validate(log) for log in inspiration.analysis_logs
        ],
    )

    # 推断分析状态：仅看最新一次标签分析日志（旧失败日志不覆盖后续成功）
    detail.analysis_status = analysis_status_from_logs(inspiration.analysis_logs)

    return detail


@router.patch("/{inspiration_id}", response_model=InspirationOut)
async def update_inspiration(
    inspiration_id: str,
    data: InspirationUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新灵感（收藏状态、作者等部分字段）。"""
    inspiration = await inspiration_service.update_inspiration(db, inspiration_id, data)
    return _to_out(inspiration)


@router.post("/batch-tags", status_code=status.HTTP_200_OK)
async def batch_add_tags(
    data: BatchAddTagsRequest,
    db: AsyncSession = Depends(get_db),
):
    """批量给多个素材关联标签（按名称查找或创建，已关联的自动跳过）。

    请求体: {"inspiration_ids": [...], "names": [...], "category": "outfit", "source": "manual"}

    用于「相似素材批量添加穿搭大标签」场景：把当前素材的大标签一次性复制到
    多个相似素材上。仅对实际新增了标签的素材重建文本向量，避免无谓调用 Ollama。
    """
    return await inspiration_service.batch_add_tags(
        db,
        data.inspiration_ids,
        data.names,
        category=data.category,
        source=data.source,
    )


@router.post("/batch-favorite", status_code=status.HTTP_200_OK)
async def batch_favorite(
    data: BatchFavoriteRequest,
    db: AsyncSession = Depends(get_db),
):
    """批量收藏/取消收藏素材（仅作用于未删除素材）。"""
    updated = await inspiration_service.batch_favorite_inspirations(
        db, data.ids, data.is_favorite
    )
    return {"updated": updated}


@router.post("/batch-trash", status_code=status.HTTP_200_OK)
async def batch_trash(
    data: BatchTrashRequest,
    db: AsyncSession = Depends(get_db),
):
    """批量将素材移入垃圾桶（软删除，30 天内可恢复）。

    reason 为空时按各素材状态自动推断；不存在/已在垃圾桶中的 ID 计入 skipped。
    """
    return await inspiration_service.batch_trash_inspirations(
        db, data.ids, data.reason
    )


@router.post("/batch-update", status_code=status.HTTP_200_OK)
async def batch_update(
    data: BatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """批量编辑素材元数据（来源/收藏/审核状态/疑似 AI 标记），仅更新显式字段。"""
    updated = await inspiration_service.batch_update_inspirations(
        db,
        data.ids,
        source_type=data.source_type,
        is_favorite=data.is_favorite,
        quality_status=data.quality_status,
        is_ai_generated=data.is_ai_generated,
    )
    return {"updated": updated}


@router.post("/{inspiration_id}/tags", status_code=status.HTTP_200_OK)
async def add_inspiration_tags(
    inspiration_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """手动给素材关联标签（按名称查找或创建，已关联的自动跳过）。

    请求体: {"names": ["御姐长腿高跟鞋穿搭"], "category": "outfit", "source": "manual"}
    """
    names = payload.get("names", [])
    category = payload.get("category", "free")
    source = payload.get("source", "manual")
    return await inspiration_service.add_inspiration_tags(
        db,
        inspiration_id,
        names,
        category=category,
        source=source,
    )


@router.delete("/{inspiration_id}/tags/{tag_id}", status_code=status.HTTP_200_OK)
async def remove_inspiration_tag(
    inspiration_id: str,
    tag_id: int,
    db: AsyncSession = Depends(get_db),
):
    """解除素材与某个标签的关联（不删除标签本身）。"""
    return await inspiration_service.remove_inspiration_tag(db, inspiration_id, tag_id)


# ── 人物关联（对标 tag 关联写法；关联用 person_id 规避同名歧义）──


@router.post("/{inspiration_id}/persons", status_code=status.HTTP_200_OK)
async def link_inspiration_persons(
    inspiration_id: str,
    data: PersonLinkRequest,
    db: AsyncSession = Depends(get_db),
):
    """给素材批量关联人物（幂等，已关联自动跳过）。

    请求体: {"person_ids": [1, 2]}——人物不存在时静默跳过该 ID；素材不存在返回 404。
    """
    result = await person_service.link_persons_batch(db, inspiration_id, data.person_ids)
    if not result["inspiration_exists"]:
        raise HTTPException(status_code=404, detail="素材未找到")
    added = []
    for link in result["links"]:
        added.append(
            PersonBriefOut(
                id=link.person_id,
                name=link.person.name,
                person_type=link.person.person_type,
                platform=link.person.platform,
                avatar_path=link.person.avatar_path,
            )
        )
    # 关联对象已在批量函数内逐条 flush（SAVEPOINT），此处无需再 flush
    return {"added": [a.model_dump() for a in added], "count": len(added)}


@router.delete("/{inspiration_id}/persons/{person_id}", status_code=status.HTTP_200_OK)
async def unlink_inspiration_person(
    inspiration_id: str,
    person_id: int,
    db: AsyncSession = Depends(get_db),
):
    """解除素材与某个人物的关联（不删除人物本身）。"""
    if not await person_service.unlink_person(db, inspiration_id, person_id):
        raise HTTPException(status_code=404, detail="未找到该人物关联")
    return {"removed": 1}


@router.delete("/{inspiration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inspiration(inspiration_id: str, db: AsyncSession = Depends(get_db)):
    """彻底删除灵感素材（物理删除文件与数据库记录，不可恢复）。

    普通「删除」请使用 ``POST /{id}/trash``（移入垃圾桶，可恢复）；
    本端点用于垃圾桶中的「彻底删除」或内部物理清理场景。
    """
    await inspiration_service.delete_inspiration(db, inspiration_id)


def _to_out(inspiration: Inspiration) -> InspirationOut:
    """将 ORM 模型转换为 API 响应模型（实现已收敛到 schemas.inspiration_to_out）。"""
    return inspiration_to_out(inspiration)
