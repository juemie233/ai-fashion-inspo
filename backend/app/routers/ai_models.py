"""AI 子路由。"""

import asyncio
import json
import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.inspiration import (
    AIAnalysisLog,
    analysis_log_filter as _analysis_log_filter,
)
from app.routers.ai_shared import _format_size, _fmt_utc, _update_env_file
from app.services import gpu_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ============ 模型管理 ============


async def _ensure_model_installed(model_name: str) -> None:
    """校验模型已安装（调用 Ollama /api/tags），未安装抛 404、连接失败抛 503。"""
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


@router.get("/models")
async def list_models():
    """列出所有已安装的 Ollama 模型，含大小和修改时间，并标注视觉/嵌入角色。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            resp.raise_for_status()
            models = resp.json().get("models", [])

        # 当前活跃视觉模型与文本嵌入模型
        active = settings.ollama_vision_model
        embedding = settings.ollama_embedding_model

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
                "is_embedding": name == embedding,
                "vram_used": gpu_info.get(name, {}).get("vram_used", 0),
                "loaded": gpu_info.get(name, {}).get("loaded", False),
            })

        return {
            "models": result,
            "active_model": active,
            "embedding_model": embedding,
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"无法连接 Ollama: {e}")


async def _get_ollama_uptime_seconds() -> int | None:
    """获取 Ollama 进程运行时长（秒）；Windows 用 PowerShell 查进程启动时间，失败返回 None。"""
    if os.name != "nt":
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-Command",
            "$p = Get-Process ollama -ErrorAction SilentlyContinue | Select-Object -First 1; "
            "if ($p) { [int]((Get-Date) - $p.StartTime).TotalSeconds }",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        text = stdout.decode().strip()
        if text.isdigit():
            return int(text)
    except Exception:
        pass
    return None


@router.get("/status")
async def ai_status():
    """AI 服务状态：Ollama 连接、版本号、运行时长、活跃视觉模型与文本嵌入模型。"""
    connected = False
    version = ""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/version")
            if resp.status_code == 200:
                connected = True
                version = resp.json().get("version", "")
    except Exception:
        pass
    uptime = await _get_ollama_uptime_seconds() if connected else None
    return {
        "ollama_connected": connected,
        "ollama_version": version,
        "ollama_uptime_seconds": uptime,
        "active_model": settings.ollama_vision_model,
        "embedding_model": settings.ollama_embedding_model,
    }


def _pull_event_stream(model_name: str):
    """拉取模型的 SSE 事件流（供下载与更新复用）。"""

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

    return event_stream()


@router.post("/models/pull")
async def pull_model(
    model_name: str = Query(..., description="要下载的模型名称，如 gemma3:4b"),
):
    """拉取新模型（SSE 流式返回下载进度）。"""
    logger.info(f"开始拉取模型: {model_name}")
    return StreamingResponse(_pull_event_stream(model_name), media_type="text/event-stream")


@router.get("/models/{model_name:path}/detail")
async def model_detail(model_name: str):
    """获取模型完整元信息（调用 Ollama /api/show）。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/show",
                json={"model": model_name, "verbose": True},
            )
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"模型 '{model_name}' 不存在")
            resp.raise_for_status()
            data = resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"无法连接 Ollama: {e}")

    details = data.get("details") or {}
    model_info = data.get("model_info") or {}
    return {
        "name": model_name,
        "parameter_size": details.get("parameter_size", ""),
        "quantization_level": details.get("quantization_level", ""),
        "family": details.get("family", ""),
        "families": details.get("families", []),
        "format": details.get("format", ""),
        "parent_model": details.get("parent_model", ""),
        "architecture": details.get("architecture", ""),
        "template": data.get("template", ""),
        "system": data.get("system", ""),
        "license": data.get("license", ""),
        "modelfile": data.get("modelfile", ""),
        "parameters": data.get("parameters", ""),
        "model_info": model_info,
    }


@router.post("/models/{model_name:path}/update")
async def update_model(model_name: str):
    """更新已安装模型到最新版（对同 tag 执行 pull，SSE 流式返回进度）。"""
    await _ensure_model_installed(model_name)
    logger.info(f"开始更新模型: {model_name}")
    return StreamingResponse(_pull_event_stream(model_name), media_type="text/event-stream")


@router.post("/models/copy")
async def copy_model(
    source: str = Query(..., description="源模型名称"),
    destination: str = Query(..., description="目标模型名称"),
):
    """复制模型（调用 Ollama /api/copy）。"""
    if not destination.strip():
        raise HTTPException(status_code=400, detail="目标模型名称不能为空")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/copy",
                json={"source": source, "destination": destination},
            )
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"模型 '{source}' 不存在")
            resp.raise_for_status()
        return {"message": f"已复制 '{source}' → '{destination}'"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复制失败: {e}")


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
    """切换活跃视觉模型。"""
    await _ensure_model_installed(model_name)

    # 更新内存配置，并持久化到 .env，重启后仍生效
    settings.ollama_vision_model = model_name
    await _update_env_file({"OLLAMA_VISION_MODEL": model_name})
    return {"message": f"已切换到模型 '{model_name}'", "active_model": model_name}


@router.put("/models/embedding-active")
async def set_embedding_model(model_name: str = Query(...)):
    """切换文本嵌入模型（向量检索文本侧使用，持久化到 .env）。"""
    await _ensure_model_installed(model_name)

    settings.ollama_embedding_model = model_name
    await _update_env_file({"OLLAMA_EMBEDDING_MODEL": model_name})
    return {"message": f"已切换到嵌入模型 '{model_name}'", "embedding_model": model_name}


# ============ GPU 显存监控 ============


@router.get("/gpu-stats")
async def gpu_stats():
    """获取 GPU 显存占用和已加载模型信息（聚合逻辑在 app.services.gpu_service）。"""
    return await gpu_service.collect_gpu_stats()


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
    """获取按模型聚合的分析统计：每个模型的分析次数、成功率、平均耗时、平均标签数。

    平均标签数按 ``ai_extracted_tags`` 结构化快照统计（仅统计该模型成功分析
    日志实际提取的标签数），不混入人工补打或其他模型/来源的标签。
    """
    from app.models.inspiration import AIAnalysisTag

    # 所有标签分析日志（排除 quality_check 审核日志）
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
        .where(_analysis_log_filter())
        .group_by(AIAnalysisLog.model_name)
        .order_by(func.count().desc())
    )
    rows = result.all()

    # 每个模型的标签快照总数（仅成功分析日志的结构化提取结果）
    tag_rows = await db.execute(
        select(
            AIAnalysisLog.model_name,
            func.count(AIAnalysisTag.id).label("tag_total"),
        )
        .join(AIAnalysisTag, AIAnalysisTag.log_id == AIAnalysisLog.id)
        .where(_analysis_log_filter(), AIAnalysisLog.error.is_(None))
        .group_by(AIAnalysisLog.model_name)
    )
    tag_totals = {row[0]: row[1] for row in tag_rows}

    models = []
    total_success = 0
    total_tags = 0
    for row in rows:
        successes = int(row.successes or 0)
        tag_total = tag_totals.get(row.model_name, 0)
        avg_tags = round(tag_total / successes, 1) if successes > 0 else 0
        total_success += successes
        total_tags += tag_total

        models.append({
            "model_name": row.model_name,
            "total_analyses": row.total,
            "success_count": successes,
            "failure_count": row.total - successes,
            "success_rate": round(successes / row.total * 100, 1) if row.total > 0 else 0,
            "avg_time_ms": round(row.avg_time) if row.avg_time else 0,
            "avg_tags": avg_tags,
            "last_used": _fmt_utc(row.last_used),
        })

    # 全局汇总
    total_all = sum(m["total_analyses"] for m in models)
    models.insert(0, {
        "model_name": "（全部模型汇总）",
        "total_analyses": total_all,
        "success_count": total_success,
        "failure_count": total_all - total_success,
        "success_rate": round(total_success / total_all * 100, 1) if total_all > 0 else 0,
        "avg_time_ms": round(
            sum(m["avg_time_ms"] * m["total_analyses"] for m in models) / total_all
        ) if total_all > 0 else 0,
        "avg_tags": round(total_tags / total_success, 1) if total_success > 0 else 0,
        "last_used": max((m["last_used"] for m in models if m["last_used"]), default=""),
    })

    return {"models": models, "total_analyses": total_all}
