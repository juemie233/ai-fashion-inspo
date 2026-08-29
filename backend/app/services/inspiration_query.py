"""灵感素材查询：列表分页筛选、详情、主色调统计。"""

import asyncio
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    NOT_DELETED,
    analysis_log_filter,
    latest_analysis_log_subquery,
)
from app.models.person import InspirationBlogger, InspirationModel
from app.models.tag import InspirationTag, Tag


async def load_inspiration_full(
    db: AsyncSession, inspiration_id: str
) -> Inspiration | None:
    """按 ID 加载素材（预加载标签/博主/模特关系），不存在返回 None。

    供详情 / 垃圾桶移入恢复 / 列表复用，收敛散落的
    「select + selectinload(tags/bloggers/models)」重复块。
    """
    result = await db.execute(
        select(Inspiration)
        .options(
            selectinload(Inspiration.tags).selectinload(InspirationTag.tag),
            selectinload(Inspiration.bloggers).selectinload(InspirationBlogger.blogger),
            selectinload(Inspiration.models).selectinload(InspirationModel.model),
        )
        .where(Inspiration.id == inspiration_id)
    )
    return result.unique().scalar_one_or_none()


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
    ids: list[str] | None = None,           # 精确 ID 过滤（定位跳转用，与其他筛选条件叠加）
    rating_min: int | None = None,          # 评分下限（评分 >= 该值，0 表示未评分也通过）
    sort: str = "newest",
) -> tuple[list[Inspiration], int]:
    """分页查询灵感列表，支持多维筛选和排序。

    参数:
        ids: 非空时仅返回这些 ID 的素材（用于「定位跳转」精确展示）
            ；与其余筛选条件为叠加（AND）关系，已删除素材始终排除。

    返回:
        (素材列表, 总数)
    """
    query = select(Inspiration).options(
        selectinload(Inspiration.tags).selectinload(InspirationTag.tag),
        selectinload(Inspiration.bloggers).selectinload(InspirationBlogger.blogger),
        selectinload(Inspiration.models).selectinload(InspirationModel.model),
    ).where(NOT_DELETED)

    if ids:
        query = query.where(Inspiration.id.in_(ids))
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
    if rating_min is not None:
        # 评分筛选：rating >= rating_min；rating_min=0 时所有未删除素材（含未评分 0）都通过
        query = query.where(Inspiration.rating >= rating_min)
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
        "rating": Inspiration.rating.desc(),  # 评分降序（高分优先）
        "rating_asc": Inspiration.rating.asc(),  # 评分升序
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

        def _sort_by_size() -> list[str]:
            """按文件实际大小降序取页内 ID（同步 stat 为阻塞 I/O，线程池执行）。"""
            ordered = sorted(size_rows, key=_file_size, reverse=True)
            return [r[0] for r in ordered[(page - 1) * size : page * size]]

        page_ids = await asyncio.to_thread(_sort_by_size)

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


def build_filters_from_query_json(query_json: dict) -> list[ColumnElement[bool]]:
    """把智能合集的 query_json 翻译为 SQLAlchemy 筛选条件（供合集动态求值复用）。

    复用素材库列表/搜索的同一套筛选口径（关键词：标签名/作者名/文件名；
    标签：AND/OR；评分、日期、来源、媒体类型、收藏），保证「素材库怎么筛、
    合集就怎么算」。字段契约见 docs/收藏合集设计方案.md：
    keyword / tag_ids / tag_mode / source_types / media_type /
    is_favorite / min_rating / start_date / end_date；未知键忽略（向前兼容），
    值类型非法的键同样忽略。无条件命中时仅返回「排除垃圾桶」这一条
    （所有合集内容查询一律排除 deleted_at IS NOT NULL 的素材）。
    """
    # 垃圾桶素材一律不在合集内容中出现（手动/智能均排除，恢复后自动重现）
    conditions: list[ColumnElement[bool]] = [NOT_DELETED]
    if not isinstance(query_json, dict):
        return conditions

    # 关键词：标签名 / 作者名 / 文件名（与搜索接口同一口径）
    keyword = query_json.get("keyword")
    if isinstance(keyword, str) and keyword.strip():
        keyword = keyword.strip()
        matching_tag_ids = select(InspirationTag.inspiration_id).join(
            Tag, InspirationTag.tag_id == Tag.id
        ).where(Tag.name.contains(keyword))
        conditions.append(
            or_(
                Inspiration.source_author.contains(keyword),
                Inspiration.file_path.contains(keyword),
                Inspiration.id.in_(matching_tag_ids),
            )
        )

    # 标签：tag_id 存储，AND/OR 两种组合语义（AND：须同时包含所有标签）
    tag_ids = query_json.get("tag_ids")
    if isinstance(tag_ids, list) and tag_ids:
        tag_ids = [t for t in tag_ids if isinstance(t, int)]
    if tag_ids:
        tag_mode = query_json.get("tag_mode")
        if tag_mode not in ("and", "or"):
            tag_mode = "and"
        if tag_mode == "or":
            conditions.append(
                Inspiration.id.in_(
                    select(InspirationTag.inspiration_id).where(
                        InspirationTag.tag_id.in_(tag_ids)
                    )
                )
            )
        else:
            for tag_id in tag_ids:
                conditions.append(
                    Inspiration.id.in_(
                        select(InspirationTag.inspiration_id).where(
                            InspirationTag.tag_id == tag_id
                        )
                    )
                )

    # 来源类型（列表，任一命中）
    source_types = query_json.get("source_types")
    if isinstance(source_types, list) and source_types:
        source_types = [s for s in source_types if isinstance(s, str) and s]
        if source_types:
            conditions.append(Inspiration.source_type.in_(source_types))

    # 媒体类型（image | video）
    media_type = query_json.get("media_type")
    if isinstance(media_type, str) and media_type:
        conditions.append(Inspiration.media_type == media_type)

    # 收藏筛选（true/false，null 忽略）
    is_favorite = query_json.get("is_favorite")
    if isinstance(is_favorite, bool):
        conditions.append(Inspiration.is_favorite == is_favorite)

    # 评分下限（0~5；与素材库 rating >= min_rating 同口径）
    min_rating = query_json.get("min_rating")
    if isinstance(min_rating, int) and not isinstance(min_rating, bool) and 0 < min_rating <= 5:
        conditions.append(Inspiration.rating >= min_rating)

    # 上传日期范围（与素材库 date_from/date_to 同口径）
    start_date = query_json.get("start_date")
    if isinstance(start_date, str) and start_date:
        conditions.append(Inspiration.created_at >= start_date)
    end_date = query_json.get("end_date")
    if isinstance(end_date, str) and end_date:
        conditions.append(Inspiration.created_at <= end_date)

    return conditions


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


async def get_inspiration(db: AsyncSession, inspiration_id: str) -> Inspiration:
    """获取单个灵感详情（包含完整标签和分析日志），不存在则抛出 404。"""
    result = await db.execute(
        select(Inspiration)
        .options(
            selectinload(Inspiration.tags).selectinload(InspirationTag.tag),
            selectinload(Inspiration.analysis_logs),
            selectinload(Inspiration.bloggers).selectinload(InspirationBlogger.blogger),
            selectinload(Inspiration.models).selectinload(InspirationModel.model),
        )
        .where(Inspiration.id == inspiration_id)
    )
    inspiration = result.unique().scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    return inspiration
