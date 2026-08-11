"""AI 分析与模型管理的 REST API 路由。"""

import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.models.inspiration import AIAnalysisLog, Inspiration

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])

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

    # 更新配置（仅内存，重启后需 .env 反映）
    settings.ollama_vision_model = model_name
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

    asyncio.create_task(_run_analysis(inspiration_id, inspiration.file_path))
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
        select(Inspiration).where(Inspiration.id.in_(inspiration_ids))
    )
    inspirations = result.scalars().all()

    if not inspirations:
        raise HTTPException(status_code=404, detail="未找到任何素材")

    for insp in inspirations:
        asyncio.create_task(_run_analysis(insp.id, insp.file_path))

    return {
        "message": f"已将 {len(inspirations)} 个素材加入分析队列",
        "count": len(inspirations),
    }


# ============ 分析队列与历史 ============


@router.get("/queue")
async def analysis_queue(db: AsyncSession = Depends(get_db)):
    """获取分析队列状态：待分析/分析中/已完成/失败统计。"""
    # 已分析过
    analyzed = await db.execute(
        select(func.count(func.distinct(AIAnalysisLog.inspiration_id)))
    )
    analyzed_count = analyzed.scalar() or 0

    # 总素材数
    total = await db.execute(select(func.count()).select_from(Inspiration))
    total_count = total.scalar() or 0

    # 失败的
    failed = await db.execute(
        select(func.count(func.distinct(AIAnalysisLog.inspiration_id))).where(
            AIAnalysisLog.error.isnot(None)
        )
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


@router.get("/history")
async def analysis_history(
    page: int = 1,
    size: int = 20,
    status: str | None = None,  # success | error
    db: AsyncSession = Depends(get_db),
):
    """获取分析历史记录列表。"""
    query = select(AIAnalysisLog)
    if status == "success":
        query = query.where(AIAnalysisLog.error.is_(None))
    elif status == "error":
        query = query.where(AIAnalysisLog.error.isnot(None))

    query = query.order_by(AIAnalysisLog.created_at.desc())

    # 总数
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # 分页
    query = query.offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    logs = result.scalars().all()

    items = []
    for log in logs:
        # 获取素材缩略图
        insp = await db.get(Inspiration, log.inspiration_id)
        items.append({
            "id": log.id,
            "inspiration_id": log.inspiration_id,
            "model_name": log.model_name,
            "thumbnail_path": insp.thumbnail_path if insp else None,
            "file_path": insp.file_path if insp else None,
            "processing_time_ms": log.processing_time_ms,
            "error": log.error,
            "status": "error" if log.error else "success",
            "created_at": log.created_at,
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

    asyncio.create_task(_run_analysis(inspiration_id, inspiration.file_path))
    return {"message": "已重新加入分析队列", "inspiration_id": inspiration_id}


# ============ 参数调优 ============


@router.get("/settings")
async def get_ai_settings():
    """获取当前 AI 参数配置。"""
    return {
        "active_model": settings.ollama_vision_model,
        "confidence_threshold": settings.ai_low_confidence_threshold,
        "analysis_timeout": settings.ai_analysis_timeout,
        "ollama_base_url": settings.ollama_base_url,
    }


@router.put("/settings")
async def update_ai_settings(
    confidence_threshold: float | None = Query(None, ge=0, le=1),
    analysis_timeout: int | None = Query(None, ge=10, le=300),
):
    """更新 AI 参数（仅内存，重启后恢复默认）。"""
    if confidence_threshold is not None:
        settings.ai_low_confidence_threshold = confidence_threshold
    if analysis_timeout is not None:
        settings.ai_analysis_timeout = analysis_timeout
    return {
        "message": "参数已更新",
        "confidence_threshold": settings.ai_low_confidence_threshold,
        "analysis_timeout": settings.ai_analysis_timeout,
    }


# ============ 工具函数 ============


async def _run_analysis(inspiration_id: str, file_path: str):
    """后台任务：对图片执行 AI 分析并保存标签。"""
    try:
        from app.services.ai_service import analyze_image

        logger.info(f"开始 AI 分析: {inspiration_id}")
        async with async_session() as db:
            await analyze_image(db, inspiration_id, file_path)
        logger.info(f"AI 分析完成: {inspiration_id}")
    except ImportError:
        logger.warning("AI 服务尚未安装")
    except Exception as e:
        logger.error(f"分析失败 {inspiration_id}: {e}")


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
