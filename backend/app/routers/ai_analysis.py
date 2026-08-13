"""AI 子路由。"""

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    analysis_log_filter as _analysis_log_filter,
)
from app.routers.ai_shared import (
    _analysis_semaphore,
    _active_analyses,
    _analysis_tasks,
    _task_by_id,
    _pending_queue,
    get_queue_paused,
    set_queue_paused,
    _quality_active,
    _run_analysis,
    _run_quality_check,
    _update_env_file,
    _fmt_utc,
    _format_size,
)
from app.services.model_config import get_model_config, update_model_config
from app.utils.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


# ============ AI 分析 ============


@router.post("/analyze/{inspiration_id}")
async def analyze_inspiration(
    inspiration_id: str,
    db: AsyncSession = Depends(get_db),
):
    """触发单个素材的 AI 分析（后台异步执行）。"""
    result = await db.execute(
        select(Inspiration).where(Inspiration.id == inspiration_id)
    )
    inspiration = result.scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    if inspiration.media_type != "image":
        raise HTTPException(status_code=400, detail="仅支持分析图片素材，视频素材暂不支持")

    task = asyncio.create_task(_run_analysis(inspiration_id, inspiration.file_path))
    _analysis_tasks.add(task)
    task.add_done_callback(_analysis_tasks.discard)
    return {
        "message": "分析任务已加入队列",
        "inspiration_id": inspiration_id,
        "status": "analyzing",
    }


@router.post("/batch-analyze")
async def batch_analyze(
    inspiration_ids: list[str],
    db: AsyncSession = Depends(get_db),
):
    """批量触发多个素材的 AI 分析。

    已改造为「数据库驱动的任务队列」：创建任务记录后立即返回 task_id，
    由独立 worker 进程（app/worker.py）异步执行，前端通过轮询
    GET /api/tasks/{task_id} 获取进度。
    """
    result = await db.execute(
        select(Inspiration).where(
            Inspiration.id.in_(inspiration_ids),
            Inspiration.media_type == "image",
        )
    )
    inspirations = result.scalars().all()

    if not inspirations:
        raise HTTPException(status_code=404, detail="未找到任何可分析的图片素材")

    ids = [insp.id for insp in inspirations]
    skipped = len(inspiration_ids) - len(inspirations)

    from app.services.task_runner import create_batch_analyze_task
    task = await create_batch_analyze_task(db, ids, skipped)

    return {
        "task_id": task.id,
        "message": f"已创建批量分析任务 #{task.id}，共 {len(ids)} 个素材"
                   + (f"，跳过 {skipped} 个素材（不存在或非图片）" if skipped > 0 else ""),
        "count": len(ids),
        "skipped": skipped,
        "status": "pending",
    }


# ============ 分析队列与历史 ============


@router.get("/queue")
async def analysis_queue(db: AsyncSession = Depends(get_db)):
    """获取分析队列状态：待分析/分析中/已完成/失败统计。"""
    # 已分析过（仅统计标签分析日志且素材仍存在，排除质量审核日志）
    analyzed = await db.execute(
        select(func.count(func.distinct(AIAnalysisLog.inspiration_id)))
        .select_from(AIAnalysisLog)
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(_analysis_log_filter(), Inspiration.media_type == "image")
    )
    analyzed_count = analyzed.scalar() or 0

    # 总素材数（仅图片，暂不分析视频）
    total = await db.execute(
        select(func.count()).select_from(Inspiration).where(
            Inspiration.media_type == "image"
        )
    )
    total_count = total.scalar() or 0

    # 失败的 — 只看每个素材的最新分析日志（排除质量审核日志）
    latest_log_sub = (
        select(
            AIAnalysisLog.inspiration_id,
            func.max(AIAnalysisLog.id).label("max_id"),
        )
        .where(_analysis_log_filter())
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


@router.get("/unanalyzed-ids")
async def unanalyzed_ids(db: AsyncSession = Depends(get_db)):
    """获取所有未分析过的图片素材 ID 列表（暂不分析视频）。"""
    analyzed_sub = (
        select(AIAnalysisLog.inspiration_id)
        .where(_analysis_log_filter())
        .distinct()
    )
    result = await db.execute(
        select(Inspiration.id).where(
            Inspiration.id.notin_(analyzed_sub),
            Inspiration.media_type == "image",
        )
    )
    ids = [row[0] for row in result]
    return {"ids": ids, "count": len(ids)}


@router.get("/history")
async def analysis_history(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    status: str | None = None,  # success | error
    model_name: str | None = None,  # 按模型筛选
    inspiration_id: str | None = None,  # 按素材 ID 搜索
    db: AsyncSession = Depends(get_db),
):
    """获取分析历史记录列表（仅标签分析，排除质量审核日志）。"""
    query = select(AIAnalysisLog).where(_analysis_log_filter())
    if status == "success":
        query = query.where(AIAnalysisLog.error.is_(None))
    elif status == "error":
        query = query.where(AIAnalysisLog.error.isnot(None))
    if model_name:
        query = query.where(AIAnalysisLog.model_name == model_name)
    if inspiration_id:
        query = query.where(AIAnalysisLog.inspiration_id.contains(inspiration_id))

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

    # 批量预加载标签
    from app.models.tag import InspirationTag as IT, Tag as T
    tag_map: dict[str, list[dict]] = {}
    if insp_ids:
        tag_result = await db.execute(
            select(IT.inspiration_id, T.name, T.category)
            .join(T, IT.tag_id == T.id)
            .where(IT.inspiration_id.in_(insp_ids))
        )
        for insp_id, tag_name, tag_cat in tag_result:
            tag_map.setdefault(insp_id, []).append(
                {"name": tag_name, "category": tag_cat}
            )

    items = []
    for log in logs:
        insp = insp_map.get(log.inspiration_id)
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
            "tags": tag_map.get(log.inspiration_id, []),
        })

    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/retry/{inspiration_id}")
async def retry_analysis(
    inspiration_id: str,
    db: AsyncSession = Depends(get_db),
):
    """重试失败的分析。"""
    result = await db.execute(
        select(Inspiration).where(Inspiration.id == inspiration_id)
    )
    inspiration = result.scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")

    if inspiration.media_type != "image":
        raise HTTPException(status_code=400, detail="暂不支持分析视频文件")

    task = asyncio.create_task(_run_analysis(inspiration_id, inspiration.file_path))
    _analysis_tasks.add(task)
    task.add_done_callback(_analysis_tasks.discard)
    return {"message": "已重新加入分析队列", "inspiration_id": inspiration_id}


@router.post("/retry-all-failed")
async def retry_all_failed(db: AsyncSession = Depends(get_db)):
    """一键重试所有失败的分析（仅取每个素材最新记录为失败的）。"""
    # 子查询：每个素材的最新日志 ID（排除质量审核日志）
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
    )
    failed = result.all()

    if not failed:
        return {"message": "没有失败的记录", "count": 0}

    count = 0
    for insp_id, file_path in failed:
        task = asyncio.create_task(_run_analysis(insp_id, file_path))
        _analysis_tasks.add(task)
        task.add_done_callback(_analysis_tasks.discard)
        count += 1

    return {"message": f"已将 {count} 个素材重新加入分析队列", "count": count}


@router.post("/history/batch-delete")
async def batch_delete_logs(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """批量删除分析历史记录。

    请求体: {"ids": [1, 2, 3]}
    """
    ids = payload.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="请提供要删除的记录 ID 列表")
    result = await db.execute(
        delete(AIAnalysisLog).where(AIAnalysisLog.id.in_(ids))
    )
    await db.commit()
    return {"deleted": result.rowcount}


@router.post("/history/batch-retry")
async def batch_retry_logs(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """批量重试分析记录：根据日志 ID 找到对应素材并重新分析。

    请求体: {"ids": [1, 2, 3]}
    """
    ids = payload.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="请提供要重试的记录 ID 列表")
    result = await db.execute(
        select(AIAnalysisLog.inspiration_id, Inspiration.file_path)
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(
            AIAnalysisLog.id.in_(ids),
            Inspiration.media_type == "image",
        )
        .distinct()
    )
    rows = result.all()
    count = 0
    for insp_id, file_path in rows:
        task = asyncio.create_task(_run_analysis(insp_id, file_path))
        _analysis_tasks.add(task)
        task.add_done_callback(_analysis_tasks.discard)
        count += 1
    return {"message": f"已将 {count} 个素材加入分析队列", "count": count}


@router.get("/history/model-names")
async def get_history_model_names(db: AsyncSession = Depends(get_db)):
    """获取分析历史中出现过的所有模型名称，供前端筛选（排除质量审核日志）。"""
    result = await db.execute(
        select(AIAnalysisLog.model_name)
        .where(_analysis_log_filter())
        .distinct()
        .order_by(AIAnalysisLog.model_name)
    )
    names = [row[0] for row in result]
    return {"models": names}


@router.delete("/history/failed/all")
async def delete_all_failed_logs(db: AsyncSession = Depends(get_db)):
    """批量删除所有失败的分析日志。"""
    result = await db.execute(
        delete(AIAnalysisLog).where(AIAnalysisLog.error.isnot(None))
    )
    count = result.rowcount

    if count == 0:
        return {"message": "没有失败的记录", "count": 0}

    await db.commit()
    logger.info(f"已批量删除 {count} 条失败的 AI 分析记录")
    return {"message": f"已删除 {count} 条失败记录", "count": count}


@router.get("/history/{log_id}")
async def get_analysis_detail(
    log_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取单条分析日志的详细信息，包含原始 AI 响应和关联标签。"""
    log = await db.get(AIAnalysisLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="分析记录未找到")

    # 获取关联的标签
    from app.models.tag import InspirationTag, Tag
    tag_result = await db.execute(
        select(Tag.name, Tag.category, InspirationTag.confidence)
        .join(Tag, InspirationTag.tag_id == Tag.id)
        .where(InspirationTag.inspiration_id == log.inspiration_id)
    )
    tags = [
        {"name": row.name, "category": row.category, "confidence": round(row.confidence, 2)}
        for row in tag_result
    ]

    # 获取素材信息
    insp = await db.get(Inspiration, log.inspiration_id)
    detail = {
        "id": log.id,
        "inspiration_id": log.inspiration_id,
        "model_name": log.model_name,
        "raw_response": log.raw_response,
        "processing_time_ms": log.processing_time_ms,
        "error": log.error,
        "status": "error" if log.error else "success",
        "created_at": _fmt_utc(log.created_at) if log.created_at else None,
        "thumbnail_path": insp.thumbnail_path if insp else None,
        "file_path": insp.file_path if insp else None,
        "tags": tags,
    }

    # 尝试解析 raw_response 中的 JSON 便于前端展示
    parsed = None
    if log.raw_response:
        from app.services.ai_parser import parse_analysis_response
        parsed = parse_analysis_response(log.raw_response) or None
    detail["parsed_response"] = parsed

    return detail


@router.delete("/history/{log_id}")
async def delete_analysis_log(
    log_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除指定分析日志。"""
    log = await db.get(AIAnalysisLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="分析记录未找到")
    await db.delete(log)
    await db.commit()
    return {"message": f"分析记录 #{log_id} 已删除"}


@router.get("/queue/pending")
async def get_pending_queue(db: AsyncSession = Depends(get_db)):
    """获取排队中素材的缩略图预览信息。"""
    if not _pending_queue and not _active_analyses:
        return {"items": [], "paused": get_queue_paused()}

    # 所有活跃/排队中的素材 ID
    all_ids = list(_active_analyses.keys())
    if not all_ids:
        return {"items": [], "paused": get_queue_paused()}

    result = await db.execute(
        select(Inspiration.id, Inspiration.thumbnail_path, Inspiration.file_path)
        .where(Inspiration.id.in_(all_ids))
    )
    insp_map = {r[0]: {"thumbnail_path": r[1], "file_path": r[2]} for r in result}

    items = []
    for insp_id in _pending_queue:
        info = insp_map.get(insp_id, {})
        items.append({
            "inspiration_id": insp_id,
            "thumbnail_path": info.get("thumbnail_path"),
            "file_path": info.get("file_path"),
            "status": "排队中",
        })
    for insp_id, status in _active_analyses.items():
        if insp_id not in _pending_queue:
            info = insp_map.get(insp_id, {})
            items.append({
                "inspiration_id": insp_id,
                "thumbnail_path": info.get("thumbnail_path"),
                "file_path": info.get("file_path"),
                "status": status,
            })

    return {"items": items, "paused": get_queue_paused()}


@router.delete("/queue/{inspiration_id}")
async def cancel_queue_item(inspiration_id: str):
    """取消排队中的分析任务（已开始分析的无法取消）。"""
    if inspiration_id in _pending_queue:
        # 取消对应的 asyncio Task
        task = _task_by_id.pop(inspiration_id, None)
        if task and not task.done():
            task.cancel()
        _pending_queue.remove(inspiration_id)
        _active_analyses.pop(inspiration_id, None)
        return {"message": "已取消排队任务"}
    elif inspiration_id in _active_analyses and inspiration_id not in _pending_queue:
        raise HTTPException(status_code=409, detail="任务正在执行中，无法取消。可等待完成后查看结果")
    else:
        raise HTTPException(status_code=404, detail="任务不在队列中")


@router.post("/queue/pause")
async def pause_queue():
    """暂停全局分析队列（已完成的不受影响）。"""
    set_queue_paused(True)
    logger.info("分析队列已暂停")
    return {"message": "队列已暂停", "paused": True}


@router.post("/queue/resume")
async def resume_queue():
    """恢复全局分析队列。"""
    set_queue_paused(False)
    logger.info("分析队列已恢复")
    return {"message": "队列已恢复", "paused": False}


@router.get("/active-analyses")
async def get_active_analyses():
    """获取当前正在分析中的素材列表，用于前端轮询显示进度。"""
    return {"active_analyses": _active_analyses, "count": len(_active_analyses)}


# ============ 分析结果对比 ============


@router.get("/compare/{inspiration_id}")
async def compare_analyses(
    inspiration_id: str,
    db: AsyncSession = Depends(get_db),
):
    """获取同一素材的所有历史分析结果，用于并排对比。

    返回：
    - analyses: 每次分析的详情列表（按时间排序）
    - tag_diff: 各次分析间的标签差异（新增/消失/共同）
    - time_comparison: 耗时对比数据
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
        raise HTTPException(status_code=404, detail="该素材暂无分析记录")

    insp = await db.get(Inspiration, inspiration_id)

    # 获取每次分析关联的标签
    from app.models.tag import InspirationTag, Tag
    tag_result = await db.execute(
        select(InspirationTag, Tag.name, Tag.category)
        .join(Tag, InspirationTag.tag_id == Tag.id)
        .where(InspirationTag.inspiration_id == inspiration_id)
    )
    # 注意：标签是素材级别的，不是每次分析独立的
    # 这里我们展示每次分析的 raw_response 解析结果来对比
    from app.services.ai_parser import parse_analysis_response

    analyses = []
    for log in logs:
        parsed = parse_analysis_response(log.raw_response) if log.raw_response else {}
        analyses.append({
            "id": log.id,
            "model_name": log.model_name,
            "processing_time_ms": log.processing_time_ms,
            "error": log.error,
            "status": "error" if log.error else "success",
            "created_at": _fmt_utc(log.created_at) if log.created_at else None,
            "parsed_response": parsed,
            "tags_count": {
                "style": len((parsed.get("style") or [])),
                "items": len((parsed.get("items") or [])),
                "fit": len((parsed.get("fit") or [])),
                "wear_style": len((parsed.get("wear_style") or [])),
                "attributes": len((parsed.get("attributes") or [])),
                "colors": len((parsed.get("dominant_colors") or [])),
            },
        })

    # 标签差异对比（取第一次和最后一次分析）
    tag_diff = None
    if len(analyses) >= 2:
        first = analyses[0]["parsed_response"]
        last = analyses[-1]["parsed_response"]

        def _tag_set(parsed: dict) -> set[str]:
            tags: set[str] = set()
            for key in ("style", "fit", "wear_style", "attributes"):
                vals = parsed.get(key, [])
                if isinstance(vals, list):
                    for v in vals:
                        tags.add(f"{key}:{v}" if isinstance(v, str) else f"{key}:{v.get('name', str(v))}")
            for item in (parsed.get("items") or []):
                if isinstance(item, dict):
                    tags.add(f"单品:{item.get('type', '')} {item.get('color', '')}")
            for c in (parsed.get("dominant_colors") or []):
                tags.add(f"颜色:{c}" if isinstance(c, str) else str(c))
            return tags

        first_tags = _tag_set(first)
        last_tags = _tag_set(last)
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
