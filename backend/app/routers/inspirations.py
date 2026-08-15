"""灵感素材 CRUD 的 REST API 路由。"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.inspiration import Inspiration
from app.schemas.inspiration import (
    BatchAddTagsRequest,
    InspirationDetailOut,
    InspirationListOut,
    InspirationOut,
    InspirationUpdate,
    InspirationTagOut,
    TagOut,
)
from app.services import inspiration_service

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

    inspiration = await inspiration_service.create_inspiration(
        db,
        file,
        source_type=source_type,
        source_url=source_url,
        source_author=source_author,
        source_platform_id=source_platform_id,
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

    inspiration = await inspiration_service.create_inspiration_from_url(
        db,
        url,
        source_author=source_author,
        tag_names=tag_names,
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
    sort: str = "newest",
    db: AsyncSession = Depends(get_db),
):
    """分页获取灵感列表，支持多维筛选和排序。"""
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
        sort=sort,
    )
    return InspirationListOut(
        items=[_to_out(i) for i in inspirations],
        total=total,
        page=page,
        size=size,
    )


@router.delete("/quality-rejected", status_code=status.HTTP_200_OK)
async def delete_rejected_inspirations(db: AsyncSession = Depends(get_db)):
    """物理删除所有质量审核被拒绝（rejected）的素材，释放磁盘空间。"""
    return await inspiration_service.delete_rejected_inspirations(db)


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


@router.delete("/{inspiration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inspiration(inspiration_id: str, db: AsyncSession = Depends(get_db)):
    """删除灵感素材及其对应的磁盘文件。"""
    await inspiration_service.delete_inspiration(db, inspiration_id)


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
        is_ai_generated=inspiration.is_ai_generated,
        created_at=inspiration.created_at,
        updated_at=inspiration.updated_at,
        tags=tags_out,
        analysis_status=status,
    )
