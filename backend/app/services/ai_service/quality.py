"""质量审核编排：穿搭二分类 + AI 生成检测。

与完整分析不同，审核只做两步轻量调用，结果直接写回
Inspiration 的 quality_status / quality_reason / is_ai_generated 字段。
"""

import hashlib

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import Inspiration
from app.services.ai_parser import parse_analysis_response, parse_is_outfit
from app.services.ai_service.common import _read_image_base64, logger
from app.services.model_config import get_model_config

"""拒绝原因码白名单（与 prompt 里的枚举保持一致；approved 时为 None）。"""
REASON_CODES = {"not_outfit", "single_item", "partial", "low_quality", "ad", "other"}


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
        # 429 限流与分析链路（analyze._http_error_message）口径一致，返回
        # 「请求过于频繁」文案以命中 _is_recoverable_error 的可恢复判定；
        # 5xx 为服务端暂时异常（命中「Ollama 服务」）→ 可恢复重试
        status_code = e.response.status_code
        if status_code == 429:
            return "", "请求过于频繁，请稍后再试。"
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
    reason_code: str | None = None,
) -> str | None:
    """写回质量审核结果（CAS），返回 None 表示成功，否则返回错误描述。

    与旧逻辑一致：仅当素材仍为 pending（或 force=True 覆盖）时写入，
    避免覆盖人工翻案；初筛器拒绝与 VLM 审核两条路径共用本函数。
    用条件 UPDATE 原子完成「判定 + 写入」，避免 check-then-act 竞态。

    **人工判定优先**：`quality_reviewed_by='human'` 的素材一律不覆盖，即使 force=True
    （随机复审）也不覆盖 —— 否则人工翻案会被下一次复审推翻。命中时返回可读原因。

    校验 rowcount：CAS 分支 0 行受影响（素材已被人工翻案/并发审核抢先）
    时返回「判定未生效」描述，调用方据此把结果记为未判定（pending），
    避免把从未落账的判定记入审核日志与任务统计。
    """
    try:
        stmt = (
            update(Inspiration)
            .where(Inspiration.id == inspiration_id)
            .values(
                quality_status=status,
                quality_reason=reason,
                is_ai_generated=is_ai_generated,
                # 结构化拒绝原因：approved 时为 None（同时清掉上一次的结构化原因）
                quality_reason_code=reason_code,
                # 判定来源：本条由模型判定（人工判定在 inspiration_update 服务里写 human）
                quality_reviewed_by="ai",
            )
        )
        # 人工判定优先：quality_reviewed_by='human' 且不在 pending 的素材一律不覆盖，
        # **即使 force=True（随机复审）也不覆盖** —— 否则人工翻案会被下一次复审推翻。
        # 人工把它设回 pending 表示「我要重审」，那种情况允许覆盖。
        stmt = stmt.where(
            or_(
                Inspiration.quality_reviewed_by.is_(None),
                Inspiration.quality_reviewed_by != "human",
                Inspiration.quality_status == "pending",
            )
        )
        if not force:
            # CAS：仅当仍为 pending 时写入（rowcount=0 表示已被人工翻案，放弃覆盖）
            stmt = stmt.where(Inspiration.quality_status == "pending")
        result = await db.execute(stmt)
        await db.commit()
        if result.rowcount == 0:
            # 人工已判定（或并发抢先）→ 明确回报，不静默丢弃
            return "素材已由人工判定，本次自动审核未覆盖（跳过）"
    except Exception as e:
        logger.warning(f"质量审核写回失败 {inspiration_id}: {e}")
        return f"写回失败: {str(e)[:100]}"
    return None


"""前置规则检查阈值：命中即直接拒绝，省掉两次 VLM 调用（审查新 P1）。"""

MIN_SIDE_PX = 64
"""短边小于该值视为「尺寸过小」：再怎么审也看不清穿搭。"""

MAX_UNIQUE_COLORS = 64
"""去重颜色数不超过该值视为「近乎纯色」：空图/纯色块/占位图，不可能是有效穿搭。"""

MAX_ASPECT_RATIO = 8.0
"""长短边比超过该值视为「极端比例」（长条截图/拼接条），穿搭主体必然被裁掉。"""


def _precheck_image(image_data: str) -> str | None:
    """在调用 VLM 之前做规则预检，命中返回 `quality_reason_code`，否则 None。

    仅在 `settings.quality_precheck_enabled` 开启时才会被调用（默认关闭）。

    只拦「肉眼可判、模型也判不准」的硬性垃圾：尺寸过小、近乎纯色、极端长宽比。
    误拦代价低（随机复审可 force 覆盖），但能省下整轮两次 VLM 调用，对上千条待审素材收益明显。

    Args:
        image_data: data URL（`_read_image_base64` 的返回值）

    Returns:
        命中时返回原因码（`low_quality` / `partial`），否则 None
    """
    import base64
    import io

    from PIL import Image

    try:
        payload = image_data.split(",", 1)[-1]
        with Image.open(io.BytesIO(base64.b64decode(payload))) as im:
            width, height = im.size
            if min(width, height) < MIN_SIDE_PX:
                return "low_quality"
            long_side, short_side = max(width, height), min(width, height)
            if short_side and long_side / short_side > MAX_ASPECT_RATIO:
                return "partial"
            colors = im.convert("RGB").resize((64, 64)).getcolors(maxcolors=MAX_UNIQUE_COLORS + 1)
            if colors is not None and len(colors) <= MAX_UNIQUE_COLORS:
                return "low_quality"
    except Exception:  # 解析失败交给下游 VLM 判（不要因预检把素材卡死在 pending）
        return None
    return None


def _should_sample_ai_check(inspiration_id: str, rate: float) -> bool:
    """按素材 ID 的哈希做稳定抽样（同一素材结论可复现，便于复盘与回归）。

    Args:
        inspiration_id: 素材 UUID
        rate: 抽样比例，0 表示全不抽、>=1 表示全抽

    Returns:
        本次是否对「被拒素材」补做 AI 生成检测
    """
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(inspiration_id.encode("utf-8")).hexdigest()
    # 取前 8 位十六进制映射到 [0,1)
    return int(digest[:8], 16) / 0x100000000 < rate


async def check_image_quality(
    db: AsyncSession,
    inspiration_id: str,
    file_path: str,
    force: bool = False,
    prefilter_vector: list[float] | None = None,
    stats: dict | None = None,
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
        stats: 可选的出参字典。本函数会写入 ``prefilter`` 键，取值：
            ``hit``（初筛器直接判垃圾）、``pass``（初筛通过，继续 VLM）、
            ``skipped``（无向量/未训练分类器）、``disabled``（force 复审不走初筛）。
            任务执行器据此统计「初筛器拦了多少 / 有多少根本没跑」，此前这两项完全不可见。

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
        '只输出 JSON，格式：{"is_outfit": true 或 false, "reason": "一句话简短理由", '
        '"reason_code": "not_outfit|single_item|partial|low_quality|ad|other"}\n'
        "reason_code 说明（仅 is_outfit=false 时必填）："
        "not_outfit=不是穿搭内容；single_item=只有单品特写没有穿在身上；"
        'partial=身体/穿搭被大幅裁切；low_quality=模糊低清或严重遮挡；ad=广告/文字/截图；other=其它。'
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

    # prompt 指纹与模型名：供审核日志落库，便于「同一 prompt 可比、跨 prompt 分组统计」
    outfit_prompt_hash = hashlib.sha256(outfit_prompt.encode("utf-8")).hexdigest()
    if stats is not None:
        stats["prompt_hash"] = outfit_prompt_hash
        stats["model_name"] = settings.ollama_vision_model

    try:
        image_data, _ = await _read_image_base64(file_path)
        # 规则预检：命中即拒，省掉两次 VLM 调用（原因码进 stats，供复盘与统计）
        precheck_code = _precheck_image(image_data) if settings.quality_precheck_enabled else None
        if precheck_code:
            precheck_reason = {
                "low_quality": "前置规则：图片过小或近乎纯色，无法判断穿搭",
                "partial": "前置规则：图片比例极端，穿搭主体不完整",
            }.get(precheck_code, "前置规则命中")
            if stats is not None:
                stats["prefilter"] = "skipped"
                stats["reason_code"] = precheck_code
                stats["raw_output"] = precheck_reason
            err = await _write_quality_result(
                db, inspiration_id, "rejected", precheck_reason, False, force, precheck_code
            )
            if err:
                return "pending", err, False
            return "rejected", precheck_reason, False
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
    if force:
        if stats is not None:
            stats["prefilter"] = "disabled"
    else:
        prefilter = await _classifier_prefilter(inspiration_id, prefilter_vector)
        if stats is not None:
            stats["prefilter"] = "skipped" if prefilter is None else "pass"
        if prefilter is not None:
            is_garbage, proba = prefilter
            if is_garbage:
                if stats is not None:
                    stats["prefilter"] = "hit"
                reason = f"初筛器判定为垃圾素材（置信度 {proba:.2f}）"
                # 写回 DB 与 VLM 路径一致（CAS：仅 pending 时写入），避免素材永远停在 pending
                err = await _write_quality_result(
                    db, inspiration_id, "rejected", reason, False, force, "prefilter_reject"
                )
                if err:
                    return "pending", err, False
                return "rejected", reason, False

    # 第一步：穿搭二分类
    raw, err = await _ollama_vision_chat(image_data, outfit_prompt, model_cfg, temperature=0.1)
    if err:
        return "pending", f"审核失败: {err}", False

    if stats is not None:
        # 只留穿搭分类这一步的原始输出：它才是 quality_status 的依据
        stats["raw_output"] = raw[:2000]
    # 解析 JSON（复用增强过的解析器，能处理注释/脏输出）
    parsed = parse_analysis_response(raw)
    if not isinstance(parsed, dict) or "is_outfit" not in parsed:
        return "pending", f"无法解析模型输出: {raw[:100]}", False

    is_outfit = parse_is_outfit(parsed.get("is_outfit"))
    if is_outfit is None:
        return "pending", f"无法判定 is_outfit 值: {raw[:100]}", False

    reason = str(parsed.get("reason", "")).strip() or ("穿搭照片" if is_outfit else "非穿搭内容")
    status = "approved" if is_outfit else "rejected"
    # 结构化原因码：模型给了合法值就用，否则按判定兜底（rejected → not_outfit），
    # approved 一律 None。自由文本 reason 仍保留（人看），code 用于筛选与统计。
    code_raw = str(parsed.get("reason_code", "")).strip()
    reason_code = code_raw if code_raw in REASON_CODES else (None if is_outfit else "not_outfit")
    if stats is not None:
        stats["reason_code"] = reason_code

    # 第二步：AI 生成检测。通过穿搭的必检；被拒的按比例抽样（见 quality_rejected_ai_sample_rate）
    ai_generated = False
    # 通过穿搭必检；被拒素材按比例抽样检测（让「AI 生成且非穿搭」可统计，成本可控）
    should_check_ai = is_outfit or _should_sample_ai_check(
        inspiration_id, settings.quality_rejected_ai_sample_rate
    )
    if should_check_ai:
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
        reason_code,
    )
    if err:
        return "pending", err, False

    return status, reason, ai_generated
