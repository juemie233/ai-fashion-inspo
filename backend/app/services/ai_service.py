"""AI 服务编排层：通过 Ollama 调用视觉模型进行穿搭分析、质量审核与大标签总结。

本模块负责「编排」：读取图片、调用模型、解析结果、写回数据库。
- 具体 JSON 解析/修复逻辑见 ai_parser.py
- 标签保存/关联逻辑见 ai_tag_saver.py
"""

import json
import logging
import re
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.services.ai_parser import looks_truncated, parse_analysis_response, parse_is_outfit
from app.services.ai_tag_saver import save_tags
from app.services.model_config import get_model_config
from app.services.model_prompt import get_model_prompt

# 支持的图片扩展名
_ALLOWED_IMG_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}

logger = logging.getLogger(__name__)


async def check_ollama_status() -> dict:
    """检查 Ollama 是否运行以及视觉模型是否可用。"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.status_code != 200:
                return {"status": "error", "message": "Ollama 服务无响应"}

            models = resp.json().get("models", [])
            model_names = [m["name"] for m in models]
            vision_available = any(
                name.startswith(settings.ollama_vision_model.split(":")[0])
                for name in model_names
            )

            return {
                "status": "ok" if vision_available else "model_missing",
                "ollama_url": settings.ollama_base_url,
                "available_models": model_names,
                "vision_model_available": vision_available,
                "recommended_model": settings.ollama_vision_model,
                "install_command": f"ollama pull {settings.ollama_vision_model}",
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"无法连接 Ollama：{str(e)}",
            "ollama_url": settings.ollama_base_url,
            "recommended_model": settings.ollama_vision_model,
        }


def _read_image_base64(file_path: str) -> tuple[str, float]:
    """读取图片并转为 base64（含路径校验和格式转换）。

    返回:
        (base64 字符串, 文件大小 MB)
    """
    storage_root = settings.storage_root.resolve()
    full_path = (storage_root / file_path).resolve()
    # 防御路径遍历攻击（按路径组件判定，Windows 下大小写不敏感）
    try:
        full_path.relative_to(storage_root)
    except ValueError:
        raise ValueError(f"非法的文件路径: {file_path}")
    if not full_path.exists():
        raise FileNotFoundError(f"图片不存在: {full_path}")
    if not full_path.is_file():
        raise ValueError(f"路径不是文件: {file_path}")

    # 图片预检：通过扩展名判断
    ext = full_path.suffix.lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise ValueError(f"不支持的图片格式: {ext}，支持: {', '.join(sorted(_ALLOWED_IMG_EXT))}")

    # 读取图片 —— WebP/BMP/GIF 统一转为 JPEG
    # 实测 qwen3-vl:8b-instruct 在 Ollama 下无法解码 WebP（报 "Failed to load image or audio file"），
    # JPEG 是所有视觉模型通用支持的格式，因此无论模型一律转换，保证兼容性。
    import base64
    image_bytes = full_path.read_bytes()
    if ext in {".webp", ".bmp", ".gif"}:
        try:
            from io import BytesIO
            from PIL import Image
            buf = BytesIO()
            Image.open(BytesIO(image_bytes)).convert("RGB").save(buf, "JPEG", quality=95)
            image_bytes = buf.getvalue()
            logger.info(f"{ext} → JPEG 转换完成 ({full_path.name})")
        except Exception as e:
            raise ValueError(f"{ext} 图片转换 JPEG 失败: {e}。文件可能已损坏。") from e
    image_data = base64.b64encode(image_bytes).decode("utf-8")
    file_size_mb = full_path.stat().st_size / (1024 * 1024)
    return image_data, file_size_mb


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

        return success


async def check_image_quality(
    db: AsyncSession, inspiration_id: str, file_path: str
) -> tuple[str, str]:
    """轻量质量审核：判断图片是否为真人穿搭照片。

    与完整分析不同，这里只做二分类，输出简短，速度快。审核结果直接写回
    Inspiration 的 quality_status / quality_reason 字段。

    参数:
        db: 数据库会话
        inspiration_id: 素材 UUID
        file_path: 图片文件的相对路径

    返回:
        (status, reason) — status 为 approved/rejected/pending（审核失败保持 pending）
    """
    import httpx

    prompt = (
        "请判断这张图片是否为「完整的真人穿搭照片」，即能看清整体搭配（例如上衣+下装的完整组合）的真人穿着照片。\n"
        "判定为「否」的情况：\n"
        "1. 无人物：商品平铺图、尺码表、广告、纯文字、与穿搭无关的内容\n"
        "2. 仅单品特写：只拍某一件单品，无真人整体穿着\n"
        "3. 局部/裁切特写：有真人但只拍到局部（如只有腿、只有脚、只有手臂、只有颈部领口），看不清整体搭配\n"
        "4. 构图裁切过度：人物主体被裁掉大部分，无法判断完整穿搭\n"
        '只输出 JSON，格式：{"is_outfit": true 或 false, "reason": "一句话简短理由"}'
    )

    try:
        image_data, _ = _read_image_base64(file_path)
    except FileNotFoundError:
        # 文件缺失：确定性失败，直接拒绝，避免永久停留 pending
        return "rejected", "文件缺失"
    except Exception as e:
        return "pending", f"审核失败: {str(e)[:100]}"

    model_cfg = get_model_config(settings.ollama_vision_model)
    think_enabled = model_cfg.get("think", False)
    # 思考模型：放大 num_predict 预算，避免短答案被思维链截断
    num_predict = model_cfg["num_predict"]
    if think_enabled:
        num_predict = max(num_predict, 8192)

    try:
        async with httpx.AsyncClient(timeout=model_cfg["timeout"]) as client:
            raw = ""
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
                        "think": think,  # 关闭思考模式
                        "options": {"temperature": 0.1, "num_predict": num_predict},
                    },
                )
                response.raise_for_status()
                result = response.json()
                raw = result.get("message", {}).get("content", "") or ""
                if raw:
                    break
            if not raw:
                return "pending", "模型返回空内容"
    except Exception as e:
        # 调用失败：保持 pending，不误判
        return "pending", f"审核失败: {str(e)[:100]}"

    # 解析 JSON（复用增强过的解析器，能处理注释/脏输出）
    parsed = parse_analysis_response(raw)
    if not isinstance(parsed, dict) or "is_outfit" not in parsed:
        return "pending", f"无法解析模型输出: {raw[:100]}"

    is_outfit = parse_is_outfit(parsed.get("is_outfit"))
    if is_outfit is None:
        return "pending", f"无法判定 is_outfit 值: {raw[:100]}"

    reason = str(parsed.get("reason", "")).strip() or ("穿搭照片" if is_outfit else "非穿搭内容")
    status = "approved" if is_outfit else "rejected"

    # 写回数据库（CAS：仅当仍为 pending 时写入，避免覆盖人工翻案）
    try:
        insp = await db.get(Inspiration, inspiration_id)
        if insp and insp.quality_status == "pending":
            insp.quality_status = status
            insp.quality_reason = reason if not is_outfit else None
            await db.commit()
    except Exception as e:
        logger.warning(f"质量审核写回失败 {inspiration_id}: {e}")
        return "pending", f"写回失败: {str(e)[:100]}"

    return status, reason


async def summarize_outfit_tags(small_tags: list[str]) -> list[str]:
    """根据小标签纯文本总结穿搭大标签（带特色闸门，宁缺毋滥）。

    只调用模型做文本总结（不传图片），速度快。返回建议的大标签列表，
    可能为空（表示素材不够有特色，不配拥有大标签）。

    参数:
        small_tags: 素材的现有小标签名称列表

    返回:
        大标签建议列表（去重、限 3 个）
    """
    import httpx

    if not small_tags:
        return []

    # 大标签总结固定用轻量非思考模型（思考模型会吃光预算返回空）
    summary_model = getattr(settings, "outfit_summary_model", "minicpm-v:8b")
    model_cfg = get_model_config(summary_model)
    tag_list = "、".join(small_tags)
    prompt = (
        "你是一个穿搭标签总结助手。根据以下穿搭小标签，提炼出 1~3 个「穿搭大标签」。\n"
        "穿搭大标签是「概括整套穿搭风格/场景的短语」，要把关键元素组合起来，例如：\n"
        "御姐长腿高跟鞋穿搭、甜妹白色过膝袜JK制服穿搭、御姐黑丝长筒皮靴穿搭、"
        "白色系穿搭、红色系穿搭、网球穿搭、女仆穿搭。\n\n"
        "要求：\n"
        "1. 组合「风格+单品+颜色/特征」形成完整短语，不要直接照抄单个小标签"
        "（如不要只输出「连衣裙」，应输出「法式连衣裙穿搭」）\n"
        "2. 短语要简洁、具体、可检索，通常以「穿搭」或「系」结尾\n"
        "3. 若穿搭普通、无特色（如普通T恤牛仔裤、基础款无亮点），返回空数组\n\n"
        f"小标签：{tag_list}\n\n"
        '只输出 JSON，格式：{"outfit_tags": ["大标签1", "大标签2"]}'
    )

    try:
        async with httpx.AsyncClient(timeout=model_cfg["timeout"]) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/chat",
                json={
                    "model": summary_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "think": False,  # 纯文本总结无需思考
                    "options": {"temperature": 0.3, "num_predict": 512},
                },
            )
            response.raise_for_status()
            raw = response.json().get("message", {}).get("content", "") or ""
    except Exception as e:
        logger.warning(f"大标签总结失败: {e}")
        return []

    if not raw:
        return []

    # 解析 {"outfit_tags": [...]}
    parsed = None
    try:
        parsed = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
            except Exception:
                parsed = None
    if not isinstance(parsed, dict):
        return []

    tags = parsed.get("outfit_tags") or []
    if not isinstance(tags, list):
        return []

    seen: set[str] = set()
    result: list[str] = []
    for t in tags:
        name = str(t).strip() if t else ""
        if name and name not in seen:
            seen.add(name)
            result.append(name)
        if len(result) >= 3:
            break
    return result


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
