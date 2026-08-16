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
from app.services.model_prompt import get_model_prompt

logger = logging.getLogger(__name__)
router = APIRouter()


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

    # 问题素材统计（排除垃圾桶）
    total_insp = (await db.execute(
        select(func.count(Inspiration.id)).where(Inspiration.deleted_at.is_(None))
    )).scalar() or 0
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
    prompt = custom_prompt or get_model_prompt(settings.ollama_vision_model)
    model_cfg = get_model_config(settings.ollama_vision_model)

    async def event_stream():
        import time as _time
        started = _time.time()

        try:
            async with httpx.AsyncClient(timeout=model_cfg["timeout"]) as client:
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
                                "temperature": model_cfg["temperature"],
                                "top_p": model_cfg["top_p"],
                                "top_k": model_cfg["top_k"],
                                "num_predict": model_cfg["num_predict"],
                                "num_ctx": model_cfg["num_ctx"],
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
            from app.services.ai_parser import parse_analysis_response
            parsed = parse_analysis_response(raw) if raw else {}

            # 流式输出解析结果
            yield f"data: {json.dumps({'type': 'done', 'raw_response': raw, 'parsed': parsed, 'model': settings.ollama_vision_model, 'elapsed_ms': elapsed_ms})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
