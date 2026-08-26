"""根据人物标签调用本地大模型生成简介。

输入是该人物的风格画像（``PersonServiceBase.style_profile`` 的返回值），
输出是纯文本简介。模型走 ``settings.ollama_vision_model``（与其他 AI 功能
共用同一个本地 Ollama 实例），但调用 ``/api/chat`` 且关闭 think——这是
纯文本任务，不需要图片，也不需要思考预算。
"""

import logging
import re

import httpx
from pydantic import BaseModel

from app.config import settings
from app.services.model_config import get_model_config
from app.services.person_bio_prompt import get_person_bio_prompt

logger = logging.getLogger(__name__)

# 生成简介的最大输出 token：60~120 字中文 ≈ 200 token，给 512 留余量
_MAX_OUTPUT_TOKENS = 512

# 简介正文长度硬上限（防止模型失控写太长）；下限用于检测空结果
_MAX_BIO_CHARS = 300
_MIN_BIO_CHARS = 4


class PersonBioInputs(BaseModel):
    """生成简介所需的人物上下文。"""

    kind_label: str  # 「穿搭博主」或「职业模特」
    name: str
    platform_label: str  # 「小红书」/「抖音」/「其他」
    ip_location: str | None = None
    # style_profile 的 top_tags: [{name, category, count}, ...]
    top_tags: list[dict] = []
    # style_profile 的 by_category: {category: count}
    by_category: dict[str, int] = {}


def _format_top_tags(top_tags: list[dict], limit: int = 12) -> str:
    """把 top_tags 拼成顿号分隔的字符串；附带频次让模型抓主次。"""
    items = []
    for t in top_tags[:limit]:
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        count = t.get("count")
        if isinstance(count, int) and count > 1:
            items.append(f"{name}×{count}")
        else:
            items.append(name)
    return "、".join(items) if items else "（暂无标签）"


def _format_category_summary(by_category: dict[str, int]) -> str:
    """把类别聚合成「上装 12、下装 8、配饰 3」风格的短句。"""
    ordered = sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)
    parts = [f"{cat} {cnt}" for cat, cnt in ordered[:8] if cat]
    return "；".join(parts) if parts else "（暂无类别数据）"


def _clean_model_output(raw: str) -> str:
    """清理模型输出：去包裹引号、去 Markdown 标题/列表前缀、压缩空白。"""
    if not raw:
        return ""
    text = raw.strip()
    # 去掉代码块包裹
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 去掉整段首尾的中英文引号
    text = text.strip().strip('"').strip("“").strip("”").strip()
    # 去掉行首的 Markdown 列表 / 标题符号（模型偶尔会输出 "- 简介..."）
    text = re.sub(r"^[-*#>\s]+", "", text)
    # 压缩多余空白，但保留单个换行
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class BioGenerationError(RuntimeError):
    """简介生成失败（模型不可达 / 返回空 / 返回异常长）。"""


async def generate_person_bio(inputs: PersonBioInputs) -> str:
    """根据标签上下文调用本地 Ollama 生成一段人物简介。

    抛出 ``BioGenerationError`` 表示无法生成；调用方负责把错误消息透传给前端。
    """
    template = get_person_bio_prompt()

    fields = {
        "kind": inputs.kind_label,
        "name": inputs.name,
        "platform": inputs.platform_label or "其他",
        "ip_location": inputs.ip_location or "未知",
        "top_tags": _format_top_tags(inputs.top_tags),
        "category_summary": _format_category_summary(inputs.by_category),
    }

    try:
        prompt = template.format(**fields)
    except KeyError as e:
        raise BioGenerationError(
            f"Prompt 模板包含未知占位符 {e}，请在「AI 设置」中修正模板"
        ) from e

    model_name = settings.ollama_vision_model
    model_cfg = get_model_config(model_name)

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.6,
            "top_p": 0.85,
            "num_predict": _MAX_OUTPUT_TOKENS,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=model_cfg["timeout"]) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/chat",
                json=payload,
            )
            response.raise_for_status()
            raw = response.json().get("message", {}).get("content", "") or ""
    except httpx.ConnectError as e:
        raise BioGenerationError(
            f"无法连接本地 Ollama（{settings.ollama_base_url}），请确认 Ollama 已启动"
        ) from e
    except httpx.HTTPStatusError as e:
        logger.warning("生成简介 HTTP 失败: %s", e)
        raise BioGenerationError(f"Ollama 返回错误状态 {e.response.status_code}") from e
    except Exception as e:
        logger.warning("生成简介失败: %s", e)
        raise BioGenerationError(f"调用本地模型失败：{e}") from e

    bio = _clean_model_output(raw)

    if len(bio) < _MIN_BIO_CHARS:
        raise BioGenerationError("模型返回的简介为空或过短，请重试或调整 Prompt")
    if len(bio) > _MAX_BIO_CHARS:
        # 硬截断到最近的句号/换行，避免半句
        truncated = bio[:_MAX_BIO_CHARS]
        m = re.search(r"[。！？\n]", truncated[::-1])
        if m:
            cut = _MAX_BIO_CHARS - m.start()
            truncated = truncated[:cut]
        bio = truncated.rstrip("，,、；;：: ")

    return bio
