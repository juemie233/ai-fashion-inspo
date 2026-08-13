"""AI 分析与模型管理的 REST API 路由。"""

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
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
from app.services.model_config import get_model_config, update_model_config
from app.utils.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])

# 分析任务并发控制：最多同时分析 2 个素材，避免显存溢出
_analysis_semaphore = asyncio.Semaphore(2)
# 正在分析中的 inspiration_id 集合，用于前端轮询
_active_analyses: dict[str, str] = {}  # inspiration_id -> 状态描述
# 保留任务引用，防止 GC 回收
_analysis_tasks: set[asyncio.Task] = set()
# 任务 ID → Task 映射，用于取消单个任务
_task_by_id: dict[str, asyncio.Task] = {}
# 排队中的任务 ID 列表（尚未获取信号量的）
_pending_queue: list[str] = []
# 队列暂停开关
_queue_paused = False
# 质量审核任务追踪（与完整分析队列共享 _analysis_semaphore 信号量）
_quality_active: set[str] = set()  # 正在审核的 inspiration_id

# ============ 模型管理 ============


@router.get("/status")
async def ai_status():
    """检查 AI 模型的可用性和状态。"""
    try:
        from app.services.ai_service import check_ollama_status
        return await check_ollama_status()
    except ImportError:
        return {
            "status": "not_configured",
            "message": "AI 服务尚未配置。请安装 Ollama 并拉取视觉模型。",
            "ollama_url": settings.ollama_base_url,
        }


@router.get("/models")
async def list_models():
    """列出所有已安装的 Ollama 模型，含大小和修改时间。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            resp.raise_for_status()
            models = resp.json().get("models", [])

        # 获取当前活跃模型
        active = settings.ollama_vision_model

        # 尝试获取 GPU 信息
        gpu_info = {}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                ps_resp = await client.get(f"{settings.ollama_base_url}/api/ps")
                if ps_resp.status_code == 200:
                    ps_data = ps_resp.json()
                    for m in ps_data.get("models", []):
                        gpu_info[m["name"]] = {
                            "vram_used": m.get("size_vram", 0),
                            "loaded": True,
                        }
        except Exception:
            pass

        result = []
        for m in models:
            name = m["name"]
            result.append({
                "name": name,
                "size_bytes": m.get("size", 0),
                "size_display": _format_size(m.get("size", 0)),
                "modified": m.get("modified_at", ""),
                "is_active": name == active,
                "vram_used": gpu_info.get(name, {}).get("vram_used", 0),
                "loaded": gpu_info.get(name, {}).get("loaded", False),
            })

        return {"models": result, "active_model": active}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"无法连接 Ollama: {e}")


@router.post("/models/pull")
async def pull_model(
    model_name: str = Query(..., description="要下载的模型名称，如 gemma3:4b"),
):
    """拉取新模型（SSE 流式返回下载进度）。"""
    logger.info(f"开始拉取模型: {model_name}")

    async def event_stream():
        try:
            async with httpx.AsyncClient(timeout=3600) as client:
                async with client.stream(
                    "POST",
                    f"{settings.ollama_base_url}/api/pull",
                    json={"name": model_name, "stream": True},
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            try:
                                chunk = json.loads(line)
                                if "error" in chunk:
                                    yield f"data: {json.dumps({'type': 'error', 'message': chunk['error']})}\n\n"
                                    return
                                if "status" in chunk:
                                    total = chunk.get("total", 0)
                                    completed = chunk.get("completed", 0)
                                    yield f"data: {json.dumps({'type': 'progress', 'status': chunk['status'], 'total': total, 'completed': completed})}\n\n"
                                if chunk.get("status") == "success":
                                    yield f"data: {json.dumps({'type': 'done', 'message': '下载完成'})}\n\n"
                                    return
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.delete("/models/{model_name:path}")
async def delete_model(model_name: str):
    """删除指定模型。"""
    # 防误删：不允许删除当前活跃模型
    if model_name == settings.ollama_vision_model:
        raise HTTPException(status_code=400, detail="不能删除当前正在使用的模型，请先切换到其他模型")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{settings.ollama_base_url}/api/delete",
                json={"name": model_name},
            )
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"模型 '{model_name}' 不存在")
            resp.raise_for_status()
        return {"message": f"模型 '{model_name}' 已删除"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")


@router.put("/models/active")
async def set_active_model(model_name: str = Query(...)):
    """切换活跃模型。"""
    # 验证模型存在
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            models = resp.json().get("models", [])
            names = [m["name"] for m in models]
            if model_name not in names:
                raise HTTPException(status_code=404, detail=f"模型 '{model_name}' 未安装")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"无法连接 Ollama: {e}")

    # 更新内存配置，并持久化到 .env，重启后仍生效
    settings.ollama_vision_model = model_name
    await _update_env_file({"OLLAMA_VISION_MODEL": model_name})
    return {"message": f"已切换到模型 '{model_name}'", "active_model": model_name}


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
    """批量触发多个素材的 AI 分析。"""
    result = await db.execute(
        select(Inspiration).where(
            Inspiration.id.in_(inspiration_ids),
            Inspiration.media_type == "image",
        )
    )
    inspirations = result.scalars().all()

    if not inspirations:
        raise HTTPException(status_code=404, detail="未找到任何可分析的图片素材")

    skipped = len(inspiration_ids) - len(inspirations)
    for insp in inspirations:
        task = asyncio.create_task(_run_analysis(insp.id, insp.file_path))
        _analysis_tasks.add(task)
        task.add_done_callback(_analysis_tasks.discard)

    return {
        "message": f"已将 {len(inspirations)} 个素材加入分析队列"
                   + (f"，跳过 {skipped} 个素材（不存在或非图片）" if skipped > 0 else ""),
        "count": len(inspirations),
        "skipped": skipped,
    }


# ============ GPU 显存监控 ============


@router.get("/gpu-stats")
async def gpu_stats():
    """获取 GPU 显存占用和已加载模型信息。

    数据来源：
    - Ollama /api/ps（正在运行中的模型及其显存占用）
    - nvidia-smi（物理 GPU 总显存，可选，Windows/Linux 均支持）
    """
    result: dict = {
        "gpu_available": False,
        "gpu_name": "",
        "total_vram_mb": 0,
        "used_vram_mb": 0,
        "free_vram_mb": 0,
        "usage_percent": 0,
        "loaded_models": [],
    }

    # 1. 从 Ollama /api/ps 获取已加载模型和显存信息
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            ps_resp = await client.get(f"{settings.ollama_base_url}/api/ps")
            if ps_resp.status_code == 200:
                ps_data = ps_resp.json()
                for m in ps_data.get("models", []):
                    vram_bytes = m.get("size_vram", 0)
                    result["loaded_models"].append({
                        "name": m["name"],
                        "vram_mb": round(vram_bytes / 1024 / 1024, 1),
                        "loaded_at": m.get("expires_at", None),
                    })
                    result["used_vram_mb"] += round(vram_bytes / 1024 / 1024, 1)
    except Exception as e:
        logger.debug(f"Ollama /api/ps 查询失败: {e}")

    # 2. 尝试 nvidia-smi 获取物理 GPU 总显存（比 Ollama 更准确，优先使用）
    try:
        import subprocess
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,memory.free",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise Exception("nvidia-smi 查询超时")
        if proc.returncode == 0 and stdout:
            line = stdout.decode().strip().split("\n")[0]  # 取第一张 GPU
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                result["gpu_available"] = True
                result["gpu_name"] = parts[0]
                # nvidia-smi 返回的已经是 MB，始终使用物理 GPU 数据
                result["total_vram_mb"] = int(float(parts[1]))
                result["used_vram_mb"] = int(float(parts[2]))
                result["free_vram_mb"] = int(float(parts[3]))
    except FileNotFoundError:
        logger.debug("nvidia-smi 未安装或不在 PATH 中")
    except Exception as e:
        logger.debug(f"nvidia-smi 查询失败: {e}")

    # 如果有 Ollama 数据但没有 nvidia-smi，标记为有 GPU
    if not result["gpu_available"] and result["loaded_models"]:
        result["gpu_available"] = True

    # 计算使用百分比
    if result["total_vram_mb"] > 0:
        result["usage_percent"] = round(
            result["used_vram_mb"] / result["total_vram_mb"] * 100, 1
        )
    elif result["used_vram_mb"] > 0:
        result["usage_percent"] = -1  # 有使用但不知道总量

    return result


@router.post("/unload-model")
async def unload_model(model_name: str = Query(...)):
    """卸载指定模型释放显存（通知 Ollama 不再 keep alive）。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={"model": model_name, "keep_alive": 0, "prompt": ""},
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=502 if resp.status_code >= 500 else resp.status_code,
                    detail=f"卸载失败: Ollama 返回 {resp.status_code}",
                )
            logger.info(f"已发送卸载请求: {model_name}")
        return {"message": f"已发送卸载请求: {model_name}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"卸载失败: {e}")


# ============ 模型统计 ============


@router.get("/model-stats")
async def model_stats(db: AsyncSession = Depends(get_db)):
    """获取按模型聚合的分析统计：每个模型的分析次数、成功率、平均耗时、平均标签数。"""
    # 所有分析日志
    result = await db.execute(
        select(
            AIAnalysisLog.model_name,
            func.count().label("total"),
            func.sum(
                case((AIAnalysisLog.error.is_(None), 1), else_=0)
            ).label("successes"),
            func.avg(AIAnalysisLog.processing_time_ms).label("avg_time"),
            func.max(AIAnalysisLog.created_at).label("last_used"),
        )
        .group_by(AIAnalysisLog.model_name)
        .order_by(func.count().desc())
    )
    rows = result.all()

    # 获取每个模型的平均标签数（需要通过 inspiration_tags）
    from app.models.tag import InspirationTag
    models = []
    for row in rows:
        # 查询该模型成功分析的素材 ID
        insp_result = await db.execute(
            select(func.distinct(AIAnalysisLog.inspiration_id)).where(
                _analysis_log_filter(),
                AIAnalysisLog.model_name == row.model_name,
                AIAnalysisLog.error.is_(None),
            )
        )
        insp_ids = [r[0] for r in insp_result]

        avg_tags = 0
        if insp_ids:
            tag_count = await db.execute(
                select(func.count()).where(
                    InspirationTag.inspiration_id.in_(insp_ids)
                )
            )
            total_tags = tag_count.scalar() or 0
            avg_tags = round(total_tags / len(insp_ids), 1)

        models.append({
            "model_name": row.model_name,
            "total_analyses": row.total,
            "success_count": row.successes,
            "failure_count": row.total - row.successes,
            "success_rate": round(row.successes / row.total * 100, 1) if row.total > 0 else 0,
            "avg_time_ms": round(row.avg_time) if row.avg_time else 0,
            "avg_tags": avg_tags,
            "last_used": _fmt_utc(row.last_used),
        })

    # 全局汇总
    total_all = sum(m["total_analyses"] for m in models)
    total_success = sum(m["success_count"] for m in models)
    models.insert(0, {
        "model_name": "（全部模型汇总）",
        "total_analyses": total_all,
        "success_count": total_success,
        "failure_count": total_all - total_success,
        "success_rate": round(total_success / total_all * 100, 1) if total_all > 0 else 0,
        "avg_time_ms": round(
            sum(m["avg_time_ms"] * m["total_analyses"] for m in models) / total_all
        ) if total_all > 0 else 0,
        "avg_tags": round(
            sum(m["avg_tags"] * m["total_analyses"] for m in models) / total_all, 1
        ) if total_all > 0 else 0,
        "last_used": max((m["last_used"] for m in models if m["last_used"]), default=""),
    })

    return {"models": models, "total_analyses": total_all}


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
    """获取分析历史记录列表。"""
    query = select(AIAnalysisLog)
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
    if not ids:
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
    if not ids:
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
    """获取分析历史中出现过的所有模型名称，供前端筛选。"""
    result = await db.execute(
        select(AIAnalysisLog.model_name).distinct().order_by(AIAnalysisLog.model_name)
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
        from app.services.ai_service import _parse_analysis_response
        parsed = _parse_analysis_response(log.raw_response) or None
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
        return {"items": [], "paused": _queue_paused}

    # 所有活跃/排队中的素材 ID
    all_ids = list(_active_analyses.keys())
    if not all_ids:
        return {"items": [], "paused": _queue_paused}

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

    return {"items": items, "paused": _queue_paused}


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
    global _queue_paused
    _queue_paused = True
    logger.info("分析队列已暂停")
    return {"message": "队列已暂停", "paused": True}


@router.post("/queue/resume")
async def resume_queue():
    """恢复全局分析队列。"""
    global _queue_paused
    _queue_paused = False
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
    # 获取该素材的所有分析日志
    result = await db.execute(
        select(AIAnalysisLog)
        .where(AIAnalysisLog.inspiration_id == inspiration_id)
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
    from app.services.ai_service import _parse_analysis_response

    analyses = []
    for log in logs:
        parsed = _parse_analysis_response(log.raw_response) if log.raw_response else {}
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


# ============ Prompt 版本管理 ============

# prompt 版本历史文件路径
_prompt_versions_file = Path(__file__).parent.parent.parent / "prompt_versions.json"


def _load_prompt_versions() -> list[dict]:
    """加载 prompt 版本历史。"""
    if _prompt_versions_file.exists():
        try:
            return json.loads(_prompt_versions_file.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_prompt_versions(versions: list[dict]):
    """保存 prompt 版本历史（保留最近 50 条）。"""
    _prompt_versions_file.write_text(
        json.dumps(versions[-50:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@router.get("/prompt/versions")
async def prompt_versions():
    """获取 prompt 版本历史列表。"""
    versions = _load_prompt_versions()
    return {
        "versions": versions[::-1],  # 最新的在前
        "current": settings.ai_analysis_prompt[:100] + "...",
    }


@router.post("/prompt/rollback")
async def rollback_prompt(payload: dict):
    """回滚 prompt 到指定版本。请求体: {"index": 0} 其中 index 0 = 最新版本（与 GET /versions 顺序一致）"""
    versions = _load_prompt_versions()  # 按时间正序存储
    idx = payload.get("index", 0)
    # 前端发送的 index：0 = 最新 = versions 最后一个
    real_idx = len(versions) - 1 - idx
    if real_idx < 0 or real_idx >= len(versions):
        raise HTTPException(status_code=400, detail="无效的版本索引")
    prompt_text = versions[real_idx]["prompt"]
    settings.ai_analysis_prompt = prompt_text
    return {
        "message": f"已回滚到版本 #{idx + 1}",
        "prompt": prompt_text,
    }


@router.post("/prompt/save-version")
async def save_prompt_version():
    """将当前 prompt 保存为一个命名版本（用于后续回滚和对比）。"""
    versions = _load_prompt_versions()
    from datetime import datetime
    versions.append({
        "prompt": settings.ai_analysis_prompt,
        "saved_at": datetime.now().isoformat(),
        "length": len(settings.ai_analysis_prompt),
    })
    _save_prompt_versions(versions)
    return {"message": f"已保存版本 #{len(versions)}", "total_versions": len(versions)}


# ============ Prompt 管理 ============


@router.get("/prompt")
async def get_prompt():
    """获取当前 AI 分析使用的 prompt 文本。"""
    return {
        "prompt": settings.ai_analysis_prompt,
        "length": len(settings.ai_analysis_prompt),
    }


@router.put("/prompt")
async def update_prompt(
    body: dict,
):
    """更新 AI 分析 prompt（可选持久化到 backend/prompt.txt）。"""
    prompt = body.get("prompt", "")
    persist = body.get("persist", False)
    if not prompt:
        raise HTTPException(status_code=400, detail="请提供 prompt 文本")
    settings.ai_analysis_prompt = prompt

    if persist:
        try:
            prompt_file = Path(__file__).parent.parent.parent / "prompt.txt"

            def _write_prompt():
                prompt_file.write_text(prompt, encoding="utf-8")

            await asyncio.to_thread(_write_prompt)
            logger.info(f"Prompt 已持久化到 {prompt_file}")
        except Exception as e:
            logger.warning(f"持久化 prompt 失败: {e}")

    return {
        "message": "Prompt 已更新" + ("并持久化" if persist else "") + f"（{len(prompt)} 字符）",
    }


# ============ 分析质量仪表盘 ============


@router.get("/quality-dashboard")
async def quality_dashboard(db: AsyncSession = Depends(get_db)):
    """分析质量总览：每日趋势、问题素材、标签覆盖率。"""
    from datetime import datetime, timedelta

    # 最近 30 天的每日分析统计
    thirty_days_ago = datetime.now() - timedelta(days=30)
    daily_result = await db.execute(
        select(
            func.date(AIAnalysisLog.created_at).label("day"),
            func.count().label("total"),
            func.sum(case((AIAnalysisLog.error.is_(None), 1), else_=0)).label("success"),
        )
        .where(_analysis_log_filter(), AIAnalysisLog.created_at >= thirty_days_ago)
        .group_by("day")
        .order_by("day")
    )
    daily = [
        {"day": row[0], "total": row[1], "success": row[2] or 0}
        for row in daily_result.all()
    ]

    # 问题素材统计
    total_insp = (await db.execute(select(func.count(Inspiration.id)))).scalar() or 0
    analyzed_ids = (
        select(AIAnalysisLog.inspiration_id)
        .where(_analysis_log_filter())
        .distinct()
    )
    analyzed_count = (await db.execute(
        select(func.count()).select_from(analyzed_ids.subquery())
    )).scalar() or 0

    # 多次失败的素材（≥3 次失败）
    fail_count_sub = (
        select(AIAnalysisLog.inspiration_id, func.count().label("fc"))
        .where(_analysis_log_filter(), AIAnalysisLog.error.isnot(None))
        .group_by(AIAnalysisLog.inspiration_id)
        .having(func.count() >= 3)
        .subquery()
    )
    multi_fail = (await db.execute(select(func.count()).select_from(fail_count_sub))).scalar() or 0

    # 零标签输出（有分析记录但没有关联任何标签的素材）
    from app.models.tag import InspirationTag as IT
    zero_tag_result = await db.execute(
        select(func.count())
        .select_from(AIAnalysisLog)
        .where(
            _analysis_log_filter(),
            AIAnalysisLog.error.is_(None),
            ~AIAnalysisLog.inspiration_id.in_(
                select(IT.inspiration_id).distinct()
            ),
        )
    )
    zero_tag_count = zero_tag_result.scalar() or 0

    # 平均标签数（单次 SQL 聚合）
    avg_tags = 0
    if analyzed_count > 0:
        tag_total = (await db.execute(
            select(func.count()).select_from(IT)
        )).scalar() or 0
        avg_tags = round(tag_total / analyzed_count, 1)

    # 平均耗时
    avg_time = (await db.execute(
        select(func.avg(AIAnalysisLog.processing_time_ms))
        .where(_analysis_log_filter(), AIAnalysisLog.error.is_(None))
    )).scalar() or 0

    return {
        "daily_trends": daily,
        "overview": {
            "total_inspirations": total_insp,
            "analyzed_count": analyzed_count,
            "unanalyzed_count": max(0, total_insp - analyzed_count),
            "coverage_percent": round(analyzed_count / total_insp * 100, 1) if total_insp > 0 else 0,
            "avg_tags_per_image": avg_tags,
            "avg_time_ms": round(avg_time),
        },
        "problem_items": {
            "multi_fail_count": multi_fail,
            "zero_tag_count": zero_tag_count,
        },
    }


# ============ 单图测试 ============


@router.post("/test-analyze")
async def test_analyze(
    inspiration_id: str | None = Query(None, description="使用已有素材 ID 测试"),
    custom_prompt: str | None = Query(None, description="临时覆盖 prompt（可选）"),
):
    """单图即时测试：使用当前模型和参数分析图片，SSE 流式返回结果。

    不保存分析记录到数据库，仅用于测试 prompt/参数效果。
    """
    import base64 as b64

    # 获取图片路径
    if inspiration_id:
        async with async_session() as db:
            insp = await db.get(Inspiration, inspiration_id)
            if not insp:
                raise HTTPException(404, "素材未找到")
            if insp.media_type != "image":
                raise HTTPException(400, "暂不支持分析视频文件")
            file_path = insp.file_path
    else:
        raise HTTPException(400, "请指定 inspiration_id")

    full_path = (settings.storage_root / file_path).resolve()
    if not str(full_path).startswith(str(settings.storage_root.resolve())):
        raise HTTPException(400, "非法的文件路径")
    if not full_path.exists():
        raise HTTPException(404, "图片文件不存在")

    # 读取图片
    image_bytes = full_path.read_bytes()
    ext = full_path.suffix.lower()

    # WebP 转换
    if ext == ".webp":
        try:
            from io import BytesIO
            from PIL import Image
            buf = BytesIO()
            Image.open(BytesIO(image_bytes)).convert("RGB").save(buf, "JPEG", quality=95)
            image_bytes = buf.getvalue()
        except Exception as e:
            raise HTTPException(400, f"WebP 转换失败: {e}")

    image_data = b64.b64encode(image_bytes).decode("utf-8")
    prompt = custom_prompt or settings.ai_analysis_prompt

    async def event_stream():
        import time as _time
        started = _time.time()

        try:
            async with httpx.AsyncClient(timeout=settings.ai_analysis_timeout) as client:
                try:
                    response = await client.post(
                        f"{settings.ollama_base_url}/api/chat",
                        json={
                            "model": settings.ollama_vision_model,
                            "messages": [
                                {"role": "user", "content": prompt, "images": [image_data]}
                            ],
                            "stream": False,
                            "options": {
                                "temperature": getattr(settings, "ai_temperature", 0.7),
                                "top_p": getattr(settings, "ai_top_p", 0.9),
                                "top_k": getattr(settings, "ai_top_k", 40),
                                "num_predict": getattr(settings, "ai_num_predict", 2048),
                            },
                        },
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    detail = ""
                    try:
                        detail = e.response.json().get("error", "")
                    except Exception:
                        pass
                    yield f"data: {json.dumps({'type': 'error', 'message': f'HTTP {e.response.status_code}: {detail or str(e)}'})}\n\n"
                    return
                except httpx.TimeoutException:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'AI 响应超时'})}\n\n"
                    return
                except httpx.ConnectError:
                    yield f"data: {json.dumps({'type': 'error', 'message': '无法连接 Ollama，请确认 Ollama 已启动'})}\n\n"
                    return

            result = response.json()
            if not isinstance(result, dict) or "message" not in result:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Ollama 返回格式异常'})}\n\n"
                return

            raw = result["message"].get("content", "")
            elapsed_ms = int((_time.time() - started) * 1000)

            # 解析响应
            from app.services.ai_service import _parse_analysis_response
            parsed = _parse_analysis_response(raw) if raw else {}

            # 流式输出解析结果
            yield f"data: {json.dumps({'type': 'done', 'raw_response': raw, 'parsed': parsed, 'model': settings.ollama_vision_model, 'elapsed_ms': elapsed_ms})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ============ 参数调优 ============


@router.get("/settings")
async def get_ai_settings():
    """获取当前 AI 参数配置（超时按当前模型独立）。"""
    model_cfg = get_model_config(settings.ollama_vision_model)
    return {
        "active_model": settings.ollama_vision_model,
        "confidence_threshold": settings.ai_low_confidence_threshold,
        "analysis_timeout": model_cfg["timeout"],
        "ollama_base_url": settings.ollama_base_url,
    }


@router.put("/settings")
async def update_ai_settings(
    confidence_threshold: float | None = Query(None, ge=0, le=1),
    analysis_timeout: int | None = Query(None, ge=10, le=300),
    persist: bool = Query(False, description="是否持久化写入 .env 文件"),
):
    """更新 AI 参数。

    超时按当前活跃模型独立保存到 model_configs.json；置信度阈值为全局设置。
    ``persist`` 参数已废弃（模型配置始终持久化），保留仅为兼容前端。
    """
    if confidence_threshold is not None:
        settings.ai_low_confidence_threshold = confidence_threshold
        await _update_env_file({"AI_LOW_CONFIDENCE_THRESHOLD": str(confidence_threshold)})

    timeout = get_model_config(settings.ollama_vision_model)["timeout"]
    if analysis_timeout is not None:
        cfg = await update_model_config(
            settings.ollama_vision_model, {"timeout": analysis_timeout}
        )
        timeout = cfg["timeout"]

    return {
        "message": "参数已更新（超时按模型持久化）",
        "confidence_threshold": settings.ai_low_confidence_threshold,
        "analysis_timeout": timeout,
    }


@router.get("/sampling-params")
async def get_sampling_params():
    """获取当前模型的 AI 采样参数（temperature, top_p, top_k, num_predict, think）。"""
    cfg = get_model_config(settings.ollama_vision_model)
    return {
        "temperature": cfg["temperature"],
        "top_p": cfg["top_p"],
        "top_k": cfg["top_k"],
        "num_predict": cfg["num_predict"],
        "think": cfg["think"],
    }


@router.put("/sampling-params")
async def update_sampling_params(
    temperature: float | None = Query(None, ge=0, le=2),
    top_p: float | None = Query(None, ge=0, le=1),
    top_k: int | None = Query(None, ge=1, le=100),
    num_predict: int | None = Query(None, ge=64, le=8192),
    think: bool | None = Query(None, description="是否开启思考模式（思考模型适用）"),
    persist: bool = Query(False),
):
    """更新当前模型的 AI 采样参数（按模型独立持久化）。"""
    updates = {}
    if temperature is not None:
        updates["temperature"] = temperature
    if top_p is not None:
        updates["top_p"] = top_p
    if top_k is not None:
        updates["top_k"] = top_k
    if num_predict is not None:
        updates["num_predict"] = num_predict
    if think is not None:
        updates["think"] = think

    cfg = get_model_config(settings.ollama_vision_model)
    if updates:
        cfg = await update_model_config(settings.ollama_vision_model, updates)

    return {
        "message": "采样参数已更新（按模型持久化）",
        "temperature": cfg["temperature"],
        "top_p": cfg["top_p"],
        "top_k": cfg["top_k"],
        "num_predict": cfg["num_predict"],
        "think": cfg["think"],
    }


# ============ 数据重置 ============


@router.delete("/reset")
async def reset_all_data(
    confirm: str = Query("no", description="输入 'yes' 二次确认删除所有数据"),
    _api_key: str = Depends(require_api_key),
):
    """重置所有数据：清空数据库所有表 + 删除存储文件。

    危险操作，需 query 参数 confirm=yes 才执行。
    """
    if confirm != "yes":
        raise HTTPException(
            status_code=400,
            detail="需要 confirm=yes 确认。此操作将删除所有素材、标签、分析记录和照片文件！",
        )

    import asyncio as aio
    import shutil
    from app.models.tag import InspirationTag, Tag
    from app.models.scraper import ScraperTask

    # 取消所有进行中的分析任务，避免删除数据后任务写回脏数据
    if _analysis_tasks:
        logger.info(f"取消 {len(_analysis_tasks)} 个进行中的分析任务...")
        for t in list(_analysis_tasks):
            t.cancel()
        _active_analyses.clear()
        await aio.sleep(1)  # 给任务 1 秒处理取消

    async with async_session() as db:
        # 按外键依赖顺序删除（先删子表，再删主表）
        tables_in_order = [
            (InspirationTag, "inspiration_tags"),
            (AIAnalysisLog, "ai_analysis_log"),
            (ScraperTask, "scraper_tasks"),
            (Inspiration, "inspirations"),
            (Tag, "tags"),
        ]
        deleted_counts = {}
        for table_model, table_name in tables_in_order:
            result = await db.execute(delete(table_model))
            deleted_counts[table_name] = result.rowcount
        await db.commit()

    # 清空存储目录（threadpool 异步执行，避免阻塞）
    storage_deleted = 0
    storage_errors = []
    for dir_path in [settings.images_dir, settings.thumbnails_dir, settings.videos_dir]:
        if dir_path.exists():
            file_count = len(list(dir_path.iterdir()))

            def _rmtree(p=dir_path):
                shutil.rmtree(p)
                p.mkdir(parents=True)

            try:
                await aio.to_thread(_rmtree)
                storage_deleted += file_count
            except Exception as e:
                storage_errors.append(f"{dir_path.name}: {e}")

    result_msg = "所有数据已重置"
    if storage_errors:
        result_msg += f"（{len(storage_errors)} 个目录删除失败）"
        logger.warning(f"存储目录删除错误: {storage_errors}")

    logger.warning(
        f"⚠ 数据已全部重置！数据库: {deleted_counts}, 文件: {storage_deleted} 个"
    )
    return {
        "message": result_msg,
        "database": deleted_counts,
        "files_deleted": storage_deleted,
        "storage_errors": storage_errors if storage_errors else None,
    }


# ============ 工具函数 ============


async def _run_analysis(inspiration_id: str, file_path: str):
    """后台任务：对图片执行 AI 分析并保存标签（带并发控制 + 任务追踪）。"""
    if inspiration_id in _active_analyses:
        logger.info(f"素材已在分析队列中，跳过: {inspiration_id}")
        return

    # 注册当前任务
    current_task = asyncio.current_task()
    if current_task:
        _task_by_id[inspiration_id] = current_task

    # 加入排队
    _pending_queue.append(inspiration_id)
    _active_analyses[inspiration_id] = "排队中..."

    # 暂停检查放在信号量之前，避免消耗信号量槽位
    while _queue_paused:
        await asyncio.sleep(1)

    async with _analysis_semaphore:
        try:
            # 安全地从排队列表移除（可能已被取消端点移除）
            try:
                _pending_queue.remove(inspiration_id)
            except ValueError:
                pass
            _active_analyses[inspiration_id] = "正在分析..."
            from app.services.ai_service import analyze_image

            logger.info(f"开始 AI 分析: {inspiration_id}")
            async with async_session() as db:
                success = await analyze_image(db, inspiration_id, file_path)
                # 仅分析成功时删除该素材的旧失败日志
                if success:
                    old_logs = await db.execute(
                        select(AIAnalysisLog.id).where(
                            AIAnalysisLog.inspiration_id == inspiration_id,
                            AIAnalysisLog.error.isnot(None),
                        )
                    )
                    old_ids = [row[0] for row in old_logs]
                    if old_ids:
                        await db.execute(
                            delete(AIAnalysisLog).where(AIAnalysisLog.id.in_(old_ids))
                        )
                        await db.commit()
                        logger.info(f"清理了 {len(old_ids)} 条旧失败日志: {inspiration_id}")
            logger.info(f"AI 分析完成: {inspiration_id}")
        except asyncio.CancelledError:
            logger.info(f"分析任务被取消: {inspiration_id}")
            raise
        except ImportError:
            logger.warning("AI 服务尚未安装")
        except Exception as e:
            logger.error(f"分析失败 {inspiration_id}: {e}")
        finally:
            _active_analyses.pop(inspiration_id, None)
            _task_by_id.pop(inspiration_id, None)


async def _run_quality_check(inspiration_id: str, file_path: str):
    """后台任务：对图片执行轻量质量审核（是否真人穿搭照片）。"""
    if inspiration_id in _quality_active:
        return

    # 预处理：跳过已审核的（人工翻案或已审核），避免重复调用模型
    async with async_session() as db:
        insp = await db.get(Inspiration, inspiration_id)
        if not insp or insp.quality_status != "pending":
            return

    _quality_active.add(inspiration_id)
    try:
        # 与完整分析共享同一全局信号量，避免单卡同时 4 路推理
        async with _analysis_semaphore:
            from app.services.ai_service import check_image_quality
            async with async_session() as db:
                status, reason = await check_image_quality(db, inspiration_id, file_path)
                # 写入质量审核日志（失败时记录原因，供前端排查）
                db.add(AIAnalysisLog(
                    inspiration_id=inspiration_id,
                    model_name=settings.ollama_vision_model,
                    log_type="quality_check",
                    error=reason if status == "pending" else None,
                ))
                await db.commit()
                logger.info(f"质量审核 {inspiration_id}: {status}（{reason}）")
    except Exception as e:
        logger.error(f"质量审核失败 {inspiration_id}: {e}")
    finally:
        _quality_active.discard(inspiration_id)


@router.post("/quality-check")
async def batch_quality_check(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """批量审核所有待审核（pending）的图片素材，后台异步执行。

    只处理图片素材；审核结果直接写回 quality_status（approved/rejected）。
    """
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path)
        .where(
            Inspiration.quality_status == "pending",
            Inspiration.media_type == "image",
        )
        .limit(limit)
    )
    items = result.all()

    if not items:
        return {"message": "没有待审核的素材", "count": 0}

    for insp_id, file_path in items:
        task = asyncio.create_task(_run_quality_check(insp_id, file_path))
        _analysis_tasks.add(task)
        task.add_done_callback(_analysis_tasks.discard)

    return {
        "message": f"已提交 {len(items)} 个素材进行质量审核",
        "count": len(items),
    }


@router.post("/quality-recheck")
async def recheck_quality(db: AsyncSession = Depends(get_db)):
    """重新审核所有已通过（approved）的图片素材。

    将 approved 重置为 pending 后立即提交批量审核，用最新审核标准重新判定。
    用于修正审核标准升级后历史素材的误判（如「只有腿部」被误判为通过）。
    """
    result = await db.execute(
        update(Inspiration)
        .where(
            Inspiration.media_type == "image",
            Inspiration.quality_status == "approved",
        )
        .values(quality_status="pending", quality_reason=None)
    )
    await db.commit()
    reset_count = result.rowcount

    if not reset_count:
        return {"message": "没有已通过的素材可重新审核", "count": 0}

    # 提交所有待审核素材（含刚重置的），信号量保证单卡并发不超过 2
    items_result = await db.execute(
        select(Inspiration.id, Inspiration.file_path).where(
            Inspiration.quality_status == "pending",
            Inspiration.media_type == "image",
        )
    )
    items = items_result.all()

    for insp_id, file_path in items:
        task = asyncio.create_task(_run_quality_check(insp_id, file_path))
        _analysis_tasks.add(task)
        task.add_done_callback(_analysis_tasks.discard)

    return {
        "message": f"已重置 {reset_count} 个已通过素材，重新提交 {len(items)} 个待审核",
        "count": len(items),
    }


@router.get("/quality-stats")
async def quality_stats(db: AsyncSession = Depends(get_db)):
    """质量审核统计：待审核/已通过/已拒绝数量及通过率（仅图片素材）。"""
    result = await db.execute(
        select(
            func.coalesce(Inspiration.quality_status, "pending"),
            func.count(Inspiration.id),
        )
        .where(Inspiration.media_type == "image")
        .group_by(func.coalesce(Inspiration.quality_status, "pending"))
    )
    counts = {status: count for status, count in result.all()}

    pending = counts.get("pending", 0)
    approved = counts.get("approved", 0)
    rejected = counts.get("rejected", 0)
    total = pending + approved + rejected
    pass_rate = round(approved / (approved + rejected) * 100, 1) if (approved + rejected) > 0 else 0

    return {
        "total": total,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "pass_rate": pass_rate,
        "active": len(_quality_active),
    }


@router.get("/quality-active")
async def quality_active():
    """正在审核中的素材 ID 列表。"""
    return {"active": list(_quality_active), "count": len(_quality_active)}


async def _update_env_file(updates: dict[str, str]) -> None:
    """将键值对更新写入 .env 文件（保留其他配置不变）。"""
    env_path = Path(__file__).parent.parent.parent / ".env"

    def _write():
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
        else:
            content = ""

        for key, value in updates.items():
            if re.search(rf"^{key}=.*$", content, re.MULTILINE):
                content = re.sub(rf"^{key}=.*$", f"{key}={value}", content, flags=re.MULTILINE)
            else:
                if content and not content.endswith("\n"):
                    content += "\n"
                content += f"{key}={value}\n"

        env_path.write_text(content, encoding="utf-8")

    await asyncio.to_thread(_write)
    logger.info(f"已更新 .env: {list(updates.keys())}")


def _fmt_utc(dt) -> str:
    """将 naive UTC datetime 格式化为带 Z 后缀的 ISO 字符串。"""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_size(size_bytes: int) -> str:
    """将字节数转换为可读格式。"""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}"
