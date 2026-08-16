"""AI 子路由。"""

import asyncio
import csv
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.ai_shared import (
    _active_analyses,
    _analysis_tasks,
    _task_by_id,
    _pending_queue,
    get_queue_paused,
    set_queue_paused,
    _run_analysis,
)
from app.services import ai_analysis_service as ai_svc
from app.services.task_runner import create_batch_analyze_task
from app.utils.csv_safety import sanitize_csv_cell

logger = logging.getLogger(__name__)
router = APIRouter()


# ============ AI 分析 ============


@router.post("/analyze/{inspiration_id}")
async def analyze_inspiration(
    inspiration_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """触发单个素材的 AI 分析（后台异步执行）。"""
    try:
        file_path = await ai_svc.trigger_analysis(
            db, inspiration_id, "仅支持分析图片素材，视频素材暂不支持"
        )
    except ai_svc.AIAnalysisNotFoundError:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    except ai_svc.InvalidMediaError as e:
        raise HTTPException(status_code=400, detail=e.message)

    task = asyncio.create_task(_run_analysis(inspiration_id, file_path))
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
) -> dict[str, str | int]:
    """批量触发多个素材的 AI 分析。

    已改造为「数据库驱动的任务队列」：创建任务记录后立即返回 task_id，
    由独立 worker 进程（app/worker.py）异步执行，前端通过轮询
    GET /api/tasks/{task_id} 获取进度。
    """
    try:
        ids, skipped = await ai_svc.get_batch_analyze_targets(db, inspiration_ids)
    except ai_svc.AIAnalysisNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)

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
async def analysis_queue(db: AsyncSession = Depends(get_db)) -> dict:
    """获取分析队列状态：待分析/分析中/已完成/失败统计。"""
    return await ai_svc.get_analysis_queue_stats(db)


@router.get("/unanalyzed-ids")
async def unanalyzed_ids(db: AsyncSession = Depends(get_db)) -> dict[str, list[str] | int]:
    """获取所有未分析过的图片素材 ID 列表（暂不分析视频）。"""
    ids = await ai_svc.get_unanalyzed_ids(db)
    return {"ids": ids, "count": len(ids)}


@router.get("/history")
async def analysis_history(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    status: str | None = None,  # success | error
    model_name: str | None = None,  # 按模型筛选
    inspiration_id: str | None = None,  # 按素材 ID 搜索
    start_date: str | None = None,  # 起始时间（ISO，含）
    end_date: str | None = None,  # 结束时间（ISO，含）
    sort_by: str | None = None,  # time_asc | time_desc（默认按时间倒序）
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取分析历史记录列表（仅标签分析，排除质量审核日志）。"""
    try:
        return await ai_svc.get_analysis_history(
            db, page, size, status, model_name, inspiration_id,
            start_date, end_date, sort_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"时间参数格式错误: {e}")


@router.get("/history/export")
async def export_analysis_history_csv(
    status: str | None = None,
    model_name: str | None = None,
    inspiration_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sort_by: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """导出分析历史为 CSV（当前筛选条件，上限 10000 条）。"""
    try:
        items = await ai_svc.export_analysis_history(
            db, status, model_name, inspiration_id, start_date, end_date, sort_by
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"时间参数格式错误: {e}")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "素材ID", "模型", "状态", "耗时(ms)", "失败原因", "时间", "标签"])
    for it in items:
        row = [
            it["id"],
            it["inspiration_id"],
            it["model_name"],
            it["status"],
            it["processing_time_ms"] if it["processing_time_ms"] is not None else "",
            it["error"] or "",
            it["created_at"] or "",
            "、".join(t["name"] for t in it["tags"]),
        ]
        # 防 CSV 公式注入：模型名/失败原因/标签为模型或用户可控，前缀 = + - @ 时加 ' 转义
        writer.writerow([sanitize_csv_cell(x) for x in row])
    csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM 让 Excel 正确识别中文
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="analysis_history.csv"'},
    )


@router.post("/retry/{inspiration_id}")
async def retry_analysis(
    inspiration_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """重试失败的分析。"""
    try:
        file_path = await ai_svc.trigger_analysis(
            db, inspiration_id, "暂不支持分析视频文件"
        )
    except ai_svc.AIAnalysisNotFoundError:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    except ai_svc.InvalidMediaError as e:
        raise HTTPException(status_code=400, detail=e.message)

    task = asyncio.create_task(_run_analysis(inspiration_id, file_path))
    _analysis_tasks.add(task)
    task.add_done_callback(_analysis_tasks.discard)
    return {"message": "已重新加入分析队列", "inspiration_id": inspiration_id}


@router.post("/retry-all-failed")
async def retry_all_failed(db: AsyncSession = Depends(get_db)) -> dict[str, str | int]:
    """一键重试所有失败的分析（仅取每个素材最新记录为失败的）。"""
    failed = await ai_svc.get_failed_analysis_targets(db)

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
) -> dict[str, int]:
    """批量删除分析历史记录。

    请求体: {"ids": [1, 2, 3]}
    """
    ids = payload.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="请提供要删除的记录 ID 列表")
    deleted = await ai_svc.delete_analysis_logs_batch(db, ids)
    return {"deleted": deleted}


@router.post("/history/batch-retry")
async def batch_retry_logs(
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    """批量重试分析记录：根据日志 ID 找到对应素材并重新分析。

    请求体: {"ids": [1, 2, 3]}
    """
    ids = payload.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="请提供要重试的记录 ID 列表")
    rows = await ai_svc.get_batch_retry_targets(db, ids)
    count = 0
    for insp_id, file_path in rows:
        task = asyncio.create_task(_run_analysis(insp_id, file_path))
        _analysis_tasks.add(task)
        task.add_done_callback(_analysis_tasks.discard)
        count += 1
    return {"message": f"已将 {count} 个素材加入分析队列", "count": count}


@router.get("/history/model-names")
async def get_history_model_names(db: AsyncSession = Depends(get_db)) -> dict[str, list[str]]:
    """获取分析历史中出现过的所有模型名称，供前端筛选（排除质量审核日志）。"""
    names = await ai_svc.get_history_model_names(db)
    return {"models": names}


@router.delete("/history/failed/all")
async def delete_all_failed_logs(db: AsyncSession = Depends(get_db)) -> dict[str, str | int]:
    """批量删除所有失败的分析日志。"""
    count = await ai_svc.delete_failed_logs(db)

    if count == 0:
        return {"message": "没有失败的记录", "count": 0}

    logger.info(f"已批量删除 {count} 条失败的 AI 分析记录")
    return {"message": f"已删除 {count} 条失败记录", "count": count}


@router.get("/history/{log_id}")
async def get_analysis_detail(
    log_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取单条分析日志的详细信息，包含原始 AI 响应和关联标签。"""
    result = await ai_svc.get_analysis_detail(db, log_id)
    if not result:
        raise HTTPException(status_code=404, detail="分析记录未找到")
    return result


@router.delete("/history/{log_id}")
async def delete_analysis_log(
    log_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """删除指定分析日志。"""
    if not await ai_svc.delete_analysis_log(db, log_id):
        raise HTTPException(status_code=404, detail="分析记录未找到")
    return {"message": f"分析记录 #{log_id} 已删除"}


@router.get("/queue/pending")
async def get_pending_queue(db: AsyncSession = Depends(get_db)) -> dict[str, list[dict] | bool]:
    """获取排队中素材的缩略图预览信息。"""
    if not _pending_queue and not _active_analyses:
        return {"items": [], "paused": get_queue_paused()}

    # 所有活跃/排队中的素材 ID
    all_ids = list(_active_analyses.keys())
    if not all_ids:
        return {"items": [], "paused": get_queue_paused()}

    insp_map = await ai_svc.get_inspiration_preview_map(db, all_ids)

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
async def cancel_queue_item(inspiration_id: str) -> dict[str, str]:
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
async def pause_queue() -> dict[str, str | bool]:
    """暂停全局分析队列（已完成的不受影响）。"""
    set_queue_paused(True)
    logger.info("分析队列已暂停")
    return {"message": "队列已暂停", "paused": True}


@router.post("/queue/resume")
async def resume_queue() -> dict[str, str | bool]:
    """恢复全局分析队列。"""
    set_queue_paused(False)
    logger.info("分析队列已恢复")
    return {"message": "队列已恢复", "paused": False}


@router.get("/active-analyses")
async def get_active_analyses() -> dict[str, dict[str, str] | int]:
    """获取当前正在分析中的素材列表，用于前端轮询显示进度。"""
    return {"active_analyses": _active_analyses, "count": len(_active_analyses)}


# ============ 分析结果对比 ============


@router.get("/compare/{inspiration_id}")
async def compare_analyses(
    inspiration_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取同一素材的所有历史分析结果，用于并排对比。

    返回：
    - analyses: 每次分析的详情列表（按时间排序）
    - tag_diff: 各次分析间的标签差异（新增/消失/共同）
    - time_comparison: 耗时对比数据
    """
    try:
        return await ai_svc.get_analysis_comparison(db, inspiration_id)
    except ai_svc.AIAnalysisNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
