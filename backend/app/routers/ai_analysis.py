"""AI 子路由。"""

import asyncio
import csv
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.inspiration import Inspiration
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
from app.services.model_prompt import get_prompt_versions
from app.services.ollama_utils import is_ollama_running, start_ollama
from app.services.task_runner import (
    MAX_MULTI_COMBINATIONS,
    create_batch_analyze_task,
    create_multi_analyze_task,
)
from app.utils.csv_safety import sanitize_csv_cell

logger = logging.getLogger(__name__)
router = APIRouter()


class BatchAnalyzeRequest(BaseModel):
    """批量分析请求体（兼容旧版纯数组与多模型组合分析对象两种格式）。"""

    inspiration_ids: list[str]
    models: list[str] | None = None  # 视觉模型名列表（多选）；缺省用全局默认模型
    prompt_ids: list[int] | None = None  # 提示词版本 ID 列表（0 = 当前默认提示词）
    apply_tags: bool = True  # 是否把标签合并到素材（组合分析默认 False）


async def _check_ollama_before_analysis() -> tuple[bool, str | None]:
    """在提交分析任务前检查 Ollama 运行状态，未运行则尝试自动启动。

    返回:
        (is_running, message):
        - is_running=True  表示 Ollama 已在运行，message 为 None
        - is_running=False 表示 Ollama 未运行且尝试启动，message 为提示信息
    """
    if await is_ollama_running():
        return True, None

    start_msg = await start_ollama()
    # 无论成功与否都返回 is_running=False，让前端提示用户
    return False, start_msg or "Ollama 未运行，请确认后重试"


# ============ AI 分析 ============


@router.post("/analyze/{inspiration_id}")
async def analyze_inspiration(
    inspiration_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """触发单个素材的 AI 分析（后台异步执行）。"""
    # 先校验素材是否存在（无论 Ollama 状态），不存在的素材直接 404
    result = await db.execute(
        select(Inspiration).where(Inspiration.id == inspiration_id)
    )
    inspiration = result.scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    if inspiration.media_type not in ("image", "video"):
        raise HTTPException(status_code=400, detail="仅支持分析图片/视频素材")

    ollama_running, ollama_msg = await _check_ollama_before_analysis()
    if not ollama_running:
        return {
            "message": ollama_msg,
            "inspiration_id": inspiration_id,
            "status": "analyzing",
            "ollama_will_start": True,
        }

    # 视频素材传 None：由后台任务懒解析第一关键帧（ffmpeg 提取耗时，不阻塞请求）
    file_path = inspiration.file_path if inspiration.media_type == "image" else None

    task = asyncio.create_task(_run_analysis(inspiration_id, file_path))
    _analysis_tasks.add(task)
    task.add_done_callback(_analysis_tasks.discard)
    return {
        "message": "分析任务已加入队列",
        "inspiration_id": inspiration_id,
        "status": "analyzing",
        "ollama_will_start": False,
    }


def _resolve_prompt_combinations(
    models: list[str] | None, prompt_ids: list[int] | None
) -> list[dict]:
    """解析「模型 × 提示词」组合列表（创建任务时把 Prompt 文本固化到载荷）。

    参数:
        models: 模型名列表；空/None 回退到全局默认视觉模型
        prompt_ids: 提示词版本 ID 列表；0 = 当前默认提示词（执行时按模型解析
            各模型自己的当前 Prompt），>=1 = 历史保存版本（文本立即固化）。
            空/None 视为仅「当前默认提示词」。

    返回:
        组合列表，每项 {"model", "prompt", "prompt_label", "prompt_id"}
    """
    # 模型去重（保持选择顺序）
    combo_models: list[str] = []
    for m in models or []:
        name = (m or "").strip()
        if name and name not in combo_models:
            combo_models.append(name)
    if not combo_models:
        combo_models = [settings.ollama_vision_model]

    # 解析提示词选项：prompt_id=0 表示「当前默认提示词」（prompt 存 None，
    # 执行时每个模型用自己的当前 Prompt，保证「多模型 × 默认提示词」语义正确）
    versions = get_prompt_versions(settings.ollama_vision_model)
    prompts: list[tuple[int, str | None, str]] = []  # (prompt_id, 文本, 展示标签)
    if prompt_ids:
        for pid in prompt_ids:
            if pid == 0:
                if (0, None, "当前默认提示词") not in prompts:
                    prompts.append((0, None, "当前默认提示词"))
            elif 1 <= pid <= len(versions):
                label = f"版本 #{pid}"
                saved_at = versions[pid - 1].get("saved_at") or ""
                if saved_at:
                    label += f"（{saved_at[:19].replace('T', ' ')}）"
                if (pid, versions[pid - 1].get("prompt", ""), label) not in prompts:
                    prompts.append((pid, versions[pid - 1].get("prompt", ""), label))
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"无效的提示词版本 ID: {pid}（当前可选 0~{len(versions)}）",
                )
    else:
        prompts = [(0, None, "当前默认提示词")]

    return [
        {"model": model, "prompt": prompt, "prompt_label": label, "prompt_id": pid}
        for model in combo_models
        for pid, prompt, label in prompts
    ]


@router.post("/batch-analyze")
async def batch_analyze(
    payload: list[str] | BatchAnalyzeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    """批量触发多个素材的 AI 分析。

    两种请求格式：
    - 旧版兼容：纯 JSON 数组 `["id1", "id2"]`，行为与历史版本完全一致
      （单模型单提示词，自动合并标签）；
    - 多模型组合：对象 `{"inspiration_ids": [...], "models": [...],
      "prompt_ids": [...], "apply_tags": false}`，按「模型 × 提示词」全部
      组合逐个分析，每个组合独立写入分析日志与标签快照；
      apply_tags=false 时不修改素材的正式标签。

    已改造为「数据库驱动的任务队列」：创建任务记录后立即返回 task_id，
    由独立 worker 进程（app/worker.py）异步执行，前端通过轮询
    GET /api/tasks/{task_id} 获取进度。
    """
    # 解析请求体（兼容两种格式）
    if isinstance(payload, list):
        inspiration_ids = payload
        models: list[str] | None = None
        prompt_ids: list[int] | None = None
        apply_tags = True
    else:
        inspiration_ids = payload.inspiration_ids
        models = payload.models
        prompt_ids = payload.prompt_ids
        apply_tags = payload.apply_tags

    # 是否为多模型 × 多提示词组合模式（任一多选参数出现，或显式关闭自动应用标签）
    is_multi = bool(models or prompt_ids or not apply_tags)

    # 先校验可分析的素材（无论 Ollama 状态）；视频素材由执行器解析关键帧后分析
    result = await db.execute(
        select(Inspiration).where(
            Inspiration.id.in_(inspiration_ids),
            Inspiration.media_type.in_(("image", "video")),
            Inspiration.deleted_at.is_(None),
        )
    )
    inspirations = result.scalars().all()
    valid_ids = [insp.id for insp in inspirations]
    skipped = len(inspiration_ids) - len(valid_ids)

    if not valid_ids:
        raise HTTPException(
            status_code=404,
            detail="未找到任何可分析的图片素材"
        )

    if is_multi:
        combinations = _resolve_prompt_combinations(models, prompt_ids)
        if len(combinations) > MAX_MULTI_COMBINATIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"组合数过多（{len(combinations)} 个），"
                    f"最多支持 {MAX_MULTI_COMBINATIONS} 个「模型 × 提示词」组合"
                ),
            )
        task = await create_multi_analyze_task(
            db, valid_ids, combinations, apply_tags=apply_tags, skipped=skipped
        )
    else:
        # 单模型 × 单提示词，保持旧任务类型与旧行为（自动合并标签）
        task = await create_batch_analyze_task(db, valid_ids, skipped)

    # Ollama 检查：仅用于消息提示，不影响已创建的任务
    ollama_running, ollama_msg = await _check_ollama_before_analysis()
    prefix = f"{ollama_msg}，" if not ollama_running else ""
    # 与旧版兼容：Ollama 未运行时前端提示用户，由用户稍后手动重试
    ollama_will_start = None if ollama_running else True

    if is_multi:
        resp = {
            "task_id": task.id,
            "message": (
                f"{prefix}已创建组合分析任务 #{task.id}：{len(valid_ids)} 个素材 × "
                f"{len(combinations)} 个组合，共 {len(valid_ids) * len(combinations)} 次分析"
                + (f"，跳过 {skipped} 个素材（不存在或类型不支持）" if skipped > 0 else "")
            ),
            "count": len(valid_ids),
            "skipped": skipped,
            "combinations": len(combinations),
            "apply_tags": apply_tags,
            "status": "pending",
        }
    else:
        resp = {
            "task_id": task.id,
            "message": f"{prefix}已创建批量分析任务 #{task.id}，共 {len(valid_ids)} 个素材"
                       + (f"，跳过 {skipped} 个素材（不存在或类型不支持）" if skipped > 0 else ""),
            "count": len(valid_ids),
            "skipped": skipped,
            "status": "pending",
        }
    if ollama_will_start:
        resp["ollama_will_start"] = True
    return resp


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
    prompt_version: str | None = None,  # 按提示词版本（内容哈希）筛选
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
            start_date, end_date, sort_by, prompt_version,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"时间参数格式错误: {e}")


@router.get("/history/export")
async def export_analysis_history_csv(
    status: str | None = None,
    model_name: str | None = None,
    prompt_version: str | None = None,
    inspiration_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sort_by: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """导出分析历史为 CSV（当前筛选条件，上限 10000 条）。"""
    try:
        items = await ai_svc.export_analysis_history(
            db, status, model_name, inspiration_id, start_date, end_date, sort_by,
            prompt_version,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"时间参数格式错误: {e}")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "素材ID", "模型", "提示词版本", "状态", "耗时(ms)", "失败原因", "时间", "标签"])
    for it in items:
        row = [
            it["id"],
            it["inspiration_id"],
            it["model_name"],
            it.get("prompt_version") or "",
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
) -> dict:
    """重试失败的分析。"""
    # 先校验素材是否存在
    result = await db.execute(
        select(Inspiration).where(Inspiration.id == inspiration_id)
    )
    inspiration = result.scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    if inspiration.media_type not in ("image", "video"):
        raise HTTPException(status_code=400, detail="暂不支持分析该素材类型")

    ollama_running, ollama_msg = await _check_ollama_before_analysis()
    if not ollama_running:
        return {
            "message": ollama_msg,
            "inspiration_id": inspiration_id,
            "ollama_will_start": True,
        }

    # 视频素材传 None：由后台任务懒解析第一关键帧（ffmpeg 提取耗时，不阻塞请求）
    file_path = inspiration.file_path if inspiration.media_type == "image" else None

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

    ollama_running, ollama_msg = await _check_ollama_before_analysis()
    if not ollama_running:
        return {
            "message": f"{ollama_msg}，请稍后 Ollama 启动后再次重试",
            "count": 0,
            "ollama_will_start": True,
        }

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

    if not rows:
        return {"message": "没有可重试的素材", "count": 0}

    ollama_running, ollama_msg = await _check_ollama_before_analysis()
    if not ollama_running:
        return {
            "message": f"{ollama_msg}，请稍后 Ollama 启动后再次重试",
            "count": 0,
            "ollama_will_start": True,
        }

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


@router.get("/history/prompt-versions")
async def get_history_prompt_versions(db: AsyncSession = Depends(get_db)) -> dict:
    """获取分析历史中出现过的所有提示词版本（内容哈希）及记录数，供前端筛选。"""
    versions = await ai_svc.get_history_prompt_versions(db)
    return {"versions": versions}


@router.get("/prompt-options")
async def get_prompt_options() -> dict:
    """获取组合分析可选的提示词列表（当前默认 + 历史保存版本）。

    返回的 ``id`` 供 POST /ai/batch-analyze 的 ``prompt_ids`` 使用：
    0 = 当前默认提示词（执行时按各模型自己的当前 Prompt），
    >=1 = 历史保存版本（对应 AI 设置页「保存版本」的版本号）。
    """
    model = settings.ollama_vision_model
    versions = get_prompt_versions(model)
    options = [{"id": 0, "label": "当前默认提示词", "source": "current"}]
    for idx, v in enumerate(versions):
        saved_at = str(v.get("saved_at") or "")[:19].replace("T", " ")
        options.append({
            "id": idx + 1,
            "label": f"版本 #{idx + 1}" + (f"（{saved_at}）" if saved_at else ""),
            "source": "version",
            "saved_at": v.get("saved_at"),
            "length": v.get("length"),
        })
    return {"model": model, "options": options}


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


@router.post("/compare-batch")
async def compare_analyses_batch(
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """按日志 ID 批量对比多条分析记录（需为同一素材，至少 2 条）。

    请求体: {"log_ids": [1, 2, 3]}

    返回：
    - analyses: 每条记录的详情（模型、提示词版本、状态、耗时、标签快照含置信度）
    - tag_diff: common（全部成功记录共有的标签）+ differing（差异标签及出现的记录）
    - time_comparison: 耗时对比数据
    """
    log_ids = payload.get("log_ids", [])
    if not isinstance(log_ids, list) or not log_ids:
        raise HTTPException(status_code=400, detail="请提供要对比的记录 ID 列表")
    if not all(isinstance(x, int) for x in log_ids):
        raise HTTPException(status_code=400, detail="记录 ID 必须为整数")
    try:
        return await ai_svc.compare_logs_batch(db, log_ids)
    except ai_svc.AIAnalysisNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/history/{log_id}/apply")
async def apply_analysis_log_to_material(
    log_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """把某条分析记录提取的标签应用到素材（覆盖 AI 标签，保留手动标签）。

    应用后素材的 AI 标签（source=ai_generated）被替换为该次分析的结果，
    手动标签不受影响；素材标签保持唯一。
    """
    try:
        result = await ai_svc.apply_analysis_to_material(db, log_id)
    except ai_svc.AIAnalysisNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "message": f"已把 {result['applied']} 个标签应用到素材",
        **result,
    }
