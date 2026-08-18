"""大标签总结：根据素材小标签纯文本调用模型总结穿搭大标签。"""

import json
import re

from app.config import settings
from app.services.ai_service.common import logger
from app.services.model_config import get_model_config


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

    # 大标签总结跟随模型管理的活跃模型；仍强制非思考模式（思考模型会吃光预算返回空）
    summary_model = settings.ollama_vision_model
    model_cfg = get_model_config(summary_model)
    # 输入约束：截断拼接文本（标签名来自模型输出/用户输入，防 prompt 注入与超长输入）
    tag_list = "、".join(small_tags)[:500]
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
