"""AI 分析路由的业务逻辑层：分析触发校验、队列统计、历史记录、结果对比。

本模块只负责「数据访问 + 业务规则」，不包含任何 HTTP 层逻辑：
- 触发分析前的素材校验（是否存在 / 是否为图片）
- 分析队列、历史记录的查询与统计
- 历史详情 / 结果对比的数据组装

路由层（routers/ai_analysis.py）负责解析参数、捕获本模块异常并转为 HTTPException。
"""

from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import (
    AIAnalysisLog,
    AIAnalysisTag,
    AIQualityReview,
    Inspiration,
    analysis_log_filter as _analysis_log_filter,
)
from app.models.tag import InspirationTag, Tag
from app.services.ai_tag_saver import iter_extracted_tags
from app.utils.time import format_utc


class AIAnalysisNotFoundError(Exception):
    """AI 分析相关记录不存在（路由层转为 404）。"""

    def __init__(self, message: str = "记录未找到"):
        super().__init__(message)
        self.message = message


class InvalidMediaError(Exception):
    """素材不是可分析的图片（路由层转为 400）。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _fmt_utc(dt) -> str | None:
    """将 naive UTC datetime 格式化为带 Z 后缀的 ISO 字符串（统一走 utils/time.format_utc）。"""
    return format_utc(dt)


async def trigger_analysis(
    db: AsyncSession, inspiration_id: str, non_image_message: str
) -> str:
    """校验素材是否可分析，返回可分析的图片文件路径。

    素材不存在抛 AIAnalysisNotFoundError；非图片素材抛 InvalidMediaError。

    参数:
        db: 数据库会话
        inspiration_id: 素材 UUID
        non_image_message: 非图片素材的提示文案（单个分析与重试的文案不同）
    """
    result = await db.execute(
        select(Inspiration).where(Inspiration.id == inspiration_id)
    )
    inspiration = result.scalar_one_or_none()
    if not inspiration:
        raise AIAnalysisNotFoundError("灵感素材未找到")
    if inspiration.media_type != "image":
        raise InvalidMediaError(non_image_message)
    return inspiration.file_path


async def get_batch_analyze_targets(
    db: AsyncSession, inspiration_ids: list[str]
) -> tuple[list[str], int]:
    """查询可分析的图片素材，返回 (可分析 ID 列表, 跳过数量)。

    无任何可分析素材时抛 AIAnalysisNotFoundError。
    """
    result = await db.execute(
        select(Inspiration).where(
            Inspiration.id.in_(inspiration_ids),
            Inspiration.media_type == "image",
            Inspiration.deleted_at.is_(None),
        )
    )
    inspirations = result.scalars().all()
    if not inspirations:
        raise AIAnalysisNotFoundError("未找到任何可分析的图片素材")

    ids = [insp.id for insp in inspirations]
    skipped = len(inspiration_ids) - len(inspirations)
    return ids, skipped


async def get_analysis_queue_stats(db: AsyncSession) -> dict:
    """统计分析队列状态：总图片数 / 已分析 / 未分析 / 失败。"""
    # 已分析过（仅统计标签分析日志且素材仍存在，排除质量审核日志）
    analyzed = await db.execute(
        select(func.count(func.distinct(AIAnalysisLog.inspiration_id)))
        .select_from(AIAnalysisLog)
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(
            _analysis_log_filter(),
            Inspiration.media_type == "image",
            Inspiration.deleted_at.is_(None),
        )
    )
    analyzed_count = analyzed.scalar() or 0

    # 总素材数（仅图片，暂不分析视频，排除垃圾桶）
    total = await db.execute(
        select(func.count()).select_from(Inspiration).where(
            Inspiration.media_type == "image",
            Inspiration.deleted_at.is_(None),
        )
    )
    total_count = total.scalar() or 0

    # 失败的 — 只看每个素材的最新分析日志（排除质量审核日志与垃圾桶素材）
    latest_log_sub = (
        select(
            AIAnalysisLog.inspiration_id,
            func.max(AIAnalysisLog.id).label("max_id"),
        )
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(
            _analysis_log_filter(),
            Inspiration.media_type == "image",
            Inspiration.deleted_at.is_(None),
        )
        .group_by(AIAnalysisLog.inspiration_id)
        .subquery()
    )
    failed = await db.execute(
        select(func.count()).select_from(AIAnalysisLog).join(
            latest_log_sub,
            AIAnalysisLog.id == latest_log_sub.c.max_id,
        ).where(AIAnalysisLog.error.isnot(None))
    )
    failed_count = failed.scalar() or 0

    # 未分析
    unanalyzed_count = max(0, total_count - analyzed_count)

    return {
        "total": total_count,
        "analyzed": analyzed_count,
        "unanalyzed": unanalyzed_count,
        "failed": failed_count,
    }


async def get_unanalyzed_ids(db: AsyncSession) -> list[str]:
    """返回所有未分析过的图片素材 ID 列表（暂不分析视频）。"""
    analyzed_sub = (
        select(AIAnalysisLog.inspiration_id)
        .where(_analysis_log_filter())
        .distinct()
    )
    result = await db.execute(
        select(Inspiration.id).where(
            Inspiration.id.notin_(analyzed_sub),
            Inspiration.media_type == "image",
            Inspiration.deleted_at.is_(None),
        )
    )
    return [row[0] for row in result]


def _parse_iso_dt(value: str) -> datetime:
    """将 ISO 时间字符串解析为 naive UTC datetime（与 DB 存储口径一致）。

    带时区的时间会先换算到 UTC 再剥离时区，避免直接 replace 导致筛选窗口
    偏移（如东八区 23:59 被当作 UTC 23:59 提前 8 小时截断）。
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


async def get_analysis_history(
    db: AsyncSession,
    page: int,
    size: int,
    status: str | None,
    model_name: str | None,
    inspiration_id: str | None,
    start_date: str | None = None,
    end_date: str | None = None,
    sort_by: str | None = None,
) -> dict:
    """分页查询分析历史记录（仅标签分析，排除质量审核日志），返回 items + total。

    支持时间范围筛选（start_date / end_date，ISO 字符串）与耗时排序
    （sort_by=time_asc|time_desc）。
    """
    query = select(AIAnalysisLog).where(_analysis_log_filter())
    if status == "success":
        query = query.where(AIAnalysisLog.error.is_(None))
    elif status == "error":
        query = query.where(AIAnalysisLog.error.isnot(None))
    if model_name:
        query = query.where(AIAnalysisLog.model_name == model_name)
    if inspiration_id:
        query = query.where(AIAnalysisLog.inspiration_id.contains(inspiration_id))
    if start_date:
        query = query.where(AIAnalysisLog.created_at >= _parse_iso_dt(start_date))
    if end_date:
        end_dt = _parse_iso_dt(end_date)
        # 纯日期（YYYY-MM-DD）扩展到当天结束，避免丢失结束日当天的记录
        if len(end_date.strip()) == 10:
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
        query = query.where(AIAnalysisLog.created_at <= end_dt)

    if sort_by == "time_asc":
        query = query.order_by(AIAnalysisLog.processing_time_ms.asc())
    elif sort_by == "time_desc":
        query = query.order_by(AIAnalysisLog.processing_time_ms.desc())
    else:
        query = query.order_by(AIAnalysisLog.created_at.desc())

    # 总数
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # 分页
    query = query.offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    logs = result.scalars().all()

    # 批量预加载关联素材（避免 N+1）
    insp_ids = [log.inspiration_id for log in logs]
    insp_map: dict[str, Inspiration] = {}
    if insp_ids:
        insp_result = await db.execute(
            select(Inspiration).where(Inspiration.id.in_(insp_ids))
        )
        insp_map = {i.id: i for i in insp_result.scalars().all()}

    # 批量预加载各日志「本次提取的标签」结构化快照（而非素材当前全量标签），
    # 避免失败日志误展示历史成功分析留下的标签
    log_ids = [log.id for log in logs]
    snap_map: dict[int, list[dict]] = {}
    if log_ids:
        snap_result = await db.execute(
            select(AIAnalysisTag.log_id, Tag.name, Tag.category)
            .join(Tag, AIAnalysisTag.tag_id == Tag.id)
            .where(AIAnalysisTag.log_id.in_(log_ids))
        )
        for log_id, tag_name, tag_cat in snap_result:
            snap_map.setdefault(log_id, []).append(
                {"name": tag_name, "category": tag_cat}
            )

    items = []
    for log in logs:
        insp = insp_map.get(log.inspiration_id)
        # 标签列只展示「本条日志」提取的标签：结构化快照优先；
        # 无快照的成功日志回退到解析 raw_response（兼容结构化存储上线前的旧数据）
        log_tags = snap_map.get(log.id, [])
        if not log_tags and not log.error and log.raw_response:
            from app.services.ai_parser import parse_analysis_response

            parsed = parse_analysis_response(log.raw_response)
            seen: set[str] = set()
            for name, category, _conf in iter_extracted_tags(parsed):
                if name not in seen:
                    seen.add(name)
                    log_tags.append({"name": name, "category": category})
        items.append({
            "id": log.id,
            "inspiration_id": log.inspiration_id,
            "model_name": log.model_name,
            "log_type": log.log_type or "analysis",
            "thumbnail_path": insp.thumbnail_path if insp else None,
            "file_path": insp.file_path if insp else None,
            "processing_time_ms": log.processing_time_ms,
            "error": log.error,
            "status": "error" if log.error else "success",
            "created_at": _fmt_utc(log.created_at),
            "tags": log_tags,
        })

    return {"items": items, "total": total, "page": page, "size": size}


async def export_analysis_history(
    db: AsyncSession,
    status: str | None,
    model_name: str | None,
    inspiration_id: str | None,
    start_date: str | None,
    end_date: str | None,
    sort_by: str | None,
) -> list[dict]:
    """导出分析历史（不分页，上限 10000 条），复用列表过滤与排序逻辑。"""
    result = await get_analysis_history(
        db, 1, 10000, status, model_name, inspiration_id, start_date, end_date, sort_by
    )
    return result["items"]


async def get_failed_analysis_targets(db: AsyncSession) -> list[tuple[str, str]]:
    """查询每个素材最新日志为失败的记录，返回 (素材 ID, 文件路径) 列表。

    子查询：每个素材的最新日志 ID（排除质量审核日志）。
    """
    latest_log = (
        select(AIAnalysisLog.inspiration_id, func.max(AIAnalysisLog.id).label("max_id"))
        .where(_analysis_log_filter())
        .group_by(AIAnalysisLog.inspiration_id)
        .subquery()
    )
    result = await db.execute(
        select(AIAnalysisLog.inspiration_id, Inspiration.file_path)
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .join(latest_log, (AIAnalysisLog.inspiration_id == latest_log.c.inspiration_id)
              & (AIAnalysisLog.id == latest_log.c.max_id))
        .where(AIAnalysisLog.error.isnot(None))
        .where(Inspiration.media_type == "image")
        .where(Inspiration.deleted_at.is_(None))
    )
    return [(r[0], r[1]) for r in result.all()]


async def delete_analysis_logs_batch(db: AsyncSession, log_ids: list[int]) -> int:
    """批量删除分析历史记录，返回删除数量。"""
    result = await db.execute(
        delete(AIAnalysisLog).where(AIAnalysisLog.id.in_(log_ids))
    )
    await db.commit()
    return result.rowcount


async def get_batch_retry_targets(
    db: AsyncSession, log_ids: list[int]
) -> list[tuple[str, str]]:
    """根据日志 ID 查询可重试的图片素材，返回 (素材 ID, 文件路径) 列表（去重，排除垃圾桶）。"""
    result = await db.execute(
        select(AIAnalysisLog.inspiration_id, Inspiration.file_path)
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(
            AIAnalysisLog.id.in_(log_ids),
            Inspiration.media_type == "image",
            Inspiration.deleted_at.is_(None),
        )
        .distinct()
    )
    return [(r[0], r[1]) for r in result.all()]


async def get_history_model_names(db: AsyncSession) -> list[str]:
    """获取分析历史中出现过的所有模型名称（排除质量审核日志）。"""
    result = await db.execute(
        select(AIAnalysisLog.model_name)
        .where(_analysis_log_filter())
        .distinct()
        .order_by(AIAnalysisLog.model_name)
    )
    return [row[0] for row in result]


async def delete_failed_logs(db: AsyncSession) -> int:
    """删除所有失败的标签分析日志（排除质量审核日志），返回删除数量。"""
    result = await db.execute(
        delete(AIAnalysisLog).where(
            _analysis_log_filter(),
            AIAnalysisLog.error.isnot(None),
        )
    )
    count = result.rowcount
    if count:
        await db.commit()
    return count


async def get_analysis_detail(db: AsyncSession, log_id: int) -> dict | None:
    """查询单条分析日志的详细信息（含关联标签、素材信息、解析结果与结构化快照），不存在返回 None。"""
    log = await db.get(AIAnalysisLog, log_id)
    if not log:
        return None

    # 获取关联的标签（素材当前全量标签）
    tag_result = await db.execute(
        select(Tag.name, Tag.category, InspirationTag.confidence)
        .join(Tag, InspirationTag.tag_id == Tag.id)
        .where(InspirationTag.inspiration_id == log.inspiration_id)
    )
    tags = [
        {"name": row.name, "category": row.category, "confidence": round(row.confidence, 2)}
        for row in tag_result
    ]

    # 结构化快照：本次分析提取的标签（按日志追溯）
    snapshot_result = await db.execute(
        select(Tag.name, Tag.category, AIAnalysisTag.confidence)
        .join(Tag, AIAnalysisTag.tag_id == Tag.id)
        .where(AIAnalysisTag.log_id == log.id)
        .order_by(AIAnalysisTag.id)
    )
    structured_tags = [
        {"name": row.name, "category": row.category, "confidence": round(row.confidence, 2)}
        for row in snapshot_result
    ]

    # 结构化质量审核结果（quality_check 日志）
    review_result = await db.execute(
        select(AIQualityReview)
        .where(AIQualityReview.log_id == log.id)
        .order_by(AIQualityReview.id)
    )
    quality_reviews = [
        {
            "result": r.result,
            "reason": r.reason,
            "reviewed_at": _fmt_utc(r.reviewed_at),
        }
        for r in review_result.scalars().all()
    ]

    # 获取素材信息
    insp = await db.get(Inspiration, log.inspiration_id)
    detail = {
        "id": log.id,
        "inspiration_id": log.inspiration_id,
        "model_name": log.model_name,
        "prompt_version": log.prompt_version,
        "model_version": log.model_version,
        "log_type": log.log_type or "analysis",
        "raw_response": log.raw_response,
        "processing_time_ms": log.processing_time_ms,
        "error": log.error,
        "status": "error" if log.error else "success",
        "created_at": _fmt_utc(log.created_at),
        "thumbnail_path": insp.thumbnail_path if insp else None,
        "file_path": insp.file_path if insp else None,
        "tags": tags,
        "structured_tags": structured_tags,
        "quality_reviews": quality_reviews,
    }

    # 尝试解析 raw_response 中的 JSON 便于前端展示
    parsed = None
    if log.raw_response:
        from app.services.ai_parser import parse_analysis_response
        parsed = parse_analysis_response(log.raw_response) or None
    detail["parsed_response"] = parsed

    return detail


async def delete_analysis_log(db: AsyncSession, log_id: int) -> bool:
    """删除指定分析日志，返回是否删除成功。"""
    log = await db.get(AIAnalysisLog, log_id)
    if not log:
        return False
    await db.delete(log)
    await db.commit()
    return True


async def get_inspiration_preview_map(
    db: AsyncSession, inspiration_ids: list[str]
) -> dict[str, dict]:
    """批量查询素材的缩略图/文件路径，返回 {素材 ID: {"thumbnail_path": ..., "file_path": ...}}。"""
    if not inspiration_ids:
        return {}
    result = await db.execute(
        select(Inspiration.id, Inspiration.thumbnail_path, Inspiration.file_path)
        .where(Inspiration.id.in_(inspiration_ids))
    )
    return {r[0]: {"thumbnail_path": r[1], "file_path": r[2]} for r in result}


def _build_analysis_item(log: AIAnalysisLog, structured_tags: list[str] | None) -> dict:
    """将单条分析日志转换为对比视图条目（结构化快照优先，否则实时解析）。"""
    from app.services.ai_parser import parse_analysis_response

    parsed = parse_analysis_response(log.raw_response) if log.raw_response else {}
    # 结构化快照优先（精确记录本次提取）；否则回退到实时解析
    tags = structured_tags
    if tags is None:
        tags = sorted({
            tag_name
            for tag_name, _cat, _conf in iter_extracted_tags(parsed)
        })
    return {
        "id": log.id,
        "model_name": log.model_name,
        "prompt_version": log.prompt_version,
        "model_version": log.model_version,
        "processing_time_ms": log.processing_time_ms,
        "error": log.error,
        "status": "error" if log.error else "success",
        "created_at": _fmt_utc(log.created_at),
        "parsed_response": parsed,
        "structured_tags": tags,
        "tags_count": {
            "style": len((parsed.get("style") or [])),
            "items": len((parsed.get("items") or [])),
            "fit": len((parsed.get("fit") or [])),
            "wear_style": len((parsed.get("wear_style") or [])),
            "attributes": len((parsed.get("attributes") or [])),
            "colors": len((parsed.get("dominant_colors") or [])),
        },
    }


async def get_analysis_comparison(db: AsyncSession, inspiration_id: str) -> dict:
    """获取同一素材的所有历史分析结果，用于并排对比。

    返回：
    - analyses: 每次分析的详情列表（按时间排序）
    - tag_diff: 各次分析间的标签差异（新增/消失/共同）
    - time_comparison: 耗时对比数据

    无分析记录时抛 AIAnalysisNotFoundError。
    """
    # 获取该素材的所有分析日志（排除质量审核日志）
    result = await db.execute(
        select(AIAnalysisLog)
        .where(
            _analysis_log_filter(),
            AIAnalysisLog.inspiration_id == inspiration_id,
        )
        .order_by(AIAnalysisLog.created_at.asc())
    )
    logs = result.scalars().all()

    if len(logs) < 1:
        raise AIAnalysisNotFoundError("该素材暂无分析记录")

    insp = await db.get(Inspiration, inspiration_id)

    # 批量加载各日志的结构化标签快照（若存在），优先用于对比
    log_ids = [log.id for log in logs]
    snapshot_map: dict[int, list[str]] = {}
    if log_ids:
        snap_result = await db.execute(
            select(AIAnalysisTag.log_id, Tag.name)
            .join(Tag, AIAnalysisTag.tag_id == Tag.id)
            .where(AIAnalysisTag.log_id.in_(log_ids))
        )
        for snap_log_id, tag_name in snap_result:
            snapshot_map.setdefault(snap_log_id, []).append(tag_name)

    analyses = [
        _build_analysis_item(log, snapshot_map.get(log.id)) for log in logs
    ]

    # 标签差异对比（取第一次和最后一次分析，优先用结构化快照）
    tag_diff = None
    if len(analyses) >= 2:
        first_tags = set(analyses[0]["structured_tags"])
        last_tags = set(analyses[-1]["structured_tags"])
        tag_diff = {
            "first_analysis_id": analyses[0]["id"],
            "last_analysis_id": analyses[-1]["id"],
            "added": sorted(list(last_tags - first_tags)),
            "removed": sorted(list(first_tags - last_tags)),
            "common": sorted(list(first_tags & last_tags)),
        }

    # 耗时对比
    time_comparison = [
        {
            "analysis_id": a["id"],
            "model_name": a["model_name"],
            "processing_time_ms": a["processing_time_ms"],
            "created_at": a["created_at"],
        }
        for a in analyses
    ]

    return {
        "inspiration_id": inspiration_id,
        "thumbnail_path": insp.thumbnail_path if insp else None,
        "file_path": insp.file_path if insp else None,
        "analyses": analyses,
        "analyses_count": len(analyses),
        "tag_diff": tag_diff,
        "time_comparison": time_comparison,
    }
