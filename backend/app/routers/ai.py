"""AI 分析与模型管理的 REST API 路由。"""

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.utils.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])

# 分析任务并发控制：最多同时分析 2 个素材，避免显存溢出
_analysis_semaphore = asyncio.Semaphore(2)
# 正在分析中的 inspiration_id 集合，用于前端轮询
_active_analyses: dict[str, str] = {}  # inspiration_id -> 状态描述
# 保留任务引用，防止 GC 回收
_analysis_tasks: set[asyncio.Task] = set()

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
                   + (f"，跳过 {skipped} 个非图片素材" if skipped > 0 else ""),
        "count": len(inspirations),
        "skipped_videos": skipped,
    }


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
    # 已分析过
    analyzed = await db.execute(
        select(func.count(func.distinct(AIAnalysisLog.inspiration_id)))
    )
    analyzed_count = analyzed.scalar() or 0

    # 总素材数（仅图片，暂不分析视频）
    total = await db.execute(
        select(func.count()).select_from(Inspiration).where(
            Inspiration.media_type == "image"
        )
    )
    total_count = total.scalar() or 0

    # 失败的 — 只看每个素材的最新分析日志
    latest_log_sub = (
        select(
            AIAnalysisLog.inspiration_id,
            func.max(AIAnalysisLog.id).label("max_id"),
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


@router.get("/unanalyzed-ids")
async def unanalyzed_ids(db: AsyncSession = Depends(get_db)):
    """获取所有未分析过的图片素材 ID 列表（暂不分析视频）。"""
    analyzed_sub = select(AIAnalysisLog.inspiration_id).distinct()
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
    # 子查询：每个素材的最新日志 ID
    latest_log = (
        select(AIAnalysisLog.inspiration_id, func.max(AIAnalysisLog.id).label("max_id"))
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


@router.get("/active-analyses")
async def get_active_analyses():
    """获取当前正在分析中的素材列表，用于前端轮询显示进度。"""
    return {"active_analyses": _active_analyses, "count": len(_active_analyses)}


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
                                "num_predict": getattr(settings, "ai_num_predict", 1024),
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
    persist: bool = Query(False, description="是否持久化写入 .env 文件"),
):
    """更新 AI 参数（可选持久化到 .env 文件）。"""
    if confidence_threshold is not None:
        settings.ai_low_confidence_threshold = confidence_threshold
    if analysis_timeout is not None:
        settings.ai_analysis_timeout = analysis_timeout

    # 持久化：写入 .env 文件
    if persist:
        try:
            await _update_env_file({
                "AI_LOW_CONFIDENCE_THRESHOLD": str(settings.ai_low_confidence_threshold),
                "AI_ANALYSIS_TIMEOUT": str(settings.ai_analysis_timeout),
            })
        except Exception as e:
            logger.warning(f"写入 .env 失败: {e}")

    return {
        "message": "参数已更新" + ("并持久化" if persist else ""),
        "confidence_threshold": settings.ai_low_confidence_threshold,
        "analysis_timeout": settings.ai_analysis_timeout,
    }


@router.get("/sampling-params")
async def get_sampling_params():
    """获取 AI 采样参数（temperature, top_p, top_k, num_predict），从配置文件读取。"""
    return {
        "temperature": getattr(settings, "ai_temperature", 0.7),
        "top_p": getattr(settings, "ai_top_p", 0.9),
        "top_k": getattr(settings, "ai_top_k", 40),
        "num_predict": getattr(settings, "ai_num_predict", 1024),
    }


@router.put("/sampling-params")
async def update_sampling_params(
    temperature: float | None = Query(None, ge=0, le=2),
    top_p: float | None = Query(None, ge=0, le=1),
    top_k: int | None = Query(None, ge=1, le=100),
    num_predict: int | None = Query(None, ge=64, le=8192),
    persist: bool = Query(False),
):
    """更新 AI 采样参数（可选持久化）。"""
    updated = {}
    if temperature is not None:
        settings.ai_temperature = temperature
        updated["AI_TEMPERATURE"] = str(temperature)
    if top_p is not None:
        settings.ai_top_p = top_p
        updated["AI_TOP_P"] = str(top_p)
    if top_k is not None:
        settings.ai_top_k = top_k
        updated["AI_TOP_K"] = str(top_k)
    if num_predict is not None:
        settings.ai_num_predict = num_predict
        updated["AI_NUM_PREDICT"] = str(num_predict)

    if persist and updated:
        try:
            _update_env_file(updated)
        except Exception as e:
            logger.warning(f"写入 .env 失败: {e}")

    return {
        "message": "采样参数已更新" + ("并持久化" if persist else ""),
        "temperature": getattr(settings, "ai_temperature", 0.7),
        "top_p": getattr(settings, "ai_top_p", 0.9),
        "top_k": getattr(settings, "ai_top_k", 40),
        "num_predict": getattr(settings, "ai_num_predict", 1024),
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

    # 等待进行中的分析任务完成（最多 10 秒）
    if _active_analyses:
        logger.info(f"等待 {len(_active_analyses)} 个分析任务完成...")
        await aio.sleep(2)  # 给任务 2 秒完成当前步骤

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

    async with _analysis_semaphore:
        _active_analyses[inspiration_id] = "正在分析..."
        try:
            from app.services.ai_service import analyze_image

            logger.info(f"开始 AI 分析: {inspiration_id}")
            async with async_session() as db:
                await analyze_image(db, inspiration_id, file_path)
                # 分析成功后删除该素材的旧失败日志（历史垃圾数据）
                from sqlalchemy import delete
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
        except ImportError:
            logger.warning("AI 服务尚未安装")
        except Exception as e:
            logger.error(f"分析失败 {inspiration_id}: {e}")
        finally:
            _active_analyses.pop(inspiration_id, None)


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
