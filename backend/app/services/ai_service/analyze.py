"""穿搭分析编排：调用视觉模型分析单张图片并保存提取的标签。

analyze_image 原为 227 行的巨型函数，现按阶段拆分为：
- _call_ollama_vision：调用 + 截断/上下文窗口重试
- _parse_and_save_tags：解析响应并保存标签
- _update_dominant_colors：主色调字段清洗与写回
- _write_analysis_log：分析日志与标签快照落库
主函数只保留编排与向量重建。
"""

import hashlib
import json
import re
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import AIAnalysisLog, AIAnalysisTag, Inspiration
from app.services.ai_parser import looks_truncated, parse_analysis_response
from app.services.ai_service.common import _read_image_base64, logger
from app.services.ai_tag_saver import (
    iter_extracted_tags,
    resolve_tag_ids,
    save_tags,
)
from app.services.model_config import get_model_config
from app.services.model_prompt import get_model_prompt


def _prompt_version(prompt: str) -> str:
    """计算 Prompt 的内容版本（SHA-256 前 8 位），用于追溯分析所用 Prompt。"""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]


async def _call_ollama_vision(
    image_data: str,
    prompt: str,
    model_cfg: dict,
    file_size_mb: float,
    inspiration_id: str,
) -> str:
    """调用 Ollama 视觉 API 获取原始输出（含截断/上下文窗口自动重试），失败抛 RuntimeError。"""
    import httpx

    think_enabled = model_cfg.get("think", False)
    # 思考模型：思维链会消耗 num_predict 预算，需放大预算以避免 JSON 答案被截断
    num_predict = model_cfg["num_predict"]
    if think_enabled:
        num_predict = max(num_predict, 8192)
    # 上下文窗口：视觉模型对高分辨率图片编码消耗大量 token（实测约 4000），
    # 若沿用 Ollama 默认 4096，输出会被硬性截断（done_reason=length）
    num_ctx = model_cfg["num_ctx"]

    async with httpx.AsyncClient(timeout=model_cfg["timeout"]) as client:
        raw_response = ""
        truncated = False
        ctx_too_small = False
        # 思考模式若思维链耗尽预算会返回空内容，此时回退到非思考模式重试
        think_attempts = [think_enabled, False] if think_enabled else [False]
        # 输出被 token 预算截断时加倍 num_ctx 重试一次（封顶 65536）
        ctx_attempts = [num_ctx]
        doubled_ctx = min(num_ctx * 2, 65536)
        if doubled_ctx > num_ctx:
            ctx_attempts.append(doubled_ctx)
        for num_ctx_attempt in ctx_attempts:
            got_complete = False
            for think in think_attempts:
                try:
                    response = await client.post(
                        f"{settings.ollama_base_url}/api/chat",
                        json={
                            "model": settings.ollama_vision_model,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": prompt,
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
                                "num_ctx": num_ctx_attempt,
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
                    # 400 + 上下文相关错误：图片 token 超过当前窗口，若还有更大窗口则继续重试
                    if status == 400 and (
                        "context" in detail.lower() or "window" in detail.lower()
                    ):
                        ctx_too_small = True
                        logger.warning(
                            f"图片超出上下文窗口 (num_ctx={num_ctx_attempt})，"
                            f"加倍窗口重试: {inspiration_id}"
                        )
                        break
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
                if raw_response and result.get("done_reason") != "length":
                    got_complete = True
                    break
                if raw_response and result.get("done_reason") == "length":
                    # token 预算耗尽导致的截断：丢弃残缺输出，用更大的上下文窗口重试
                    truncated = True
                    raw_response = ""
                    logger.warning(
                        f"输出被 token 预算截断 (done_reason=length, num_ctx={num_ctx_attempt})，"
                        f"加倍上下文窗口重试: {inspiration_id}"
                    )
                    break
                if think:
                    logger.warning(f"思考模型返回空内容，改用非思考模式重试: {inspiration_id}")
            if got_complete:
                break

    if not raw_response:
        if ctx_too_small:
            raise RuntimeError(
                "图片占用的 token 超过模型上下文窗口（已尝试加倍 num_ctx 仍不足），"
                "请增大该模型的 num_ctx，或压缩图片分辨率后重试"
            )
        if truncated:
            raise RuntimeError(
                "AI 输出被 token 预算截断（已尝试加倍上下文窗口仍不完整），"
                "请重试，或增大该模型的 num_ctx / num_predict"
            )
        raise RuntimeError("Ollama 返回空内容，模型可能不支持视觉功能或图片无效")
    return raw_response


async def _parse_and_save_tags(
    db: AsyncSession, inspiration_id: str, raw_response: str
) -> tuple[dict, list[tuple[str, str, float]], str | None]:
    """解析模型输出并保存标签，返回 (tags_data, 提取到的标签, 错误消息)。"""
    tags_data = parse_analysis_response(raw_response)
    extracted_tags: list[tuple[str, str, float]] = []
    error_msg: str | None = None

    if not tags_data:
        if looks_truncated(raw_response):
            error_msg = (
                "AI 输出的 JSON 不完整（提前截断），自动修复未能恢复完整数据。"
                "可尝试点击重试；若反复失败，可增大该模型的 num_predict"
            )
        else:
            error_msg = "AI 响应无法解析为 JSON，原始输出无法识别"
        logger.warning(f"分析解析失败 {inspiration_id}: {raw_response[:200]}")
    else:
        extracted_tags = list(iter_extracted_tags(tags_data))
        tag_count = await save_tags(db, inspiration_id, tags_data)
        if tag_count == 0:
            error_msg = f"AI 未提取到任何有效标签（原始输出 {len(raw_response)} 字符）"
            logger.warning(f"零标签分析 {inspiration_id}: {raw_response[:200]}")

    return tags_data, extracted_tags, error_msg


async def _update_dominant_colors(
    db: AsyncSession, inspiration_id: str, tags_data: dict
) -> None:
    """更新素材的主色调字段（清理注释后缀与括号描述）。"""
    if not isinstance(tags_data.get("dominant_colors"), list):
        return
    clean_colors = []
    for c in tags_data["dominant_colors"]:
        if isinstance(c, str):
            # 去除 // 注释和括号描述
            c = c.split("//")[0].strip()
            c = re.sub(r"[（(][^)）]*[)）]", "", c).strip()
            if c:
                clean_colors.append(c)
    if not clean_colors:
        return
    insp = await db.get(Inspiration, inspiration_id)
    if insp:
        insp.dominant_colors = json.dumps(clean_colors)
        await db.flush()


async def _write_analysis_log(
    db: AsyncSession,
    inspiration_id: str,
    prompt: str,
    raw_response: str | None,
    error_msg: str | None,
    processing_time: int,
    extracted_tags: list[tuple[str, str, float]],
) -> None:
    """记录分析日志与标签快照（独立事务，即使主流程失败也能写入）。"""
    try:
        log_entry = AIAnalysisLog(
            inspiration_id=inspiration_id,
            model_name=settings.ollama_vision_model,
            prompt_version=_prompt_version(prompt),
            model_version=settings.ollama_vision_model,
            raw_response=raw_response,
            processing_time_ms=processing_time,
            error=error_msg,
        )
        db.add(log_entry)
        await db.flush()

        # 结构化快照：记录「本次分析提取了哪些标签」（仅成功路径），
        # 支撑多版本对比与追溯；快照只引用已有标签，不创建新标签。
        if not error_msg and extracted_tags:
            tag_ids = await resolve_tag_ids(
                db, list(dict.fromkeys(name for name, _, _ in extracted_tags))
            )
            conf_map = {name: conf for name, _, conf in extracted_tags}
            for name, tag_id in tag_ids.items():
                db.add(
                    AIAnalysisTag(
                        log_id=log_entry.id,
                        tag_id=tag_id,
                        confidence=conf_map.get(name, 0.8),
                    )
                )
        await db.commit()
    except Exception as log_err:
        logger.error(f"写入分析日志失败 {inspiration_id}: {log_err}")
        # 显式回滚，避免会话进入 pending-rollback 状态；
        # 否则后续查询会抛 PendingRollbackError，被误判为永久失败
        try:
            await db.rollback()
        except Exception:
            pass


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
    error_msg: str | None = None
    raw_response: str | None = None
    prompt = ""
    extracted_tags: list[tuple[str, str, float]] = []
    success = False

    try:
        image_data, file_size_mb = _read_image_base64(file_path)

        # 图片体积检查 (>5MB 可能导致 Ollama 400)
        if file_size_mb > 5:
            logger.warning(f"图片较大 ({file_size_mb:.1f}MB)，可能导致分析失败: {file_path}")

        # 按当前模型读取独立配置（隔离每个模型的超时与采样参数）
        model_cfg = get_model_config(settings.ollama_vision_model)
        prompt = get_model_prompt(settings.ollama_vision_model)

        # 调用 Ollama 视觉 API（含截断/上下文窗口自动重试）
        raw_response = await _call_ollama_vision(
            image_data, prompt, model_cfg, file_size_mb, inspiration_id
        )

        # 解析分析结果并保存标签
        tags_data, extracted_tags, error_msg = await _parse_and_save_tags(
            db, inspiration_id, raw_response
        )
        await _update_dominant_colors(db, inspiration_id, tags_data)

        # 提交所有变更
        await db.commit()
        success = error_msg is None

        # AI 分析完成（单条分析与批量分析共用本函数，均运行于后台任务）：
        # 立即重建文本 + 图像向量，保证新素材可被语义搜索 / 相似推荐检索到。
        # 标签此刻已入库，文本向量有内容可生成；图像向量对视频素材自动跳过。
        # LanceDB / CLIP / Ollama 不可用时内部静默降级，不影响分析结果。
        if success:
            try:
                from app.services.vector_service import rebuild_inspiration_vectors

                await rebuild_inspiration_vectors(db, inspiration_id)
            except Exception as vec_err:
                logger.warning(
                    f"分析完成后重建向量失败（忽略，不影响分析结果）"
                    f"{inspiration_id}: {vec_err}"
                )

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
        await _write_analysis_log(
            db,
            inspiration_id,
            prompt,
            raw_response,
            error_msg,
            processing_time,
            extracted_tags,
        )
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
