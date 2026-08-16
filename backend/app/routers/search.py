"""多维度搜索的 REST API 路由。"""

import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.inspiration import AIAnalysisLog, Inspiration, analysis_log_filter
from app.models.tag import InspirationTag, Tag
from app.schemas.inspiration import InspirationListOut, InspirationOut
from app.schemas.search import (
    SimilarItemOut,
    SimilarOut,
    VectorSearchItem,
    VectorSearchOut,
    VectorStatusOut,
)
from app.services import vector_store
from app.services.embedding_service import (
    generate_image_embedding,
    generate_text_embedding,
    get_image_embedding_status,
    get_text_embedding_status,
)
from app.services.vector_service import find_similar_hybrid

router = APIRouter(prefix="/api/search", tags=["search"])

# 向量不可用时给用户的可读提示
_TEXT_VEC_UNAVAILABLE_MSG = (
    "文本向量不可用：请确认 Ollama 已启动且已安装 embedding 模型 "
    f"（ollama pull {settings.ollama_embedding_model}）"
)
_IMAGE_VEC_UNAVAILABLE_MSG = (
    "图像向量不可用：请先安装 CLIP 依赖（pip install sentence-transformers），"
    f"并确认模型 {settings.clip_model_name} 已下载（首次加载会自动下载）"
)


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

    # 基础查询（预加载标签，排除垃圾桶中已软删除的素材）
    base_query = select(Inspiration).options(
        selectinload(Inspiration.tags).selectinload(InspirationTag.tag)
    ).where(Inspiration.deleted_at.is_(None))

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


@router.post("/vector", response_model=VectorSearchOut)
async def vector_search(
    text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    top_k: int = Form(default=settings.vector_top_k_default, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """语义搜索 / 以图搜图：接受文本或图片，返回 TopK 相似素材。

    请求体为 multipart/form-data：
        - text: 搜索文本（语义搜索，走 Ollama 文本向量 + 文本向量表）
        - file: 搜索图片（以图搜图，走 CLIP 图像向量 + 图像向量表）
        - top_k: 返回数量（默认 20）
    """
    if not text and not file:
        raise HTTPException(status_code=400, detail="请提供搜索文本或图片")
    if text and file:
        raise HTTPException(status_code=400, detail="请勿同时提供文本与图片")

    # 未安装 LanceDB 时直接返回 503（可选依赖，项目默认不强制安装）
    if not vector_store.is_lancedb_available():
        raise HTTPException(
            status_code=503, detail="lancedb 未安装，请执行 pip install lancedb"
        )

    if text:
        query_vec = await generate_text_embedding(text)
        if not query_vec:
            raise HTTPException(status_code=503, detail=_TEXT_VEC_UNAVAILABLE_MSG)
        hits = await vector_store.search_vectors("text", query_vec, top_k)
        query_type = "text"
    else:
        tmp_path = None
        try:
            tmp_path = await _save_temp_image(file)
            query_vec = await generate_image_embedding(file_path=str(tmp_path))
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)
        if not query_vec:
            raise HTTPException(status_code=503, detail=_IMAGE_VEC_UNAVAILABLE_MSG)
        hits = await vector_store.search_vectors("image", query_vec, top_k)
        query_type = "image"

    items: list[VectorSearchItem] = []
    for hit in hits:
        insp = await _load_inspiration(db, hit["inspiration_id"])
        if insp:
            items.append(
                VectorSearchItem(
                    inspiration=_to_search_out(insp),
                    score=hit["score"],
                )
            )

    return VectorSearchOut(
        query_type=query_type,
        query_text=text,
        items=items,
        total=len(items),
    )


@router.get("/vector/status", response_model=VectorStatusOut)
async def vector_search_status(db: AsyncSession = Depends(get_db)):
    """查询向量检索能力状态（LanceDB / 文本向量 / 图像向量 / 存量向量数量）。"""
    lancedb_available = vector_store.is_lancedb_available()
    text_count = (
        await vector_store.count_vectors("text") if lancedb_available else 0
    )
    image_count = (
        await vector_store.count_vectors("image") if lancedb_available else 0
    )
    return VectorStatusOut(
        lancedb_available=lancedb_available,
        lancedb_dir=str(settings.lancedb_dir),
        text_embedding=get_text_embedding_status(),
        image_embedding=get_image_embedding_status(),
        text_vector_count=text_count,
        image_vector_count=image_count,
    )


@router.get("/similar/{inspiration_id}", response_model=SimilarOut)
async def similar_inspirations(
    inspiration_id: str,
    top_k: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """根据图像向量 + 标签匹配加权排序寻找相似素材。

    优先使用图像向量（视觉相似，权重 0.6）+ 标签重合度（权重 0.4）加权排序；
    图像向量不可用或无向量数据时，回退到纯标签匹配。
    结果中 match_source 标记：visual（视觉）/ hybrid（混合）/ tag（标签）。
    """
    source = await _load_inspiration(db, inspiration_id)
    if not source:
        raise HTTPException(status_code=404, detail="素材未找到")

    similar = await find_similar_hybrid(db, source, top_k)

    out_items = [
        SimilarItemOut(
            inspiration=_to_search_out(item["inspiration"]),
            similarity=item["similarity"],
            shared_tags=item["shared_tags"],
            match_source=item["match_source"],
        )
        for item in similar
    ]

    return SimilarOut(
        source=_to_search_out(source),
        similar=out_items,
    )


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
    from app.schemas.inspiration import inspiration_to_out

    return inspiration_to_out(inspiration)


async def _load_inspiration(db: AsyncSession, inspiration_id: str) -> Inspiration | None:
    """加载素材（预加载标签，排除垃圾桶素材），不存在时返回 None。"""
    result = await db.execute(
        select(Inspiration)
        .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
        .where(
            Inspiration.id == inspiration_id,
            Inspiration.deleted_at.is_(None),
        )
    )
    return result.unique().scalar_one_or_none()


async def _save_temp_image(file: UploadFile) -> Path:
    """将上传图片保存到临时目录，返回临时文件路径（调用方负责删除）。

    LanceDB 的图像向量基于本地文件生成，这里使用独立临时目录，
    不污染正式 images 存储目录。分块流式写入并限制大小，
    避免超大文件整体驻留内存；失败时清理残留文件。
    """
    tmp_dir = settings.storage_root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"vec_{uuid.uuid4().hex}.img"
    size_limit = settings.max_image_upload_mb * 1024 * 1024
    total = 0
    try:
        async with aiofiles.open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > size_limit:
                    raise HTTPException(
                        status_code=400,
                        detail=f"文件超过大小限制（{settings.max_image_upload_mb}MB）",
                    )
                await f.write(chunk)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path
