"""AI 服务：通过 Ollama 调用视觉模型进行穿搭分析。

本模块负责：
- 与 Ollama API 通信
- 将图片发送给 Qwen2-VL 进行分析
- 解析并校验返回的 JSON 结果
- 根据分析结果创建标签

Phase 2 完整实现。
"""

import json
import logging
import time
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import AIAnalysisLog, Inspiration

logger = logging.getLogger(__name__)

# 视觉模型分析提示词（中文 — 模型需要用中文理解穿搭概念）
ANALYSIS_PROMPT = """你是一个专业的时尚穿搭分析助手。请分析这张穿搭图片，提取以下维度的标签：

1. 风格体系：JK制服/汉服/Lolita/Y2K/CleanFit/法式/日系/韩系/学院风/街头/新中式/复古/极简/美式复古/英伦风/波西米亚/运动风/甜美风/暗黑风
   （可以输出多个风格标签，如果没有明显风格可以不输出）

2. 单品识别：识别图中每一件主要服饰单品，包括类型+颜色+特征。
   格式：{"type": "单品类型", "color": "颜色", "features": ["特征1", "特征2"]}

3. 版型：宽松/修身/Oversized/直筒/紧身/A字/H型/喇叭/锥形/阔腿

4. 穿着方式/身体部位关系：过膝/露腰/高腰/V领/圆领/高领/一字肩/七分袖/长袖/短袖/无袖/拖地/迷你/中长款/长款/短款

5. 适合场合：日常/通勤/约会/出游/校园/派对/运动/居家/度假/逛街

6. 适合季节：春季/夏季/秋季/冬季

7. 图片属性：露脸/不露脸/全身/半身/坐姿/站姿/对镜自拍/他拍/叠穿/单穿/街拍/棚拍

8. 主色调提取：提取2-3个主要颜色（返回hex值）

请以JSON格式输出，不要包含任何其他文字：
{
  "style": [],
  "items": [{"type": "", "color": "", "features": []}],
  "fit": [],
  "wear_style": [],
  "occasion": [],
  "season": [],
  "attributes": [],
  "dominant_colors": []
}"""


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

        full_path = settings.storage_root / file_path
        if not full_path.exists():
            raise FileNotFoundError(f"图片不存在: {full_path}")

        # 读取图片并编码为 base64
        import base64
        with open(full_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        # 调用 Ollama 视觉 API — 传入采样参数
        async with httpx.AsyncClient(timeout=settings.ai_analysis_timeout) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/chat",
                json={
                    "model": settings.ollama_vision_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": ANALYSIS_PROMPT,
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
            result = response.json()
            raw_response = result["message"]["content"]

        # 解析分析结果并保存标签
        tags_data = _parse_analysis_response(raw_response)
        await _save_tags(db, inspiration_id, tags_data)

        # 更新素材的主色调字段
        insp = await db.get(Inspiration, inspiration_id)
        if insp and tags_data.get("dominant_colors"):
            insp.dominant_colors = json.dumps(tags_data["dominant_colors"])
            await db.flush()

        # 提交所有变更
        await db.commit()

    except Exception as e:
        error_msg = str(e)
        logger.error(f"AI 分析失败 {inspiration_id}: {e}")

    finally:
        processing_time = int((time.time() - start_time) * 1000)

        # 记录分析日志
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


def _parse_analysis_response(raw: str) -> dict:
    """从模型响应中提取并解析 JSON。"""
    import re
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

    # 去除 JSON 中的 // 和 /* */ 注释（模型偶尔会输出）
    text = re.sub(r'//.*?$', '', text, flags=re.MULTILINE)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试在文本中查找 JSON 对象
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return {}
        else:
            return {}

    return data


async def _save_tags(db: AsyncSession, inspiration_id: str, data: dict):
    """将 AI 分析提取的标签保存到数据库。

    处理 AI 模型输出的不稳定性：列表元素可能是纯字符串或嵌套 dict，
    递归提取所有可用的字符串标签。
    """
    from app.services.tag_service import get_or_create_tag
    from app.models.tag import InspirationTag
    from app.utils.tag_normalizer import normalize_tag_name

    # 数据键 -> 标签类别的映射
    category_map = {
        "style": "style",
        "fit": "fit",
        "wear_style": "body_part",
        "occasion": "occasion",
        "season": "season",
        "attributes": "attribute",
    }

    # 处理简单列表型标签（风格、版型、场合等）
    for key, category in category_map.items():
        values = data.get(key, [])
        for value in values:
            extracted = _extract_tag_names(value)
            for name in extracted:
                name = normalize_tag_name(name)
                if name:
                    tag = await get_or_create_tag(db, name, category)
                    await _link_tag(db, inspiration_id, tag.id, confidence=0.8)

    # 处理结构化单品标签
    items = data.get("items", [])
    for item in items:
        if isinstance(item, dict):
            item_type = normalize_tag_name(str(item.get("type", "")).strip())
            color_raw = str(item.get("color", "")).strip()
            # 将 hex 颜色值转换为中文颜色名
            color = _normalize_color(color_raw)
            features = item.get("features", [])

            if item_type:
                tag = await get_or_create_tag(db, item_type, "item_type")
                await _link_tag(db, inspiration_id, tag.id, confidence=0.8)

            if color:
                tag = await get_or_create_tag(db, color, "color")
                await _link_tag(db, inspiration_id, tag.id, confidence=0.85)

            for feat in features:
                if isinstance(feat, str):
                    feat_name = normalize_tag_name(feat.strip())
                    if feat_name:
                        tag = await get_or_create_tag(db, feat_name, "body_part")
                        await _link_tag(db, inspiration_id, tag.id, confidence=0.7)
                elif isinstance(feat, dict):
                    for fv in _extract_tag_names(feat):
                        fv = normalize_tag_name(fv)
                        if fv:
                            tag = await get_or_create_tag(db, fv, "body_part")
                            await _link_tag(db, inspiration_id, tag.id, confidence=0.7)


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
        # 跳过明显不是标签的字符串
        if not s or len(s) <= 1:
            return []
        # 跳过 JSON/dict 结构字符串（被错误 str() 化的情况）
        if s.startswith("{") and s.endswith("}"):
            return []
        # 跳过过长字符串（很可能是描述文本而非标签）
        if len(s) > 10:
            return []
        # 跳过包含逗号、句号的描述文本
        if '，' in s or '。' in s or ',' in s:
            return []
        # 斜杠分隔的值拆分为多个独立标签
        if '/' in s:
            parts = [p.strip() for p in s.split('/') if p.strip()]
            results = []
            for p in parts:
                results.extend(_extract_tag_names(p))
            return results
        # 跳过纯数字
        if s.isdigit():
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
        for key in ("图片属性", "属性值", "适合场合", "适合季节", "穿着方式",
                     "穿着方式/身体部位关系"):
            v = value.get(key)
            if v is not None:
                results.extend(_extract_tag_names(v))

        # 第4轮：兜底 — 遍历所有值
        # 处理 {"宽松/修身": "修身"} 或 {"上衣": "黑色长袖"} 等非标准键
        if not results:
            for k, v in value.items():
                if isinstance(k, str) and not any('一' <= c <= '鿿' for c in k):
                    if isinstance(v, bool):
                        continue
                if isinstance(v, str) and v.strip():
                    results.append(v.strip())
                elif isinstance(v, list):
                    results.extend(_extract_tag_names(v))
                elif isinstance(v, dict):
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
    """将标签与素材关联，避免重复。置信度更高时更新。"""
    from sqlalchemy import select
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
    else:
        link = InspirationTag(
            inspiration_id=inspiration_id,
            tag_id=tag_id,
            confidence=confidence,
        )
        db.add(link)

    await db.flush()
