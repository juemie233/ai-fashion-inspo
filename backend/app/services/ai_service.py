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

        # 调用 Ollama 视觉 API
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
    """将 AI 分析提取的标签保存到数据库。"""
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
            name = normalize_tag_name(str(value).strip())
            if not name:
                continue
            tag = await get_or_create_tag(db, name, category)
            await _link_tag(db, inspiration_id, tag.id, confidence=0.8)

    # 处理结构化单品标签
    items = data.get("items", [])
    for item in items:
        if isinstance(item, dict):
            item_type = normalize_tag_name(item.get("type", "").strip())
            color = normalize_tag_name(item.get("color", "").strip())
            features = item.get("features", [])

            if item_type:
                tag = await get_or_create_tag(db, item_type, "item_type")
                await _link_tag(db, inspiration_id, tag.id, confidence=0.8)

            if color:
                tag = await get_or_create_tag(db, color, "color")
                await _link_tag(db, inspiration_id, tag.id, confidence=0.85)

            for feat in features:
                feat_name = normalize_tag_name(feat.strip())
                if feat_name:
                    tag = await get_or_create_tag(db, feat_name, "body_part")
                    await _link_tag(db, inspiration_id, tag.id, confidence=0.7)


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
