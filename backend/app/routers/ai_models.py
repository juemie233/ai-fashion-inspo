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
    _queue_paused,
    _run_analysis,
    _update_env_file,
    _fmt_utc,
    _format_size,
)
from app.services.model_config import get_model_config, update_model_config
from app.utils.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


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
