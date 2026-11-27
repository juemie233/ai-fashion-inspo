"""多维度搜索的 REST API 路由。"""

import asyncio
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.schemas.inspiration import (
    AnalysisStatus,
    InspirationListOut,
    MediaType,
    SearchSort,
    TagCombine,
    TagStatus,
    inspiration_to_out,
)
from app.schemas.search import (
    SimilarItemOut,
    SimilarOut,
    VectorSearchItem,
    VectorSearchOut,
    VectorStatusOut,
)
from app.services import search_suggest_service, vector_store
from app.services.embedding_service import (
    generate_image_embedding,
    generate_text_embedding,
    get_image_embedding_status,
    get_text_embedding_status,
)
from app.services import file_service
from app.services.inspiration_query import (
    RATING_SORTS,
    build_inspiration_filters,
    inspiration_load_options,
    load_live_inspiration,
    load_live_inspirations,
    rating_sort_clauses,
    rating_sort_filter,
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
    media_type: MediaType | None = Query(None),
    is_favorite: bool | None = Query(None, description="仅收藏 / 全部"),
    analysis_status: AnalysisStatus | None = Query(None, description="done | pending | error"),
    tag_status: TagStatus | None = Query(None, description="tagged | untagged"),
    date_from: str | None = Query(None, description="ISO 日期，例如 2026-01-01"),
    date_to: str | None = Query(None),
    rating_min: int | None = Query(None, ge=1, le=5, description="评分下限（评分 >= 该值）"),
    sort: SearchSort = Query(
        "newest",
        description=(
            "newest | oldest | tag_count | match_score | rating | "
            "rating_asc（仅已评分） | unrated_first（未评分优先）"
        ),
    ),
    combine: TagCombine = Query("AND", description="标签组合逻辑 AND | OR"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> InspirationListOut:
    """按多个维度搜索素材，支持关键词、标签、颜色、日期、来源等组合筛选。"""
    include_list = (
        [t.strip() for t in include_tags.split(",") if t.strip()]
        if include_tags else []
    )
    exclude_list = (
        [t.strip() for t in exclude_tags.split(",") if t.strip()]
        if exclude_tags else []
    )

    conditions = build_inspiration_filters(
        source_type=source_type,
        media_type=media_type,
        is_favorite=is_favorite,
        analysis_status=analysis_status,
        tag_status=tag_status,
        include_tags=include_list,
        exclude_tags=exclude_list,
        tag_mode=combine,
        keyword=keyword,
        dominant_color=dominant_color,
        date_from=date_from,
        date_to=date_to,
        rating_min=rating_min,
    )
    base_query = select(Inspiration).options(
        *inspiration_load_options(),
    ).where(*conditions)

    # match_score 排序需要 include_ids；筛选条件本身由共享 builder 负责
    include_ids: list[int] = []
    if include_list:
        tag_result = await db.execute(
            select(Tag.id).where(Tag.name.in_(include_list))
        )
        include_ids = [row[0] for row in tag_result.all()]

    if sort == "rating_asc":
        filter_clause = rating_sort_filter(sort)
        if filter_clause is not None:
            base_query = base_query.where(filter_clause)

    # 先 count，再做 ORDER BY / tag_count / match_score 的 JOIN
    total = (
        await db.execute(select(func.count()).select_from(base_query.subquery()))
    ).scalar() or 0

    if sort == "tag_count":
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
        ).order_by(
            func.coalesce(tag_count_sub.c.cnt, 0).desc(),
            Inspiration.created_at.desc(),
            Inspiration.id.desc(),
        )
    elif sort == "match_score" and include_ids:
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
        ).order_by(
            func.coalesce(match_sub.c.cnt, 0).desc(),
            Inspiration.created_at.desc(),
            Inspiration.id.desc(),
        )
    elif sort in RATING_SORTS:
        base_query = base_query.order_by(*rating_sort_clauses(sort))
    elif sort == "oldest":
        base_query = base_query.order_by(
            Inspiration.created_at.asc(), Inspiration.id.asc()
        )
    else:
        # newest / match_score 缺 include_tags / 未知 sort 均回退 newest
        base_query = base_query.order_by(
            Inspiration.created_at.desc(), Inspiration.id.desc()
        )

    # 分页
    base_query = base_query.offset((page - 1) * size).limit(size)

    result = await db.execute(base_query)
    inspirations = result.unique().scalars().all()

    return InspirationListOut(
        items=[inspiration_to_out(i) for i in inspirations],
        total=total,
        page=page,
        size=size,
    )


@router.post("/vector", response_model=VectorSearchOut)
async def vector_search(
    text: str | None = Form(default=None, max_length=1000),
    file: UploadFile | None = File(default=None),
    top_k: int | None = Form(default=None, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> VectorSearchOut:
    """语义搜索 / 以图搜图：接受文本或图片，返回 TopK 相似素材。

    请求体为 multipart/form-data：
        - text: 搜索文本（语义搜索，走 Ollama 文本向量 + 文本向量表）
        - file: 搜索图片（以图搜图，走 CLIP 图像向量 + 图像向量表）
        - top_k: 返回数量（默认 20）
    """
    top_k = top_k or settings.vector_top_k_default
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
            # 先验证确实是图片；否则 CLIP 返回 None 会被误报成“未安装 CLIP 依赖”
            await asyncio.to_thread(
                file_service.validate_media, tmp_path, getattr(file, "content_type", None)
            )
            query_vec = await generate_image_embedding(file_path=str(tmp_path))
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)
        if not query_vec:
            raise HTTPException(status_code=503, detail=_IMAGE_VEC_UNAVAILABLE_MSG)
        hits = await vector_store.search_vectors("image", query_vec, top_k)
        query_type = "image"

    # 一次 IN 取回全部命中，避免逐条 load_live_inspiration 的 N+1；
    # 向量库可能包含已软删除素材，过滤后可能少于 top_k，所以上面多取一倍。
    hit_ids = [str(hit["inspiration_id"]) for hit in hits]
    by_id = await load_live_inspirations(db, hit_ids)
    items = [
        VectorSearchItem(
            inspiration=inspiration_to_out(by_id[str(hit["inspiration_id"])]),
            score=hit["score"],
        )
        for hit in hits
        if str(hit["inspiration_id"]) in by_id
    ][:top_k]

    return VectorSearchOut(
        query_type=query_type,
        query_text=text,
        items=items,
        total=len(items),
    )


@router.get("/vector/status", response_model=VectorStatusOut)
async def vector_search_status(db: AsyncSession = Depends(get_db)) -> VectorStatusOut:
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
) -> SimilarOut:
    """根据图像向量 + 标签匹配加权排序寻找相似素材。

    优先使用图像向量（视觉相似，权重 0.6）+ 标签重合度（权重 0.4）加权排序；
    图像向量不可用或无向量数据时，回退到纯标签匹配。
    结果中 match_source 标记：visual（视觉）/ hybrid（混合）/ tag（标签）。
    """
    source = await load_live_inspiration(db, inspiration_id)
    if not source:
        raise HTTPException(status_code=404, detail="素材未找到")

    similar = await find_similar_hybrid(db, source, top_k)

    out_items = [
        SimilarItemOut(
            inspiration=inspiration_to_out(item["inspiration"]),
            similarity=item["similarity"],
            shared_tags=item["shared_tags"],
            match_source=item["match_source"],
        )
        for item in similar
    ]

    return SimilarOut(
        source=inspiration_to_out(source),
        similar=out_items,
    )


@router.get("/suggestions")
async def search_suggestions(
    q: str = Query(..., min_length=1, description="搜索前缀"),
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """根据输入前缀返回匹配的标签名建议；查询在 ``search_suggest_service``。"""
    return await search_suggest_service.search_suggestions(db, q, limit)


@router.get("/tag-cooccurrence")
async def tag_cooccurrence(
    tag_name: str = Query(..., description="标签名"),
    limit: int = Query(10, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """与指定标签常一起出现的其他标签；查询在 ``search_suggest_service``。"""
    return await search_suggest_service.tag_cooccurrence(db, tag_name, limit)


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
    except BaseException:
        # CancelledError 也要清理；否则客户端断开时半写文件会残留
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path
