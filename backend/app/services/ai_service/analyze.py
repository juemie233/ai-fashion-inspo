"""穿搭分析编排：调用视觉模型分析单张图片并保存提取的标签。"""

import json
import re
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.services.ai_parser import looks_truncated, parse_analysis_response
from app.services.ai_service.common import _read_image_base64, logger
from app.services.ai_tag_saver import save_tags
from app.services.model_config import get_model_config
from app.services.model_prompt import get_model_prompt


async def analyze_image(db: AsyncSession, inspiration_id: str, file_path: str) -> bool:
    """分析单张图片：调用视觉模型并保存提取的标签。

    参数:
        db: 数据库会话
        inspiration_id: 素材 UUID
        file_path: 图片文件的相对路径

    返回:
        True 表示分析成功（无错误），False 表示分析失败
    """
    start_time = time.time()
    error_msg = None
    raw_response = None
    success = False

    try:
        import httpx

        image_data, file_size_mb = _read_image_base64(file_path)

        # 图片体积检查 (>5MB 可能导致 Ollama 400)
        if file_size_mb > 5:
            logger.warning(f"图片较大 ({file_size_mb:.1f}MB)，可能导致分析失败: {file_path}")

        # 按当前模型读取独立配置（隔离每个模型的超时与采样参数）
        model_cfg = get_model_config(settings.ollama_vision_model)
        think_enabled = model_cfg.get("think", False)
        # 思考模型：思维链会消耗 num_predict 预算，需放大预算以避免 JSON 答案被截断
        num_predict = model_cfg["num_predict"]
        if think_enabled:
            num_predict = max(num_predict, 8192)

        # 调用 Ollama 视觉 API — 传入采样参数
        async with httpx.AsyncClient(timeout=model_cfg["timeout"]) as client:
            raw_response = ""
            # 思考模式若思维链耗尽预算会返回空内容，此时回退到非思考模式重试
            think_attempts = [think_enabled, False] if think_enabled else [False]
            for think in think_attempts:
                try:
                    response = await client.post(
                        f"{settings.ollama_base_url}/api/chat",
                        json={
                            "model": settings.ollama_vision_model,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": get_model_prompt(settings.ollama_vision_model),
                                    "images": [image_data],
                                }
                            ],
                            "stream": False,
                            "think": think,  # 关闭思考模式（可每模型配置）
                            "options": {
                                "temperature": model_cfg["temperature"],
                                "top_p": model_cfg["top_p"],
                                "top_k": model_cfg["top_k"],
                                "num_predict": num_predict,
                            },
                        },
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    # 将 HTTP 状态码转为人可读的中文错误信息
                    status = e.response.status_code
                    detail = ""
                    try:
                        detail = e.response.json().get("error", "")
                    except Exception:
                        pass
                    raise RuntimeError(
                        _http_error_message(status, detail, file_size_mb)
                    ) from e
                except httpx.TimeoutException:
                    raise RuntimeError(
                        f"AI 模型响应超时 ({settings.ai_analysis_timeout} 秒)，"
                        "请检查 Ollama 是否正常运行或尝试增大超时时间"
                    )
                except httpx.ConnectError:
                    raise RuntimeError("无法连接 Ollama 服务，请确认 Ollama 已启动")

                result = response.json()
                if not isinstance(result, dict) or "message" not in result:
                    raise RuntimeError(f"Ollama 返回格式异常，缺少 message 字段")
                raw_response = result["message"].get("content") or ""
                if raw_response:
                    break
                if think:
                    logger.warning(f"思考模型返回空内容，改用非思考模式重试: {inspiration_id}")

            if not raw_response:
                raise RuntimeError("Ollama 返回空内容，模型可能不支持视觉功能或图片无效")

        # 解析分析结果并保存标签
        tags_data = parse_analysis_response(raw_response)
        if not tags_data:
            if looks_truncated(raw_response):
                error_msg = (
                    "AI 输出的 JSON 被截断（可能因思考模型 num_predict 不足），"
                    "请增大 num_predict 或关闭 think"
                )
            else:
                error_msg = "AI 响应无法解析为 JSON，原始输出无法识别"
            logger.warning(f"分析解析失败 {inspiration_id}: {raw_response[:200]}")
        else:
            tag_count = await save_tags(db, inspiration_id, tags_data)
            if tag_count == 0:
                error_msg = f"AI 未提取到任何有效标签（原始输出 {len(raw_response)} 字符）"
                logger.warning(f"零标签分析 {inspiration_id}: {raw_response[:200]}")

        # 更新素材的主色调字段（清理注释后缀）
        if isinstance(tags_data.get("dominant_colors"), list):
            clean_colors = []
            for c in tags_data["dominant_colors"]:
                if isinstance(c, str):
                    # 去除 // 注释和括号描述
                    c = c.split("//")[0].strip()
                    c = re.sub(r'[（(][^)）]*[)）]', '', c).strip()
                    if c:
                        clean_colors.append(c)
            if clean_colors:
                insp = await db.get(Inspiration, inspiration_id)
                if insp:
                    insp.dominant_colors = json.dumps(clean_colors)
                    await db.flush()

        # 提交所有变更
        await db.commit()
        success = (error_msg is None)

    except Exception as e:
        error_msg = str(e)
        success = False
        logger.error(f"AI 分析失败 {inspiration_id}: {e}")
        # 发生异常时回滚当前事务，确保日志记录不受脏事务影响
        try:
            await db.rollback()
        except Exception:
            pass

    finally:
        processing_time = int((time.time() - start_time) * 1000)

        # 记录分析日志（独立事务，即使主流程失败也能写入）
        try:
            log_entry = AIAnalysisLog(
                inspiration_id=inspiration_id,
                model_name=settings.ollama_vision_model,
                raw_response=raw_response,
                processing_time_ms=processing_time,
                error=error_msg,
            )
            db.add(log_entry)
            await db.flush()
            await db.commit()
        except Exception as log_err:
            logger.error(f"写入分析日志失败 {inspiration_id}: {log_err}")
            # 显式回滚，避免会话进入 pending-rollback 状态；
            # 否则后续查询会抛 PendingRollbackError，被误判为永久失败
            try:
                await db.rollback()
            except Exception:
                pass

        return success


def _http_error_message(status: int, detail: str, file_size_mb: float) -> str:
    """将 Ollama HTTP 错误转为人可读的中文消息。"""
    if status == 400:
        if "image" in detail.lower() or "vision" in detail.lower():
            return f"图片格式不支持或模型不支持视觉功能 ({detail})"
        if file_size_mb > 5:
            return f"图片过大 ({file_size_mb:.1f}MB) 导致请求被拒绝。请尝试压缩图片到 5MB 以下。"
        if "context" in detail.lower() or "token" in detail.lower():
            return f"图片太大超出模型上下文限制。请使用更小的图片 (当前 {file_size_mb:.1f}MB)。"
        return f"请求参数错误 (400): {detail or '请检查图片格式和大小'}"
    if status == 404:
        return f"模型 '{settings.ollama_vision_model}' 未安装。请先在模型管理页下载模型。"
    if status == 413:
        return f"图片体积过大 ({file_size_mb:.1f}MB)，超过服务端限制。请压缩图片。"
    if status == 429:
        return "请求过于频繁，请稍后再试。"
    if status == 500:
        return f"Ollama 服务内部错误 (500): {detail or '请检查 Ollama 日志'}。"
    if status == 502 or status == 503:
        return "Ollama 服务暂时不可用，请确认模型已加载且显存充足。"
    return f"Ollama 返回错误 (HTTP {status}): {detail or '未知错误'}"
