"""AI 子路由。"""

import json
import logging

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.models.inspiration import Inspiration
from app.services import ai_dashboard_service
from app.services.model_config import get_model_config
from app.services.model_prompt import get_model_prompt

logger = logging.getLogger(__name__)
router = APIRouter()


# ============ 分析质量仪表盘 ============


@router.get("/quality-dashboard")
async def quality_dashboard(db: AsyncSession = Depends(get_db)):
    """分析质量总览：每日趋势、问题素材、标签覆盖率（聚合在 ai_dashboard_service）。"""
    return await ai_dashboard_service.collect_quality_dashboard(db)


# ============ 单图测试 ============


@router.post("/test-analyze")
async def test_analyze(
    inspiration_id: str | None = Query(None, description="使用已有素材 ID 测试"),
    custom_prompt: str | None = Query(None, description="临时覆盖 prompt（可选）"),
    file: UploadFile | None = File(None, description="直接上传图片测试（优先于素材 ID）"),
):
    """单图即时测试：使用当前模型和参数分析图片，SSE 流式返回结果。

    支持两种图片来源：直接上传图片（file，优先）或已有素材 ID（inspiration_id）。
    不保存分析记录到数据库，仅用于测试 prompt/参数效果。
    """
    import base64 as b64

    # 获取图片字节与扩展名：优先使用上传的文件，否则回退到素材文件
    if file is not None:
        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(400, "上传的图片为空")
        filename = file.filename or ""
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ".jpg"
    elif inspiration_id:
        async with async_session() as db:
            insp = await db.get(Inspiration, inspiration_id)
            if not insp:
                raise HTTPException(404, "素材未找到")
            if insp.media_type != "image":
                raise HTTPException(400, "暂不支持分析视频文件")
            file_path = insp.file_path

        full_path = (settings.storage_root / file_path).resolve()
        if not str(full_path).startswith(str(settings.storage_root.resolve())):
            raise HTTPException(400, "非法的文件路径")
        if not full_path.exists():
            raise HTTPException(404, "图片文件不存在")

        image_bytes = full_path.read_bytes()
        ext = full_path.suffix.lower()
    else:
        raise HTTPException(400, "请上传图片或指定 inspiration_id")

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
