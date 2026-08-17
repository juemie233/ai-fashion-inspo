"""质量审核编排：穿搭二分类 + AI 生成检测。

与完整分析不同，审核只做两步轻量调用，结果直接写回
Inspiration 的 quality_status / quality_reason / is_ai_generated 字段。
"""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import Inspiration
from app.services.ai_parser import parse_analysis_response, parse_is_outfit
from app.services.ai_service.common import _read_image_base64, logger
from app.services.model_config import get_model_config


async def _ollama_vision_chat(
    image_data: str, prompt: str, model_cfg: dict, temperature: float
) -> tuple[str, str | None]:
    """调用视觉模型返回原始文本。

    返回 ``(raw, error)``：``error`` 为 ``None`` 且 ``raw`` 非空表示成功；
    空内容或调用异常时 ``raw`` 为空串、``error`` 为原因描述，由调用方决定降级策略。
    """
    import httpx

    think_enabled = model_cfg.get("think", False)
    # 思考模型：思维链会消耗 num_predict 预算，需放大预算以避免 JSON 答案被截断
    num_predict = model_cfg["num_predict"]
    if think_enabled:
        num_predict = max(num_predict, 8192)

    try:
        async with httpx.AsyncClient(timeout=model_cfg["timeout"]) as client:
            # 思考模式若思维链耗尽预算会返回空内容，回退到非思考模式重试
            think_attempts = [think_enabled, False] if think_enabled else [False]
            for think in think_attempts:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/chat",
                    json={
                        "model": settings.ollama_vision_model,
                        "messages": [
                            {"role": "user", "content": prompt, "images": [image_data]}
                        ],
                        "stream": False,
                        "think": think,
                        "options": {
                            "temperature": temperature,
                            "num_predict": num_predict,
                            # 视觉模型图片编码 token 消耗大，必须显式传 num_ctx（默认 4096 会截断/拒绝大图）
                            "num_ctx": model_cfg["num_ctx"],
                        },
                    },
                )
                response.raise_for_status()
                result = response.json()
                raw = result.get("message", {}).get("content", "") or ""
                if raw:
                    return raw, None
        return "", "模型返回空内容"
    except httpx.ConnectError:
        # Ollama 未启动/连接失败：可恢复（_is_recoverable_error 命中「无法连接 Ollama」）
        return "", "无法连接 Ollama 服务"
    except httpx.TimeoutException:
        # 模型响应超时：可恢复（命中「超时」）
        return "", "调用 Ollama 超时"
    except httpx.HTTPStatusError as e:
        # 4xx 为请求/模型问题（如 400 模型未就绪），重试无益 → 永久错误；
        # 5xx 为服务端暂时异常（命中「Ollama 服务」）→ 可恢复重试
        status_code = e.response.status_code
        if 400 <= status_code < 500:
            return "", f"Ollama 请求被拒绝（HTTP {status_code}）"
        return "", f"Ollama 服务异常（HTTP {status_code}）"
    except Exception as e:
        return "", f"调用失败: {str(e)[:100]}"


def _parse_ai_generated(parsed: dict) -> bool:
    """解析 AI 生成检测结果：仅当模型明确判 true 且置信度达标才标记，宁缺毋滥。

    置信度缺失/非法或低于阈值时返回 False，避免把真实照片误标为疑似 AI。
    """
    if not isinstance(parsed, dict):
        return False
    if parse_is_outfit(parsed.get("is_ai_generated")) is not True:
        return False
    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError):
        return False
    return confidence >= settings.ai_generated_confidence_threshold


async def _classifier_prefilter(
    inspiration_id: str, vector: list[float] | None = None
) -> tuple[bool, float] | None:
    """负样本初筛器前置初筛：用已训练分类器对素材图像向量做「垃圾」判定。

    参数:
        inspiration_id: 素材 UUID
        vector: 预取的图像向量（批量审核时由任务执行器批量预取，避免逐条全表扫描）；
            为 None 时此处现场读取。

    返回 ``(是否垃圾, 垃圾置信度)``；未训练分类器、LanceDB 未安装或素材无图像向量时
    返回 None（静默跳过，仍走完整 VLM 审核）。「宁缺毋滥」：仅在置信度超过阈值时
    由调用方直接拒绝，否则退回 VLM 复审。
    """
    from app.services import quality_learner
    from app.services.vector import store as vector_store

    if not vector_store.is_lancedb_available():
        return None
    if vector is None:
        vector = await vector_store.get_vector("image", inspiration_id)
    if not vector:
        return None
    return await quality_learner.predict_vector(vector)


async def _write_quality_result(
    db: AsyncSession,
    inspiration_id: str,
    status: str,
    reason: str | None,
    is_ai_generated: bool,
    force: bool,
) -> str | None:
    """写回质量审核结果（CAS），返回 None 表示成功，否则返回错误描述。

    与旧逻辑一致：仅当素材仍为 pending（或 force=True 覆盖）时写入，
    避免覆盖人工翻案；初筛器拒绝与 VLM 审核两条路径共用本函数。
    用条件 UPDATE 原子完成「判定 + 写入」，避免 check-then-act 竞态。
    """
    try:
        stmt = (
            update(Inspiration)
            .where(Inspiration.id == inspiration_id)
            .values(
                quality_status=status,
                quality_reason=reason,
                is_ai_generated=is_ai_generated,
            )
        )
        if not force:
            # CAS：仅当仍为 pending 时写入（rowcount=0 表示已被人工翻案，放弃覆盖）
            stmt = stmt.where(Inspiration.quality_status == "pending")
        await db.execute(stmt)
        await db.commit()
    except Exception as e:
        logger.warning(f"质量审核写回失败 {inspiration_id}: {e}")
        return f"写回失败: {str(e)[:100]}"
    return None


async def check_image_quality(
    db: AsyncSession,
    inspiration_id: str,
    file_path: str,
    force: bool = False,
    prefilter_vector: list[float] | None = None,
) -> tuple[str, str, bool]:
    """轻量质量审核：分两步——先做穿搭二分类，通过后再单独做 AI 生成检测。

    与完整分析不同，这里输出简短、速度快。审核结果直接写回
    Inspiration 的 quality_status / quality_reason / is_ai_generated 字段。

    参数:
        db: 数据库会话
        inspiration_id: 素材 UUID
        file_path: 图片文件的相对路径
        force: 为 True 时覆盖写入，忽略素材当前审核状态（用于随机复审已审查素材）；
            默认 False 仅写入 pending 素材，避免覆盖人工翻案
        prefilter_vector: 预取的图像向量（批量审核场景由任务执行器一次性批量读取，
            供初筛器复用，避免逐条 get_vector 造成 O(N²) 全表扫描）

    返回:
        (status, reason, is_ai_generated) — status 为 approved/rejected/pending
        （审核失败保持 pending）；is_ai_generated 为是否疑似 AI 生成标记
    """
    outfit_prompt = (
        "请判断这张图片是否为「完整的真人穿搭照片」，即能看清整体搭配（例如上衣+下装的完整组合）的真人穿着照片。\n"
        "判定为「否」的情况：\n"
        "1. 无人物：商品平铺图、尺码表、广告、纯文字、与穿搭无关的内容\n"
        "2. 仅单品特写：只拍某一件单品，无真人整体穿着\n"
        "3. 局部/裁切特写：有真人但只拍到局部（如只有腿、只有脚、只有手臂、只有颈部领口），看不清整体搭配\n"
        "4. 构图裁切过度：人物主体被裁掉大部分，无法判断完整穿搭\n"
        '只输出 JSON，格式：{"is_outfit": true 或 false, "reason": "一句话简短理由"}'
    )

    ai_prompt = (
        "请仅判断这张图片是否疑似由 AI 生成。\n"
        "判定为 true 的硬性要求：必须找到至少 2 处具体的生成痕迹，并在 evidence 中逐条指出。\n"
        "AI 生成常见痕迹：手指/肢体数量或形态异常、背景文字乱码、透视或空间关系违和、"
        "塑料感或动漫感过强、纹理不自然重复。\n"
        "以下情况不算 AI 痕迹（不要据此判 true）：皮肤光滑/磨皮（可能是美颜或后期处理）、"
        "背景虚化/景深（人像摄影正常）、轻微光影不均。\n"
        '只输出 JSON，格式：{"is_ai_generated": true 或 false, "confidence": 0.0 到 1.0, "evidence": ["痕迹1", "痕迹2"]}'
    )

    try:
        image_data, _ = _read_image_base64(file_path)
    except FileNotFoundError:
        # 文件缺失：确定性失败，直接拒绝并写回，避免永久停留 pending
        err = await _write_quality_result(
            db, inspiration_id, "rejected", "文件缺失", False, force
        )
        if err:
            return "pending", err, False
        return "rejected", "文件缺失", False
    except Exception as e:
        return "pending", f"审核失败: {str(e)[:100]}", False

    model_cfg = get_model_config(settings.ollama_vision_model)

    # 阶段 2：负样本初筛器前置初筛（仅普通审核生效；随机复审 force=True 走完整 VLM，
    # 未训练分类器 / 无图像向量时静默跳过）
    if not force:
        prefilter = await _classifier_prefilter(inspiration_id, prefilter_vector)
        if prefilter is not None:
            is_garbage, proba = prefilter
            if is_garbage:
                reason = f"初筛器判定为垃圾素材（置信度 {proba:.2f}）"
                # 写回 DB 与 VLM 路径一致（CAS：仅 pending 时写入），避免素材永远停在 pending
                err = await _write_quality_result(
                    db, inspiration_id, "rejected", reason, False, force
                )
                if err:
                    return "pending", err, False
                return "rejected", reason, False

    # 第一步：穿搭二分类
    raw, err = await _ollama_vision_chat(image_data, outfit_prompt, model_cfg, temperature=0.1)
    if err:
        return "pending", f"审核失败: {err}", False

    # 解析 JSON（复用增强过的解析器，能处理注释/脏输出）
    parsed = parse_analysis_response(raw)
    if not isinstance(parsed, dict) or "is_outfit" not in parsed:
        return "pending", f"无法解析模型输出: {raw[:100]}", False

    is_outfit = parse_is_outfit(parsed.get("is_outfit"))
    if is_outfit is None:
        return "pending", f"无法判定 is_outfit 值: {raw[:100]}", False

    reason = str(parsed.get("reason", "")).strip() or ("穿搭照片" if is_outfit else "非穿搭内容")
    status = "approved" if is_outfit else "rejected"

    # 第二步：仅对通过穿搭判断的图片做 AI 生成检测（非穿搭内容无需检测，省一次调用）
    ai_generated = False
    if is_outfit:
        raw_ai, err_ai = await _ollama_vision_chat(image_data, ai_prompt, model_cfg, temperature=0.0)
        # AI 检测失败/空结果默认不标记，宁可不标，避免误报
        if not err_ai:
            ai_generated = _parse_ai_generated(parse_analysis_response(raw_ai))

    # 写回数据库（force=True 时覆盖写入，用于随机复审已审查素材；
    # 否则 CAS：仅当仍为 pending 时写入，避免覆盖人工翻案）
    err = await _write_quality_result(
        db,
        inspiration_id,
        status,
        reason if not is_outfit else None,
        ai_generated,
        force,
    )
    if err:
        return "pending", err, False

    return status, reason, ai_generated
