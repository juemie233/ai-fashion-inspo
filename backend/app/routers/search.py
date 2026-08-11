"""多维度搜索的 REST API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.schemas.inspiration import InspirationListOut, InspirationOut

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=InspirationListOut)
async def search_inspirations(
    include_tags: str | None = Query(
        None, description="逗号分隔的标签名称（需包含）"
    ),
    exclude_tags: str | None = Query(
        None, description="逗号分隔的标签名称（需排除）"
    ),
    dominant_color: str | None = Query(None),
    source_type: str | None = Query(None),
    date_from: str | None = Query(None, description="ISO 日期，例如 2026-01-01"),
    date_to: str | None = Query(None),
    combine: str = Query("AND", description="标签组合逻辑 AND | OR"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """按多个标签维度搜索灵感素材。"""
    include_list = (
        [t.strip() for t in include_tags.split(",") if t.strip()]
        if include_tags else []
    )
    exclude_list = (
        [t.strip() for t in exclude_tags.split(",") if t.strip()]
        if exclude_tags else []
    )

    # 基础查询（预加载标签）
    base_query = select(Inspiration).options(
        selectinload(Inspiration.tags).selectinload(InspirationTag.tag)
    )

    # 收集筛选条件
    conditions = []

    if source_type:
        conditions.append(Inspiration.source_type == source_type)
    if date_from:
        conditions.append(Inspiration.created_at >= date_from)
    if date_to:
        conditions.append(Inspiration.created_at <= date_to)
    if dominant_color:
        conditions.append(Inspiration.dominant_colors.contains(dominant_color))

    if conditions:
        base_query = base_query.where(and_(*conditions))

    # 应用标签筛选
    if include_list:
        include_tag_ids = select(Tag.id).where(Tag.name.in_(include_list))
        tag_result = await db.execute(include_tag_ids)
        include_ids = [row[0] for row in tag_result.all()]

        if not include_ids:
            return InspirationListOut(items=[], total=0, page=page, size=size)

        if combine.upper() == "OR":
            # 包含任意指定标签即可
            base_query = base_query.where(
                Inspiration.id.in_(
                    select(InspirationTag.inspiration_id).where(
                        InspirationTag.tag_id.in_(include_ids)
                    )
                )
            )
        else:  # AND
            # 必须同时包含所有指定标签
            for tag_id in include_ids:
                base_query = base_query.where(
                    Inspiration.id.in_(
                        select(InspirationTag.inspiration_id).where(
                            InspirationTag.tag_id == tag_id
                        )
                    )
                )

    if exclude_list:
        exclude_tag_ids = select(Tag.id).where(Tag.name.in_(exclude_list))
        tag_result = await db.execute(exclude_tag_ids)
        exclude_ids = [row[0] for row in tag_result.all()]

        if exclude_ids:
            base_query = base_query.where(
                ~Inspiration.id.in_(
                    select(InspirationTag.inspiration_id).where(
                        InspirationTag.tag_id.in_(exclude_ids)
                    )
                )
            )

    # 统计总数
    count_subquery = base_query.subquery()
    count_query = select(func.count()).select_from(count_subquery)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 排序和分页
    base_query = base_query.order_by(Inspiration.created_at.desc())
    base_query = base_query.offset((page - 1) * size).limit(size)

    result = await db.execute(base_query)
    inspirations = result.unique().scalars().all()

    return InspirationListOut(
        items=[_to_search_out(i) for i in inspirations],
        total=total,
        page=page,
        size=size,
    )


@router.get("/similar/{inspiration_id}")
async def similar_inspirations(
    inspiration_id: str,
    top_k: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """根据标签相似度寻找与指定素材最相似的其他素材。"""
    from app.services.embedding_service import find_similar_images

    # 验证源素材存在
    result = await db.execute(
        select(Inspiration)
        .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
        .where(Inspiration.id == inspiration_id)
    )
    source = result.unique().scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="素材未找到")

    similar = await find_similar_images(db, inspiration_id, top_k)

    # 加载完整信息
    out_items = []
    for item in similar:
        result = await db.execute(
            select(Inspiration)
            .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
            .where(Inspiration.id == item["id"])
        )
        insp = result.unique().scalar_one_or_none()
        if insp:
            out = _to_search_out(insp)
            out_items.append({
                "inspiration": out,
                "similarity": item["similarity"],
                "shared_tags": item["shared_tags"],
            })

    return {
        "source": _to_search_out(source),
        "similar": out_items,
    }


def _to_search_out(inspiration: Inspiration) -> InspirationOut:
    """将 Inspiration 模型转换为搜索结果的 InspirationOut。"""
    from app.routers.inspirations import _to_out
    return _to_out(inspiration)
