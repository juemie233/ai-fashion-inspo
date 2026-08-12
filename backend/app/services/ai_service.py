"""AI 服务：通过 Ollama 调用视觉模型进行穿搭分析。

本模块负责：
- 与 Ollama API 通信
- 将图片发送给 MiniCPM-V 进行分析
- 解析并校验返回的 JSON 结果
- 根据分析结果创建标签

Phase 2 完整实现。
"""

import json
import logging
import re
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import AIAnalysisLog, Inspiration

# 支持的图片扩展名
_ALLOWED_IMG_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}

# MiniCPM-V 等模型不支持 WebP，需要转为 JPEG
_WEBP_NEEDS_CONVERSION = True

logger = logging.getLogger(__name__)

# 分析 prompt 从配置读取（前端可编辑），保留此注释标记旧常量位置


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


async def analyze_image(db: AsyncSession, inspiration_id: str, file_path: str):
    """分析单张图片：调用视觉模型并保存提取的标签。

    参数:
        db: 数据库会话
        inspiration_id: 素材 UUID
        file_path: 图片文件的相对路径
    """
    start_time = time.time()
    error_msg = None
    raw_response = None

    try:
        import httpx

        full_path = (settings.storage_root / file_path).resolve()
        # 防御路径遍历攻击
        if not str(full_path).startswith(str(settings.storage_root.resolve())):
            raise ValueError(f"非法的文件路径: {file_path}")
        if not full_path.exists():
            raise FileNotFoundError(f"图片不存在: {full_path}")
        if not full_path.is_file():
            raise ValueError(f"路径不是文件: {file_path}")

        # 图片预检：通过扩展名判断
        ext = full_path.suffix.lower()
        if ext not in _ALLOWED_IMG_EXT:
            raise ValueError(f"不支持的图片格式: {ext}，支持: {', '.join(sorted(_ALLOWED_IMG_EXT))}")

        # 读取图片 —— WebP 需要转为 JPEG（MiniCPM-V 等模型不支持 WebP）
        import base64
        image_bytes = full_path.read_bytes()
        if ext == ".webp" and _WEBP_NEEDS_CONVERSION:
            try:
                from io import BytesIO
                from PIL import Image
                buf = BytesIO()
                Image.open(BytesIO(image_bytes)).convert("RGB").save(buf, "JPEG", quality=95)
                image_bytes = buf.getvalue()
                logger.info(f"WebP → JPEG 转换完成 ({full_path.name})")
            except Exception as e:
                raise ValueError(f"WebP 图片转换 JPEG 失败: {e}。文件可能已损坏。") from e
        image_data = base64.b64encode(image_bytes).decode("utf-8")

        # 图片体积检查 (>5MB 可能导致 Ollama 400)
        file_size_mb = full_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 5:
            logger.warning(f"图片较大 ({file_size_mb:.1f}MB)，可能导致分析失败: {file_path}")

        # 调用 Ollama 视觉 API — 传入采样参数
        async with httpx.AsyncClient(timeout=settings.ai_analysis_timeout) as client:
            try:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/chat",
                    json={
                        "model": settings.ollama_vision_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": settings.ai_analysis_prompt,
                                "images": [image_data],
                            }
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
            raw_response = result["message"].get("content")
            if not raw_response:
                raise RuntimeError("Ollama 返回空内容，模型可能不支持视觉功能或图片无效")

        # 解析分析结果并保存标签
        tags_data = _parse_analysis_response(raw_response)
        if not tags_data:
            error_msg = "AI 响应无法解析为 JSON，原始输出无法识别"
            logger.warning(f"分析解析失败 {inspiration_id}: {raw_response[:200]}")
        else:
            tag_count = await _save_tags(db, inspiration_id, tags_data)
            if tag_count == 0:
                error_msg = f"AI 未提取到任何有效标签（原始输出 {len(raw_response)} 字符）"
                logger.warning(f"零标签分析 {inspiration_id}: {raw_response[:200]}")

        # 更新素材的主色调字段（清理注释后缀）
        if tags_data.get("dominant_colors"):
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

    except Exception as e:
        error_msg = str(e)
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


def _parse_analysis_response(raw: str) -> dict:
    """从模型响应中提取并解析 JSON。"""
    text = raw.strip()

    # 去除可能的 markdown 代码块标记
    if text.startswith("```"):
        lines = text.split("\n")
        start_idx = 0
        end_idx = len(lines)
        for i, line in enumerate(lines):
            if line.startswith("```") and start_idx == 0:
                start_idx = i + 1
            elif line.startswith("```") and start_idx > 0:
                end_idx = i
                break
        text = "\n".join(lines[start_idx:end_idx])

    # 先尝试直接解析（模型可能在前导文字后直接输出合法 JSON）
    try:
        data = json.loads(text)
        return data
    except json.JSONDecodeError:
        pass

    # 尝试在文本中查找 JSON 对象（处理前导文字 + JSON 末尾的模式）
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        json_candidate = match.group()
        try:
            data = json.loads(json_candidate)
            return data
        except json.JSONDecodeError:
            pass

    # 最后手段：去除注释后再尝试
    cleaned = re.sub(r'//.*?$', '', text, flags=re.MULTILINE)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    match2 = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match2:
        try:
            data = json.loads(match2.group())
            return data
        except json.JSONDecodeError:
            pass

    # 终极兜底：修复被截断的 JSON（num_predict 不足导致输出被切断）
    repaired = _repair_truncated_json(text)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    return {}


def _repair_truncated_json(text: str) -> str | None:
    """尝试修复被截断的 JSON（模型输出超过 num_predict 限制）。"""
    start = text.find("{")
    if start == -1:
        return None
    fragment = text[start:]

    # 追踪括号栈
    brackets = []
    in_string = False
    escaped = False
    for ch in fragment:
        if escaped:
            escaped = False; continue
        if ch == '\\':
            escaped = True; continue
        if ch == '"':
            in_string = not in_string; continue
        if in_string:
            continue
        if ch == '{': brackets.append('}')
        elif ch == '[': brackets.append(']')
        elif ch == '}' and brackets and brackets[-1] == '}': brackets.pop()
        elif ch == ']' and brackets and brackets[-1] == ']': brackets.pop()

    # 闭合末尾未闭合的字符串（模型被截断时常见）
    if in_string:
        fragment += '"'

    # 补齐缺失的闭合符
    fragment += ''.join(reversed(brackets))

    return fragment if fragment.startswith('{') else None


async def _save_tags(db: AsyncSession, inspiration_id: str, data: dict) -> int:
    """将 AI 分析提取的标签保存到数据库。返回创建的标签数。"""
    from app.services.tag_service import get_or_create_tag
    from app.models.tag import InspirationTag
    from app.utils.tag_normalizer import normalize_tag_name

    tag_count = 0

    # 数据键 -> 标签类别的映射
    category_map = {
        "style": "style",
        "fit": "fit",
        "wear_style": "body_part",
        "occasion": "occasion",
        "attributes": "attribute",
    }

    # 处理简单列表型标签（风格、版型、场合等） — 兼容 null 值
    for key, category in category_map.items():
        values = data.get(key) or []
        if not isinstance(values, list):
            values = [values] if values else []
        for value in values:
            extracted = _extract_tag_names(value)
            for name in extracted:
                name = normalize_tag_name(name)
                if name:
                    tag = await get_or_create_tag(db, name, category, "ai_generated")
                    await _link_tag(db, inspiration_id, tag.id, confidence=0.8)
                    tag_count += 1

    # 处理结构化单品标签 — 兼容 type/color 为列表、features 为字符串
    items = data.get("items") or []
    if not isinstance(items, list):
        items = [items] if isinstance(items, dict) else []
    for item in items:
        if isinstance(item, dict):
            # type/color 可能是列表 → 取首元素或 join
            raw_type = item.get("type", "")
            if isinstance(raw_type, list):
                raw_type = raw_type[0] if raw_type else ""
            item_type = normalize_tag_name(str(raw_type).strip())

            raw_color = item.get("color", "")
            if isinstance(raw_color, list):
                raw_color = raw_color[0] if raw_color else ""
            color = _normalize_color(str(raw_color).strip())

            features = item.get("features", [])
            # features 可能是字符串 → 按顿号/逗号拆分
            if isinstance(features, str):
                features = [p.strip() for p in features.replace('，', ',').replace('、', ',').split(',') if p.strip()]

            if item_type:
                tag = await get_or_create_tag(db, item_type, "item_type", "ai_generated")
                await _link_tag(db, inspiration_id, tag.id, confidence=0.8)
                tag_count += 1

            if color:
                tag = await get_or_create_tag(db, color, "color", "ai_generated")
                await _link_tag(db, inspiration_id, tag.id, confidence=0.85)
                tag_count += 1

            for feat in features:
                if isinstance(feat, str):
                    for fv in _extract_tag_names(feat):
                        fv = normalize_tag_name(fv)
                        if fv:
                            tag = await get_or_create_tag(db, fv, "body_part", "ai_generated")
                            await _link_tag(db, inspiration_id, tag.id, confidence=0.7)
                            tag_count += 1
                elif isinstance(feat, dict):
                    for fv in _extract_tag_names(feat):
                        fv = normalize_tag_name(fv)
                        if fv:
                            tag = await get_or_create_tag(db, fv, "body_part", "ai_generated")
                            await _link_tag(db, inspiration_id, tag.id, confidence=0.7)
                            tag_count += 1

    return tag_count


def _extract_tag_names(value) -> list[str]:
    """从任意 AI 返回值中递归提取标签名称字符串。

    AI 模型可能返回:
      - 纯字符串: "坐姿"
      - 嵌套 dict: {"type": "坐姿", "position": ["沙发"]}
      - 混合列表
    此函数递归提取所有有意义的字符串值。
    """
    if isinstance(value, str):
        s = value.strip()
        if not s or len(s) <= 1:
            return []
        if s.startswith("{") and s.endswith("}"):
            return []
        if any(c in s for c in '。！？…~'):
            return []
        if any(w in s for w in ('这是一', '图片中', '背景为', '整体造型', '完整展示', '人物为')):
            return []

        # 先做分隔符拆分（拆分后再递归检查每部分的长度和合法性）
        if '，' in s or ',' in s:
            parts = [p.strip() for p in s.replace('，', ',').split(',') if p.strip()]
            results = []
            for p in parts:
                results.extend(_extract_tag_names(p))
            return results
        if '、' in s:
            parts = [p.strip() for p in s.split('、') if p.strip()]
            results = []
            for p in parts:
                results.extend(_extract_tag_names(p))
            return results
        if '/' in s:
            parts = [p.strip() for p in s.split('/') if p.strip()]
            results = []
            for p in parts:
                results.extend(_extract_tag_names(p))
            return results

        # 拆分后再检查长度
        if len(s) > 8:
            return []
        if s.isascii() and not any(c.isdigit() for c in s):
            return []
        if s.startswith('#') or (len(s) == 6 and all(c in '0123456789ABCDEFabcdef' for c in s)):
            return []
        if s.isdigit():
            return []
        # 去除括号内容（如 "室内拍摄（棚拍）" → "室内拍摄"）
        if '（' in s or '(' in s:
            s = s.split('（')[0].split('(')[0].strip() if '（' in s else s.split('(')[0].strip()
            if not s or len(s) <= 1:
                return []
        return [s]

    if isinstance(value, (int, float, bool)):
        return []

    if isinstance(value, dict):
        results = []

        # 第1轮：已知的 value-only key（直接提取值）
        known_value_keys = (
            "type", "name", "label",
            "属性", "属性名称", "属性标签", "标签",
            "部位", "style_name", "style",
            "description", "描述",
        )
        for key in known_value_keys:
            v = value.get(key)
            if v is not None:
                results.extend(_extract_tag_names(v))

        # 第2轮：pose/position 结构字段
        for key in ("pose", "position", "body_position", "orientation"):
            v = value.get(key)
            if v is not None:
                results.extend(_extract_tag_names(v))

        # 第3轮：中文语境键（图片属性、场合、季节等）
        for key in ("图片属性", "属性值", "适合场合", "穿着方式",
                     "穿着方式/身体部位关系"):
            v = value.get(key)
            if v is not None:
                results.extend(_extract_tag_names(v))

        # 第4轮：兜底 — 遍历所有值，通过递归确保过滤一致
        # 处理 {"宽松/修身": "修身"} 或 {"上衣": "黑色长袖"} 等非标准键
        if not results:
            for k, v in value.items():
                # 跳过纯英文 boolean 键（如 {'face': True}）
                if isinstance(k, str) and not any('一' <= c <= '鿿' for c in k):
                    if isinstance(v, bool):
                        continue
                # 所有值统一通过递归 _extract_tag_names 处理，复用长度/标点过滤
                results.extend(_extract_tag_names(v))

        return results

    if isinstance(value, list):
        results = []
        for item in value:
            results.extend(_extract_tag_names(item))
        return results

    return []


# 常用 hex 颜色 → 中文名称映射
_HEX_COLOR_MAP: dict[str, str] = {
    # 红/粉
    "#FF0000": "红色", "#FF0F1C": "红色", "#E60012": "红色",
    "#FF008C": "粉色", "#FF69B4": "粉色", "#FFC0CB": "粉色",
    "#FFB6C1": "粉色", "#f1a0d6": "粉色",
    # 橙/黄/金
    "#FFA500": "橙色", "#FF8C00": "橙色",
    "#FFD700": "金色", "#FFC41B": "金色", "#FFB30A": "金色", "#E4B53A": "金色",
    "#FFFF00": "黄色",
    # 绿
    "#008000": "绿色", "#00FF00": "绿色", "#128F7D": "青绿色", "#015342": "深绿色",
    # 蓝
    "#0000FF": "蓝色", "#0000A2": "深蓝色", "#0A3647": "深蓝色",
    "#0D173A": "深蓝色", "#0E1A3D": "深蓝色", "#000039": "深蓝色",
    "#1E90FF": "蓝色", "#4169E1": "蓝色",
    # 紫
    "#800080": "紫色", "#8B00FF": "紫色", "#4B0082": "紫色",
    # 黑/白/灰
    "#000000": "黑色", "#000": "黑色", "#0C1317": "黑色",
    "#1e1d20": "黑色", "#1A0B2C": "深紫色",
    "#FFFFFF": "白色", "#FFF": "白色",
    "#808080": "灰色", "#A2A2AA": "灰色", "#C0C0C0": "银色",
    # 棕/米
    "#8B4513": "棕色", "#6C4B2A": "棕色", "#8C6B49": "棕色", "#b78432": "棕色",
    "#A0522D": "棕色",
    "#F5DEB3": "米色", "#F5F5DC": "米色",
    # 肤色
    "#FFE4C4": "肤色", "#FFDAB9": "肤色", "#FFE4B5": "肤色", "#E5938D": "肤色",
}


def _normalize_color(raw: str) -> str:
    """将原始颜色值标准化为中文颜色名。"""
    if not raw:
        return ""

    # 已经是中文颜色名（可能带 # 前缀，如 "#黑色"）
    has_chinese = any('一' <= c <= '鿿' for c in raw)
    if has_chinese:
        # 去掉 # 前缀
        return raw.lstrip("#").strip()

    # 英文颜色名（可能带 # 前缀，如 "#Black"）
    EN_COLOR_MAP = {"BLACK": "黑色", "WHITE": "白色", "RED": "红色", "BLUE": "蓝色",
                    "GREEN": "绿色", "YELLOW": "黄色", "PINK": "粉色", "PURPLE": "紫色",
                    "ORANGE": "橙色", "BROWN": "棕色", "GRAY": "灰色", "GREY": "灰色",
                    "GOLD": "金色", "SILVER": "银色", "BEIGE": "米色"}
    upper_raw = raw.lstrip("#").strip().upper()
    if upper_raw in EN_COLOR_MAP:
        return EN_COLOR_MAP[upper_raw]

    # 去除 # 前缀后查找
    cleaned = raw.strip().upper()
    if not cleaned.startswith("#"):
        cleaned = f"#{cleaned}"

    # 精确匹配
    if cleaned in _HEX_COLOR_MAP:
        return _HEX_COLOR_MAP[cleaned]

    # 前缀模糊匹配（如 #000039 → 深蓝色）
    if len(cleaned) >= 4:
        prefix = cleaned[:4]
        for hex_key, name in _HEX_COLOR_MAP.items():
            if hex_key.startswith(prefix):
                return name

    # 按 RGB 分量推断基本颜色
    return _guess_color_from_hex(cleaned)


def _guess_color_from_hex(hex_str: str) -> str:
    """根据十六进制颜色值推断基本颜色名称。"""
    try:
        h = hex_str.lstrip("#")
        if len(h) >= 6:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        elif len(h) == 3:
            r, g, b = int(h[0], 16) * 17, int(h[1], 16) * 17, int(h[2], 16) * 17
        else:
            return ""

        # 灰度检测
        if abs(r - g) < 20 and abs(g - b) < 20 and abs(r - b) < 20:
            if r < 40:
                return "黑色"
            elif r > 220:
                return "白色"
            elif r < 120:
                return "深灰色"
            else:
                return "浅灰色"

        # 基本颜色推断
        if r > g and r > b:
            if r - max(g, b) < 40:
                if g > b:
                    return "棕色"
                return "粉色" if r > 200 else "深红色"
            return "红色"
        if g > r and g > b:
            return "绿色"
        if b > r and b > g:
            return "蓝色"
        if r > 150 and g > 100 and b < 80:
            return "橙色"
        if r > 150 and g > 150 and b < 60:
            return "金色"
        return ""
    except (ValueError, IndexError):
        return ""


async def _link_tag(
    db: AsyncSession,
    inspiration_id: str,
    tag_id: int,
    confidence: float = 1.0,
):
    """将标签与素材关联，避免重复。纠竞态冲突，置信度更高时更新。"""
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    from app.models.tag import InspirationTag

    result = await db.execute(
        select(InspirationTag).where(
            InspirationTag.inspiration_id == inspiration_id,
            InspirationTag.tag_id == tag_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        if confidence > existing.confidence:
            existing.confidence = confidence
        await db.flush()
    else:
        link = InspirationTag(
            inspiration_id=inspiration_id,
            tag_id=tag_id,
            confidence=confidence,
        )
        db.add(link)
        try:
            await db.flush()
        except IntegrityError:
            # 并发场景下对方已先插入，回滚后重查更新
            await db.rollback()
            result2 = await db.execute(
                select(InspirationTag).where(
                    InspirationTag.inspiration_id == inspiration_id,
                    InspirationTag.tag_id == tag_id,
                )
            )
            retry_existing = result2.scalar_one_or_none()
            if retry_existing and confidence > retry_existing.confidence:
                retry_existing.confidence = confidence
                await db.flush()
