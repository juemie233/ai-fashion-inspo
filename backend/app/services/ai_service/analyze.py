"""穿搭分析编排：图片单帧 / 视频多帧分析，并保存标签、日志与向量。

按阶段拆分：
- _call_ollama_vision：调用 + 截断/上下文窗口重试
- _parse_and_save_tags：解析响应并保存标签
- _update_dominant_colors：主色调字段清洗与写回
- _write_analysis_log：分析日志与标签快照落库
- analyze_image_detail / analyze_video_detail：对外编排入口
- analyze_image / analyze_video：保留布尔契约的兼容包装
主函数只保留编排与向量重建。
"""

import hashlib
import json
import re
import time

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import AIAnalysisLog, AIAnalysisTag, Inspiration
from app.services.ai_parser import (
    is_empty_tag_payload,
    looks_truncated,
    parse_analysis_response,
)
from app.services.ai_service.common import _read_image_base64, logger
from app.services.ai_tag_saver import (
    clear_ai_tags,
    iter_extracted_tags,
    link_tag,
    resolve_tag_ids,
    save_tags,
)
from app.services.model_config import get_model_config
from app.services.model_prompt import get_model_prompt
from app.services.tag_service import get_or_create_tag


@dataclass(frozen=True)
class AnalysisOutcome:
    """一次分析的结果（比裸 bool 多两个信息：是否「无穿搭内容」、失败原因）。

    为什么不再返回裸 bool：批量分析要据此决定「这张图片没有穿搭内容 → 物理删除」，
    调用方还要在任务结果里区分「成功 / 无内容 / 失败」。保留 ``__bool__`` 让所有
    ``if ok:`` 的老调用点无需改动。

    Attributes:
        success: 分析成功（含「模型判定无穿搭内容」这种情况）。
        empty_payload: 模型返回了**合法但全空**的 JSON（不是解析失败）。
        error: 失败原因（成功时为 None）。
    """

    success: bool
    empty_payload: bool = False
    error: str | None = None

    def __bool__(self) -> bool:
        return self.success


async def write_unavailable_log(inspiration_id: str, error: str) -> None:
    """写入「分析源不可用」的失败日志（视频抽帧失败 / 素材已删除）。

    为什么必须落一条日志：抽帧失败原先只记 logger，素材既没有成功日志（仍算未分析）
    也没有失败记录（历史里看不到原因），用户只能看到一个永远清不掉的「未分析」计数。
    落库后它至少能在分析历史里被看见、被筛出来人工处理。

    Args:
        inspiration_id: 素材 ID。
        error: 失败原因（会直接展示在分析历史里）。
    """
    from app.database import async_session

    try:
        async with async_session() as db:
            db.add(
                AIAnalysisLog(
                    inspiration_id=inspiration_id,
                    model_name=settings.ollama_vision_model,
                    model_version=settings.ollama_vision_model,
                    prompt_version="",
                    processing_time_ms=0,
                    error=error,
                )
            )
            await db.commit()
    except Exception as log_err:  # 日志失败不能影响分析主流程
        logger.exception(f"写入分析源不可用日志出错 {inspiration_id}: {log_err}")


def _prompt_version(prompt: str) -> str:
    """计算 Prompt 的内容版本（SHA-256 前 8 位），用于追溯分析所用 Prompt。

    prompt 为空时返回空串，不能写 sha256("") 的假版本；否则质量看板会把
    「还没拿到 prompt 就失败」的日志聚成一个独立版本。
    """
    if not prompt:
        return ""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]


async def _call_ollama_vision(
    image_data: str,
    prompt: str,
    model_cfg: dict,
    file_size_mb: float,
    inspiration_id: str,
    model_name: str,
) -> str:
    """调用 Ollama 视觉 API 获取原始输出（含截断/上下文窗口自动重试），失败抛 RuntimeError。

    参数:
        model_name: 本次分析使用的视觉模型名（支持多模型组合分析，不再硬编码全局默认）
    """
    import httpx

    think_enabled = model_cfg.get("think", False)
    # 思考模型：思维链会消耗 num_predict 预算，需放大预算以避免 JSON 答案被截断
    num_predict = model_cfg["num_predict"]
    if think_enabled:
        num_predict = max(num_predict, 8192)
    # 上下文窗口：视觉模型对高分辨率图片编码消耗大量 token（实测约 4000），
    # 若沿用 Ollama 默认 4096，输出会被硬性截断（done_reason=length）
    num_ctx = model_cfg["num_ctx"]
    num_predict_attempt = num_predict

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
                            "model": model_name,
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
                                "num_predict": num_predict_attempt,
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
                    except Exception as exc:
                        logger.debug("_call_ollama_vision: 忽略异常（预期降级）：%s", exc)
                    # 400 + 上下文相关错误：图片 token 超过当前窗口，若还有更大窗口则继续重试
                    if status == 400 and (
                        "context" in detail.lower() or "window" in detail.lower()
                    ):
                        ctx_too_small = True
                        logger.warning(
                            "图片超出上下文窗口 (num_ctx=%s)，加倍窗口重试: %s",
                            num_ctx_attempt,
                            inspiration_id,
                        )
                        break
                    raise RuntimeError(
                        _http_error_message(status, detail, file_size_mb, model_name)
                    ) from e
                except httpx.TimeoutException:
                    raise RuntimeError(
                        f"AI 模型响应超时 ({model_cfg['timeout']} 秒，模型 {model_name})，"
                        "请检查 Ollama 是否正常运行或尝试增大该模型超时时间"
                    ) from None
                except httpx.ConnectError:
                    raise RuntimeError("无法连接 Ollama 服务，请确认 Ollama 已启动") from None
                except (httpx.ReadError, httpx.RemoteProtocolError) as exc:
                    raise RuntimeError(f"读取 Ollama 响应失败: {exc}") from None

                try:
                    result = response.json()
                except ValueError as exc:
                    raise RuntimeError("Ollama 返回了非 JSON 响应") from exc
                if not isinstance(result, dict) or "message" not in result:
                    raise RuntimeError("Ollama 返回格式异常，缺少 message 字段")
                raw_response = result["message"].get("content") or ""
                if raw_response and result.get("done_reason") != "length":
                    got_complete = True
                    break
                if raw_response and result.get("done_reason") == "length":
                    # token 预算耗尽：同时提高 num_ctx 与 num_predict；只加倍窗口、
                    # 输出预算不变时大概率再次截断，白花一轮视觉推理。
                    truncated = True
                    raw_response = ""
                    num_predict_attempt = min(max(num_predict_attempt * 2, 1024), 8192)
                    logger.warning(
                        "输出被 token 预算截断 (done_reason=length, num_ctx=%s, "
                        "num_predict=%s)，加倍窗口/输出预算重试: %s",
                        num_ctx_attempt,
                        num_predict_attempt,
                        inspiration_id,
                    )
                    break
                if think:
                    logger.warning("思考模型返回空内容，改用非思考模式重试: %s", inspiration_id)
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
    db: AsyncSession,
    inspiration_id: str,
    raw_response: str,
    apply_tags: bool = True,
    tags_data: dict | None = None,
) -> tuple[dict, list[tuple[str, str, float]], str | None]:
    """解析模型输出并保存标签，返回 (tags_data, 提取到的标签, 错误消息)。

    参数:
        apply_tags: 是否把标签合并到素材（inspiration_tags 关联）。为 False 时
            仅确保标签存在于标签表（供结构化快照引用），不建立素材关联，
            素材的正式标签保持不变（多模型 × 多提示词组合分析场景）。
        tags_data: 可选，已解析好的结果（用于落库前二次验证后传入）；
            缺省由本函数从 raw_response 解析。
    """
    if tags_data is None:
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
        if apply_tags:
            tag_count = await save_tags(db, inspiration_id, tags_data)
        else:
            # 不应用模式：只确保标签在标签表中存在（快照按 tag_id 引用），
            # 不写入 inspiration_tags 关联，素材标签保持不变
            name_categories = {
                name: category for name, category, _conf in extracted_tags
            }
            for name, category in name_categories.items():
                await get_or_create_tag(db, name, category, "ai_generated")
            tag_count = len(name_categories)
        if tag_count == 0:
            if is_empty_tag_payload(tags_data):
                # 合法但全空：模型正确回答「这张图没有穿搭内容」（蛋糕/风景/纯文字截图），
                # 不是失败。记成功 + 0 标签，素材才能离开「未分析」；是否物理删除由调用方决定
                logger.info(f"分析结果无穿搭内容（模型未给出任何标签）: {inspiration_id}")
            else:
                error_msg = f"AI 未提取到任何有效标签（原始输出 {len(raw_response)} 字符）"
                logger.warning(f"零标签分析 {inspiration_id}: {raw_response[:200]}")

    return tags_data, extracted_tags, error_msg


_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


async def _update_dominant_colors(
    db: AsyncSession, inspiration_id: str, tags_data: dict
) -> None:
    """更新素材的主色调字段（只接受 #RRGGBB，清理注释与括号描述）。

    不能把模型输出的 "N/A"、"米白色" 等任意字符串写进 dominant_colors：
    上传链路用 image_utils 生成的是 hex，混合格式会让主色调筛选和统计失效。
    """
    if not isinstance(tags_data.get("dominant_colors"), list):
        return
    clean_colors: list[str] = []
    for c in tags_data["dominant_colors"]:
        if not isinstance(c, str):
            continue
        # 去除 // 注释和括号描述
        c = c.split("//")[0].strip()
        c = re.sub(r"[（(][^)）]*[)）]", "", c).strip()
        if _HEX_COLOR_RE.match(c) and c.upper() not in clean_colors:
            clean_colors.append(c.upper())
    if not clean_colors:
        return
    # 只为写一个字符串字段，不要 ORM 加载整条素材（会触发 tags/analysis_logs 等
    # 集合的 selectin 预加载）；直接 UPDATE 单列。
    from sqlalchemy import update

    await db.execute(
        update(Inspiration)
        .where(Inspiration.id == inspiration_id)
        .values(dominant_colors=json.dumps(clean_colors[:3], ensure_ascii=False))
    )
    await db.flush()


async def _write_analysis_log(
    db: AsyncSession,
    inspiration_id: str,
    prompt: str,
    raw_response: str | None,
    error_msg: str | None,
    processing_time: int,
    extracted_tags: list[tuple[str, str, float]],
    model_name: str | None = None,
) -> None:
    """记录分析日志与标签快照（同一会话写入，失败时回滚，避免脏会话）。

    参数:
        model_name: 本次分析所用模型名，缺省回退到全局默认视觉模型
    """
    try:
        used_model = model_name or settings.ollama_vision_model
        log_entry = AIAnalysisLog(
            inspiration_id=inspiration_id,
            model_name=used_model,
            prompt_version=_prompt_version(prompt) if prompt else "",
            model_version=used_model,
            raw_response=raw_response,
            processing_time_ms=processing_time,
            error=error_msg,
        )
        db.add(log_entry)
        await db.flush()

        # 结构化快照：记录「本次分析提取了哪些标签」（仅成功路径），
        # 支撑多版本对比与追溯；快照只引用已有标签，不创建新标签。
        if not error_msg and extracted_tags:
            # get_or_create_tag 会做 DB 别名归并，提取名可能不是最终 Tag.name；
            # 快照按最终名反查，避免别名标签在快照里静默缺失。
            from app.utils.tag_normalizer import normalize_tag_name_async

            name_map: dict[str, str] = {}
            conf_map: dict[str, float] = {}
            for name, _category, conf in extracted_tags:
                actual_name = await normalize_tag_name_async(db, name)
                name_map[name] = actual_name
                conf_map[actual_name] = max(conf_map.get(actual_name, 0.0), conf)
            tag_ids = await resolve_tag_ids(
                db, list(dict.fromkeys(name_map.values()))
            )
            for actual_name, tag_id in tag_ids.items():
                db.add(
                    AIAnalysisTag(
                        log_id=log_entry.id,
                        tag_id=tag_id,
                        confidence=conf_map.get(actual_name, 0.8),
                    )
                )
        await db.commit()
    except Exception as log_err:
        logger.exception(f"写入分析日志失败 {inspiration_id}: {log_err}")
        # 显式回滚，避免会话进入 pending-rollback 状态；
        # 否则后续查询会抛 PendingRollbackError，被误判为永久失败
        try:
            await db.rollback()
        except Exception as exc:
            logger.debug("_write_analysis_log: 忽略异常（预期降级）：%s", exc)
            pass


async def analyze_image_detail(
    db: AsyncSession,
    inspiration_id: str,
    file_path: str,
    model_name: str | None = None,
    prompt: str | None = None,
    apply_tags: bool = True,
) -> AnalysisOutcome:
    """分析单张图片：调用视觉模型并保存提取的标签。

    参数:
        db: 数据库会话
        inspiration_id: 素材 UUID
        file_path: 图片文件的相对路径
        model_name: 视觉模型名（缺省用全局默认，多模型组合分析时传入）
        prompt: 分析 Prompt 文本（缺省用该模型当前配置的 Prompt）
        apply_tags: 是否把标签合并到素材；False 时只写分析日志与标签快照，
            素材的正式标签（inspiration_tags）与主色调字段保持不变

    返回:
        :class:`AnalysisOutcome`：``success`` 为真表示分析成功（含模型判定「无穿搭内容」
        的情况）；``empty_payload`` 标记模型返回了合法但全空的 JSON——调用方据此决定
        是否物理删除这张图片（批量分析里会删，详见 batch_analyze）。
    """
    used_model = model_name or settings.ollama_vision_model
    start_time = time.time()
    error_msg: str | None = None
    raw_response: str | None = None
    used_prompt = ""
    extracted_tags: list[tuple[str, str, float]] = []
    success = False
    empty_payload = False

    try:
        # 先取配置与 Prompt，再读图：读图失败时日志也能记录真实 prompt_version，
        # 不再写入 sha256("") 的假版本。
        model_cfg = get_model_config(used_model)
        used_prompt = prompt or get_model_prompt(used_model)

        image_data, file_size_mb = await _read_image_base64(file_path)

        # 图片体积检查 (>5MB 可能导致 Ollama 400)
        if file_size_mb > 5:
            logger.warning(f"图片较大 ({file_size_mb:.1f}MB)，可能导致分析失败: {file_path}")

        # 调用 Ollama 视觉 API（含截断/上下文窗口自动重试）
        raw_response = await _call_ollama_vision(
            image_data, used_prompt, model_cfg, file_size_mb, inspiration_id, used_model
        )

        # 高风险袜类（过膝/大腿/长筒袜）二次验证：用腿部放大裁剪做定向判定，
        # 与第一轮结论不一致时改写 items（如「黑色过膝袜」验证为连裤袜）。
        # 日志 raw_response 保留模型原始输出以便追溯；验证失败静默按原结果保存。
        tags_data = parse_analysis_response(raw_response)
        # 「合法但全空」必须建立在解析成功（非空 dict）上；解析失败不是
        # 「模型判定没有穿搭内容」，否则下游会物理删除图片。
        empty_payload = bool(tags_data) and is_empty_tag_payload(tags_data)
        if tags_data:
            # 二次验证是增强项：任何未预期异常都只记录并沿用第一轮结果，
            # 不能让一个验证器把整张图的分析打断。
            try:
                from app.services.ai_service.sock_verify import maybe_verify_socks

                tags_data = await maybe_verify_socks(
                    file_path, tags_data, model_cfg, used_model, inspiration_id
                )
            except Exception as verify_err:
                logger.exception(
                    "袜类二次验证异常，沿用第一轮结果 %s: %s",
                    inspiration_id,
                    verify_err,
                )

            try:
                from app.services.ai_service.leg_posture_verify import (
                    maybe_verify_leg_posture,
                )

                tags_data = await maybe_verify_leg_posture(
                    file_path, tags_data, model_cfg, used_model, inspiration_id
                )
            except Exception as verify_err:
                logger.exception(
                    "腿部姿态二次验证异常，沿用第一轮结果 %s: %s",
                    inspiration_id,
                    verify_err,
                )

        # 解析分析结果并保存标签（tags_data 已预解析，二次验证结果在此生效）
        tags_data, extracted_tags, error_msg = await _parse_and_save_tags(
            db, inspiration_id, raw_response, apply_tags=apply_tags, tags_data=tags_data
        )
        if error_msg:
            # 有错误时不提交半成品：save_tags 可能已清旧标签，rollback 把它恢复。
            await db.rollback()
        else:
            if apply_tags:
                # 主色调属于素材字段的写入，与标签合并同受 apply_tags 控制
                await _update_dominant_colors(db, inspiration_id, tags_data)
            await db.commit()
        success = error_msg is None

        # AI 分析完成（单条分析与批量分析共用本函数，均运行于后台任务）：
        # 立即重建文本 + 图像向量，保证新素材可被语义搜索 / 相似推荐检索到。
        # 标签此刻已入库，文本向量有内容可生成；图像向量对视频素材自动跳过。
        # LanceDB / CLIP / Ollama 不可用时内部静默降级，不影响分析结果。
        # 不应用标签模式（apply_tags=False）下素材标签未变化，无需重建向量。
        if success and apply_tags:
            try:
                from app.services.vector_service import rebuild_inspiration_vectors

                await rebuild_inspiration_vectors(db, inspiration_id)
            except Exception as vec_err:
                logger.warning(
                    f"分析完成后重建向量失败（忽略，不影响分析结果）"
                    f"{inspiration_id}: {vec_err}"
                )
                # 主事务已提交；此处回滚只清理向量重建留下的脏会话状态，
                # 否则 finally 里写分析日志会撞 PendingRollbackError。
                try:
                    await db.rollback()
                except Exception as rollback_err:
                    logger.debug(
                        "analyze_image_detail 向量失败后回滚异常（预期降级）：%s",
                        rollback_err,
                    )

    except Exception as e:
        error_msg = str(e)
        success = False
        # 异常路径不能把「空 payload」传给下游，否则会触发物理删除
        empty_payload = False
        logger.exception(f"AI 分析失败 {inspiration_id}: {e}")
        # 发生异常时回滚当前事务，确保日志记录不受脏事务影响
        try:
            await db.rollback()
        except Exception as exc:
            logger.debug("analyze_image_detail: 忽略异常（预期降级）：%s", exc)

    finally:
        processing_time = int((time.time() - start_time) * 1000)
        await _write_analysis_log(
            db,
            inspiration_id,
            used_prompt,
            raw_response,
            error_msg,
            processing_time,
            extracted_tags,
            model_name=used_model,
        )

    # 注意：return 必须放在 finally 之外。放在 finally 里会**吞掉** try 块的异常
    # （Python 语义：finally 中的 return 会取代正在传播的异常），导致失败被伪装成正常返回。
    return AnalysisOutcome(
        success=success,
        # apply_tags=False 的对比分析不修改素材，更不能触发图片物理删除
        empty_payload=empty_payload and success and apply_tags,
        error=error_msg,
    )


async def analyze_image(
    db: AsyncSession,
    inspiration_id: str,
    file_path: str,
    model_name: str | None = None,
    prompt: str | None = None,
    apply_tags: bool = True,
) -> bool:
    """分析单张图片，返回是否成功（保留既有布尔契约）。

    细节（是否「无穿搭内容」）请看 :func:`analyze_image_detail`：批量分析需要它来
    决定是否物理删除非穿搭图片；其余调用方只关心成败，用这个包装即可。
    """
    outcome = await analyze_image_detail(
        db,
        inspiration_id,
        file_path,
        model_name=model_name,
        prompt=prompt,
        apply_tags=apply_tags,
    )
    return bool(outcome)


async def analyze_video_detail(
    db: AsyncSession,
    inspiration_id: str,
    frame_paths: list[str],
    model_name: str | None = None,
    prompt: str | None = None,
    apply_tags: bool = True,
) -> AnalysisOutcome:
    """多帧融合分析视频素材：逐帧调用视觉模型，标签按帧融合后一次性落库。

    融合语义（与单图分析「先清后写」语义对齐）：
    - 标签：逐帧提取 (名称, 类别, 置信度)，同名标签取各帧最高置信度，
      全部帧融合完成后**一次性**清旧写新——不能逐帧调 save_tags（其先清
      后写语义会导致后一帧覆盖前一帧的标签）；
    - 主色调：取首个产出了主色调的帧的结果；
    - 日志：整个视频只写一条分析日志（raw_response 按帧拼接留痕，
      processing_time 为全部帧总耗时），快照记录融合后的标签集合。

    参数:
        db: 数据库会话
        inspiration_id: 素材 UUID（必须是视频素材，帧由调用方提取）
        frame_paths: 关键帧相对路径列表（相对 storage_root，时间序）
        model_name: 视觉模型名（缺省用全局默认，多模型组合分析时传入）
        prompt: 分析 Prompt 文本（缺省用该模型当前配置的 Prompt）
        apply_tags: 是否把融合标签写入素材；False 时只写日志与快照

    返回:
        :class:`AnalysisOutcome`：至少一帧解析成功即 ``success``；``empty_payload`` 表示
        帧都解析成功但一条标签都没得到（无穿搭内容）。视频不做自动删除（仅图片删）。
    """
    if not frame_paths:
        message = "视频无可用关键帧"
        logger.warning("视频分析无可用关键帧: %s", inspiration_id)
        await write_unavailable_log(inspiration_id, message)
        return AnalysisOutcome(success=False, error=message)

    used_model = model_name or settings.ollama_vision_model
    start_time = time.time()
    used_prompt = prompt or get_model_prompt(used_model)
    model_cfg = get_model_config(used_model)

    fused: dict[str, tuple[str, float]] = {}  # 标签名 -> (类别, 最高置信度)
    tags_data_list: list[dict] = []  # 各帧解析结果（主色调取用）
    raw_parts: list[str] = []  # 各帧原始输出（日志留痕）
    frame_errors: list[str] = []
    ok_frames = 0

    for idx, frame_rel in enumerate(frame_paths, start=1):
        try:
            image_data, file_size_mb = await _read_image_base64(frame_rel)
            raw_response = await _call_ollama_vision(
                image_data, used_prompt, model_cfg, file_size_mb,
                f"{inspiration_id}#f{idx}", used_model,
            )
            tags_data = parse_analysis_response(raw_response)
            if not tags_data:
                frame_errors.append(f"第 {idx} 帧: AI 响应无法解析为 JSON")
                logger.warning(f"视频第 {idx}/{len(frame_paths)} 帧解析失败 {inspiration_id}")
                continue
            ok_frames += 1
            raw_parts.append(f"[帧 {idx}] {raw_response}")
            tags_data_list.append(tags_data)
            for name, category, confidence in iter_extracted_tags(tags_data):
                existing = fused.get(name)
                if existing is None or confidence > existing[1]:
                    fused[name] = (category, confidence)
        except Exception as e:
            frame_errors.append(f"第 {idx} 帧: {e}")
            logger.warning(f"视频第 {idx}/{len(frame_paths)} 帧分析失败 {inspiration_id}: {e}")

    # 判定整体成败：至少一帧解析出结果即视为成功。
    # 「帧都解析成功但一条标签都没有」= 模型判定没有穿搭内容（短视频的封面帧、
    # 纯环境镜头），属正确回答而不是失败——记成功 + 0 标签，让它离开「未分析」。
    error_msg: str | None = None
    empty_payload = ok_frames > 0 and not fused
    if ok_frames == 0:
        error_msg = "视频全部关键帧分析失败：" + ("；".join(frame_errors[:3]) or "未知错误")
        empty_payload = False
    elif empty_payload:
        # 和图片路径对齐：只有所有帧都是「合法但全空」才算无穿搭内容；
        # 帧有标签但全部被规则过滤掉，是失败，不能静默离开未分析队列。
        has_raw_content = any(not is_empty_tag_payload(data) for data in tags_data_list)
        if has_raw_content:
            error_msg = "视频关键帧未提取到任何有效标签"
            empty_payload = False
        else:
            logger.info(f"视频分析结果无穿搭内容（{ok_frames} 帧均无标签）: {inspiration_id}")

    success = error_msg is None

    try:
        if success:
            if apply_tags:
                # 先清后写一次（与 save_tags 的「重新分析」语义一致），同名标签
                # 已按帧融合去重，link_tag 内部还会处理并发与置信度更新
                await clear_ai_tags(db, inspiration_id)
                for name, (category, confidence) in fused.items():
                    tag = await get_or_create_tag(db, name, category, "ai_generated")
                    await link_tag(db, inspiration_id, tag.id, confidence=confidence)
            else:
                # 不应用模式也要确保标签存在于 tags 表，否则快照 resolve_tag_ids
                # 查不到新标签，组合分析的视频快照会静默缺失。
                for name, (category, _confidence) in fused.items():
                    await get_or_create_tag(db, name, category, "ai_generated")
            # 主色调：取首个产出了合法 hex 的帧
            for tags_data in tags_data_list:
                if isinstance(tags_data.get("dominant_colors"), list) and tags_data["dominant_colors"]:
                    await _update_dominant_colors(db, inspiration_id, tags_data)
                    break

        await db.commit()

        if success and apply_tags:
            try:
                from app.services.vector_service import rebuild_inspiration_vectors

                await rebuild_inspiration_vectors(db, inspiration_id)
            except Exception as vec_err:
                logger.warning(f"视频分析完成后重建向量失败（忽略）{inspiration_id}: {vec_err}")
                try:
                    await db.rollback()
                except Exception as rollback_err:
                    logger.debug(
                        "analyze_video_detail 向量失败后回滚异常（预期降级）：%s",
                        rollback_err,
                    )
    except Exception as e:
        error_msg = error_msg or str(e)
        success = False
        empty_payload = False
        logger.exception(f"视频分析落库失败 {inspiration_id}: {e}")
        try:
            await db.rollback()
        except Exception as exc:
            logger.debug("analyze_video_detail: 忽略异常（预期降级）：%s", exc)

    processing_time = int((time.time() - start_time) * 1000)
    extracted = [(name, cat, conf) for name, (cat, conf) in fused.items()]
    await _write_analysis_log(
        db,
        inspiration_id,
        used_prompt,
        "\n----\n".join(raw_parts) if raw_parts else None,
        error_msg,
        processing_time,
        extracted,
        model_name=used_model,
    )
    logger.info(
        f"视频多帧分析完成 {inspiration_id}: {ok_frames}/{len(frame_paths)} 帧有效，"
        f"融合标签 {len(extracted)} 个，成功={success}"
    )
    return AnalysisOutcome(
        success=success,
        empty_payload=empty_payload and success,
        error=error_msg,
    )


async def analyze_video(
    db: AsyncSession,
    inspiration_id: str,
    frame_paths: list[str],
    model_name: str | None = None,
    prompt: str | None = None,
    apply_tags: bool = True,
) -> bool:
    """多帧融合分析视频，返回是否成功（保留既有布尔契约）。

    细节见 :func:`analyze_video_detail`。
    """
    outcome = await analyze_video_detail(
        db,
        inspiration_id,
        frame_paths,
        model_name=model_name,
        prompt=prompt,
        apply_tags=apply_tags,
    )
    return bool(outcome)


def _http_error_message(
    status: int, detail: str, file_size_mb: float, model_name: str | None = None
) -> str:
    """将 Ollama HTTP 错误转为人可读的中文消息。"""
    used_model = model_name or settings.ollama_vision_model
    if status == 400:
        if "image" in detail.lower() or "vision" in detail.lower():
            return f"图片格式不支持或模型不支持视觉功能 ({detail})"
        if file_size_mb > 5:
            return f"图片过大 ({file_size_mb:.1f}MB) 导致请求被拒绝。请尝试压缩图片到 5MB 以下。"
        if "context" in detail.lower() or "token" in detail.lower():
            return f"图片太大超出模型上下文限制。请使用更小的图片 (当前 {file_size_mb:.1f}MB)。"
        return f"请求参数错误 (400): {detail or '请检查图片格式和大小'}"
    if status == 404:
        return f"模型 '{used_model}' 未安装。请先在模型管理页下载模型。"
    if status == 413:
        return f"图片体积过大 ({file_size_mb:.1f}MB)，超过服务端限制。请压缩图片。"
    if status == 429:
        return "请求过于频繁，请稍后再试。"
    if status == 500:
        return f"Ollama 服务内部错误 (500): {detail or '请检查 Ollama 日志'}。"
    if status == 502 or status == 503:
        return "Ollama 服务暂时不可用，请确认模型已加载且显存充足。"
    return f"Ollama 返回错误 (HTTP {status}): {detail or '未知错误'}"
