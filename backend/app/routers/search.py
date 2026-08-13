"""多维度搜索的 REST API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.inspiration import AIAnalysisLog, Inspiration, analysis_log_filter
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
    keyword: str | None = Query(
        None, description="全文搜索：标签名/作者名/文件名"
    ),
    dominant_color: str | None = Query(None),
    source_type: str | None = Query(None),
    media_type: str | None = Query(None),
    analysis_status: str | None = Query(None, description="done | pending | error"),
    tag_status: str | None = Query(None, description="tagged | untagged"),
    date_from: str | None = Query(None, description="ISO 日期，例如 2026-01-01"),
    date_to: str | None = Query(None),
    sort: str = Query("newest", description="newest | oldest | tag_count | match_score"),
    combine: str = Query("AND", description="标签组合逻辑 AND | OR"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """按多个维度搜索素材，支持关键词、标签、颜色、日期、来源等组合筛选。"""
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
    if media_type:
        conditions.append(Inspiration.media_type == media_type)
    if date_from:
        conditions.append(Inspiration.created_at >= date_from)
    if date_to:
        conditions.append(Inspiration.created_at <= date_to)
    if dominant_color:
        conditions.append(Inspiration.dominant_colors.contains(dominant_color))

    # 关键词搜索：标签名、作者名、文件名
    if keyword:
        kw = f"%{keyword}%"
        matching_tag_ids = select(InspirationTag.inspiration_id).join(
            Tag, InspirationTag.tag_id == Tag.id
        ).where(Tag.name.contains(keyword))
        kw_conds = [
            Inspiration.source_author.contains(keyword),
            Inspiration.file_path.contains(keyword),
            Inspiration.id.in_(matching_tag_ids),
        ]
        conditions.append(or_(*kw_conds))

    # 分析状态
    if analysis_status == "done":
        conditions.append(
            Inspiration.id.in_(
                select(AIAnalysisLog.inspiration_id).where(
                    analysis_log_filter(),
                    AIAnalysisLog.error.is_(None),
                ).distinct()
            )
        )
    elif analysis_status == "error":
        conditions.append(
            Inspiration.id.in_(
                select(AIAnalysisLog.inspiration_id).where(
                    analysis_log_filter(),
                    AIAnalysisLog.error.isnot(None),
                ).distinct()
            )
        )
    elif analysis_status == "pending":
        conditions.append(
            Inspiration.id.notin_(
                select(AIAnalysisLog.inspiration_id)
                .where(analysis_log_filter())
                .distinct()
            )
        )

    # 标签状态
    if tag_status == "tagged":
        conditions.append(
            Inspiration.id.in_(select(InspirationTag.inspiration_id).distinct())
        )
    elif tag_status == "untagged":
        conditions.append(
            Inspiration.id.notin_(select(InspirationTag.inspiration_id).distinct())
        )

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
            base_query = base_query.where(
                Inspiration.id.in_(
                    select(InspirationTag.inspiration_id).where(
                        InspirationTag.tag_id.in_(include_ids)
                    )
                )
            )
        else:  # AND
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

    # 排序
    sort_mapping = {
        "newest": Inspiration.created_at.desc(),
        "oldest": Inspiration.created_at.asc(),
        "tag_count": Inspiration.id.desc(),  # 占位，下面特殊处理
        "match_score": Inspiration.id.asc(),  # 占位，下面特殊处理
    }

    if sort == "tag_count":
        # 按标签数量降序（标签丰富的素材排前面）
        tag_count_sub = (
            select(
                InspirationTag.inspiration_id,
                func.count(InspirationTag.tag_id).label("cnt"),
            )
            .group_by(InspirationTag.inspiration_id)
            .subquery()
        )
        base_query = base_query.outerjoin(
            tag_count_sub,
            Inspiration.id == tag_count_sub.c.inspiration_id,
        ).order_by(func.coalesce(tag_count_sub.c.cnt, 0).desc())
    elif sort == "match_score" and include_ids:
        # 按匹配标签数量降序（包含更多搜索标签的排前面）
        match_sub = (
            select(
                InspirationTag.inspiration_id,
                func.count(InspirationTag.tag_id).label("cnt"),
            )
            .where(InspirationTag.tag_id.in_(include_ids))
            .group_by(InspirationTag.inspiration_id)
            .subquery()
        )
        base_query = base_query.outerjoin(
            match_sub,
            Inspiration.id == match_sub.c.inspiration_id,
        ).order_by(func.coalesce(match_sub.c.cnt, 0).desc())
    else:
        base_query = base_query.order_by(sort_mapping.get(sort, Inspiration.created_at.desc()))

    # 统计总数
    count_subquery = base_query.subquery()
    count_query = select(func.count()).select_from(count_subquery)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 分页
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

    result = await db.execute(
        select(Inspiration)
        .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
        .where(Inspiration.id == inspiration_id)
    )
    source = result.unique().scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="素材未找到")

    similar = await find_similar_images(db, inspiration_id, top_k)

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


@router.get("/suggestions")
async def search_suggestions(
    q: str = Query(..., min_length=1, description="搜索前缀"),
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """根据输入前缀返回匹配的标签名建议（用于搜索框自动补全）。"""
    result = await db.execute(
        select(Tag.name, func.count(InspirationTag.inspiration_id).label("cnt"))
        .outerjoin(InspirationTag, Tag.id == InspirationTag.tag_id)
        .where(Tag.name.contains(q))
        .group_by(Tag.name)
        .order_by(func.count(InspirationTag.inspiration_id).desc())
        .limit(limit)
    )
    return [
        {"name": row[0], "usage_count": row[1]}
        for row in result.all()
    ]


@router.get("/tag-cooccurrence")
async def tag_cooccurrence(
    tag_name: str = Query(..., description="标签名"),
    limit: int = Query(10, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
):
    """获取与指定标签经常一同出现的其他标签（共现分析）。"""
    tag_result = await db.execute(select(Tag.id).where(Tag.name == tag_name))
    tag_row = tag_result.first()
    if not tag_row:
        return {"tag": tag_name, "related": []}

    tag_id = tag_row[0]

    # 查找与 tag_name 共享素材最多的其他标签
    related = await db.execute(
        select(
            Tag.name,
            Tag.category,
            func.count(InspirationTag.inspiration_id).label("shared_count"),
        )
        .join(InspirationTag, Tag.id == InspirationTag.tag_id)
        .where(
            InspirationTag.inspiration_id.in_(
                select(InspirationTag.inspiration_id).where(
                    InspirationTag.tag_id == tag_id
                )
            ),
            Tag.id != tag_id,
        )
        .group_by(Tag.name, Tag.category)
        .order_by(func.count(InspirationTag.inspiration_id).desc())
        .limit(limit)
    )
    return {
        "tag": tag_name,
        "related": [
            {"name": row[0], "category": row[1], "shared_count": row[2]}
            for row in related.all()
        ],
    }


def _to_search_out(inspiration: Inspiration) -> InspirationOut:
    """将 Inspiration 模型转换为搜索结果的 InspirationOut。"""
    from app.routers.inspirations import _to_out
    return _to_out(inspiration)
