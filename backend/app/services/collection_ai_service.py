"""合集 AI 整理预览（P2，2026-11-20）：规则分组 + Ollama 起名。

职责（只预览、**不写库**）：
- 采样合集最多 80 条素材，读取标题（caption/文件名）/作者/标签/主色/评分/来源；
- 规则分组：按标签词频取最多 5 个主题标签，成员为包含该标签的素材（可重叠），
  每个分组最多取 12 个 item_ids；没有标签时按主色/评分兜底 1 组；
- 调用本地 Ollama ``/api/chat``（settings.ollama_base_url + settings.ollama_vision_model，
  think=false、temperature=0.3）让 AI 生成：合集摘要、3 个名称建议、命名理由、
  5~8 个主题标签、每个规则分组的中文主题名；
- AI 返回 JSON 解析失败不报错：分组名回退规则 tag，其余字段用规则兜底文案；
  HTTP/超时等调用失败返回 503（前端提示「AI 服务暂不可用」）。

前端用现有 PATCH 接口应用名称/描述；本模块不落库、不新增表。
"""

import json
import logging
import re
from collections import Counter
from pathlib import Path

import httpx
from fastapi import HTTPException

from app.config import settings
from app.services import collection_service
from app.utils.time import format_utc, utcnow

logger = logging.getLogger(__name__)

# AI 采样上限（任务口径：最多 80 条）与每组展示上限
AI_SAMPLE_LIMIT = 80
AI_MAX_THEME_TAGS = 5
AI_MAX_GROUP_ITEMS = 12
AI_MAX_THEME_TAGS_OUTPUT = 8
AI_MIN_THEME_TAGS_OUTPUT = 5
# AI 调用超时（秒）：本地 8B 模型 + 80 条文本提示，给足时间；失败/超时统一 503
AI_TIMEOUT_SECONDS = 120.0


def _tags_of(item) -> list[str]:
    """素材标签名列表（关联未预加载/空时返回空列表）。"""
    names = []
    for link in getattr(item, "tags", []) or []:
        tag = getattr(link, "tag", None)
        if tag is not None and tag.name:
            names.append(tag.name)
    return names


def _title_of(item) -> str:
    """素材标题：caption 优先，其次文件名（去掉扩展名）。"""
    caption = (getattr(item, "caption", None) or "").strip()
    if caption:
        return caption
    return Path(item.file_path or "").stem


def _brief(item) -> dict:
    """分组 items 的精简信息（前端预览缩略图用）。"""
    return {
        "id": item.id,
        "thumbnail_path": item.thumbnail_path,
        "source_author": item.source_author,
    }


def _colors_of(item) -> list[str]:
    """素材主色列表：``dominant_colors`` 存的是 JSON 数组字符串（兼容已经是 list 的旧对象）。"""
    raw = getattr(item, "dominant_colors", None)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as exc:
            logger.debug("素材主色 JSON 解析失败，按无颜色处理：%r（%s）", raw[:80], exc)
            return []
        raw = parsed
    if not isinstance(raw, list):
        return []
    return [str(color).strip() for color in raw if str(color).strip()]


def _rule_groups(items: list) -> list[dict]:
    """规则分组：标签词频 top5；无标签按主色/评分兜底 1 组。"""
    tag_counter: Counter = Counter()
    tag_members: dict[str, list] = {}
    for item in items:
        for tag_name in _tags_of(item):
            tag_counter[tag_name] += 1
            tag_members.setdefault(tag_name, []).append(item)

    groups = []
    for tag_name, _count in tag_counter.most_common(AI_MAX_THEME_TAGS):
        members = tag_members[tag_name]
        preview = members[:AI_MAX_GROUP_ITEMS]
        groups.append(
            {
                "tag": tag_name,
                "name": tag_name,  # AI 起名失败时回退 tag
                "count": len(members),
                "item_ids": [item.id for item in preview],
                "items": [_brief(item) for item in preview],
            }
        )
    if groups:
        return groups

    # 兜底：没有标签 → 主色（取每张第一个主色）优先，其次按评分是否已评
    color_counter: Counter = Counter()
    for item in items:
        colors = _colors_of(item)
        if colors:
            color_counter[colors[0]] += 1
    if color_counter:
        tag_name, _ = color_counter.most_common(1)[0]
    else:
        rated = [item for item in items if (item.rating or 0) > 0]
        tag_name = "高评分" if rated else "未评分"
    preview = items[:AI_MAX_GROUP_ITEMS]
    return [
        {
            "tag": tag_name,
            "name": tag_name,
            "count": len(items),
            "item_ids": [item.id for item in preview],
            "items": [_brief(item) for item in preview],
        }
    ]


def _item_prompt_line(index: int, item) -> str:
    """单条素材的提示词行（字段截断，防超长输入）。"""
    tags = "、".join(_tags_of(item))[:80] or "-"
    colors = "、".join(_colors_of(item)[:3])[:40] or "-"
    title = _title_of(item)[:60] or "-"
    author = (item.source_author or "-")[:40]
    return (
        f"{index}. 标题:{title}｜作者:{author}｜标签:{tags}｜主色:{colors}"
        f"｜评分:{item.rating or 0}｜来源:{item.source_type or '-'}"
    )


def _build_prompt(collection, items: list, groups: list[dict]) -> str:
    """构造 Ollama 提示词：要求只输出 JSON（summary/名称/理由/主题标签/分组名）。"""
    item_lines = [_item_prompt_line(index, item) for index, item in enumerate(items, 1)]
    group_lines = [
        f"- {group['tag']}（{group['count']} 条；示例 id：{', '.join(group['item_ids'][:6])}）"
        for group in groups
    ]
    prompt = (
        "你是一个穿搭收藏整理助手。请根据合集内容与规则分组，生成整理预览结果。\n\n"
        f"合集名称：{collection.name}\n"
        f"素材数量：{len(items)} 条（最多采样 80 条，下面为全部采样）\n\n"
        "素材采样：\n"
        + "\n".join(item_lines)
        + "\n\n规则分组（按标签词频；允许重叠）：\n"
        + "\n".join(group_lines)
        + "\n\n要求：\n"
        "1. summary：用 1~2 句中文概括这个合集的穿搭主题/风格；\n"
        "2. name_suggestions：给合集起 3 个中文名称（简洁、可检索）；\n"
        "3. reason：用 1 句话说明这样命名/整理的理由；\n"
        f"4. theme_tags：{AI_MIN_THEME_TAGS_OUTPUT}~{AI_MAX_THEME_TAGS_OUTPUT} 个中文主题标签（按重要性排序）；\n"
        "5. groups：为上面每个规则分组起一个中文主题名，tag 必须原样返回对应分组的 tag。\n\n"
        "只输出 JSON（不要 markdown 代码块），格式：\n"
        '{"summary": "...", "name_suggestions": ["a", "b", "c"], "reason": "...", '
        '"theme_tags": ["...", "..."], "groups": [{"tag": "分组tag", "name": "中文主题名"}]}'
    )
    # 极端长文本的保护（80 条 × 约 150 字仍在预算内；这里再兜一层）
    return prompt[:14000]


def _parse_ai_json(raw: str) -> dict:
    """解析 AI 输出：支持纯 JSON 与 markdown 代码块包裹；失败返回空 dict。"""
    text = (raw or "").strip()
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError):
        matched = re.search(r"\{.*\}", text, re.DOTALL)
        if not matched:
            return {}
        try:
            parsed = json.loads(matched.group())
        except (TypeError, ValueError) as exc:
            logger.debug("AI 输出 JSON 解析失败，按规则兜底：%s", exc)
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_str_list(value) -> list[str]:
    """把 AI 返回的列表清洗成去重、非空、限长的字符串列表。"""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip() if item is not None else ""
        if text and text not in result:
            result.append(text[:40])
    return result


def _fallback_summary(collection, items: list, groups: list[dict]) -> str:
    """AI 未给出 summary 时的规则兜底文案。"""
    themes = [group["tag"] for group in groups if group.get("tag")]
    theme_text = "、".join(themes[:5]) or "未分组"
    return f"合集《{collection.name}》共 {len(items)} 条素材，主要主题：{theme_text}。"


def _apply_ai_result(parsed: dict, collection, items: list, groups: list[dict]) -> dict:
    """把 AI JSON 合并进规则分组结果，缺失/非法字段用规则兜底。"""
    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        summary = _fallback_summary(collection, items, groups)

    name_suggestions = _clean_str_list(parsed.get("name_suggestions"))
    fallback_names = [
        f"{collection.name}精选",
        f"{collection.name}收藏",
        "我的穿搭灵感合集",
    ]
    for name in fallback_names:
        if len(name_suggestions) >= 3:
            break
        if name not in name_suggestions:
            name_suggestions.append(name[:40])

    reason = str(parsed.get("reason") or "").strip() or (
        "按素材标签频次自动分组，并由 AI 生成主题名（仅预览，未写入合集）。"
    )

    # 主题标签：AI 值优先，其次规则 tag，最后通用词补足 5~8 个
    theme_tags = [
        tag
        for tag in _clean_str_list(parsed.get("theme_tags"))
        if tag and len(tag) <= 20
    ]
    for group in groups:
        if len(theme_tags) >= AI_MAX_THEME_TAGS_OUTPUT:
            break
        tag = group.get("tag")
        if tag and tag not in theme_tags:
            theme_tags.append(str(tag)[:20])
    fillers = ["穿搭", "收藏", "灵感", "参考", "整理"]
    for filler in fillers:
        if len(theme_tags) >= AI_MIN_THEME_TAGS_OUTPUT:
            break
        if filler not in theme_tags:
            theme_tags.append(filler)
    theme_tags = theme_tags[:AI_MAX_THEME_TAGS_OUTPUT]

    # AI 分组名：按 tag 映射；缺失/非法回退规则 tag
    ai_group_names: dict[str, str] = {}
    raw_groups = parsed.get("groups")
    if isinstance(raw_groups, list):
        for entry in raw_groups:
            if not isinstance(entry, dict):
                continue
            tag = str(entry.get("tag") or "").strip()
            name = str(entry.get("name") or "").strip()
            if tag and name:
                ai_group_names[tag] = name[:40]
    for group in groups:
        group["name"] = ai_group_names.get(group["tag"]) or group["tag"]

    return {
        "summary": summary[:600],
        "name_suggestions": name_suggestions[:3],
        "reason": reason[:300],
        "theme_tags": theme_tags,
        "groups": groups,
    }


async def _request_ai(prompt: str) -> str:
    """调用本地 Ollama /api/chat（非流式、非思考、temperature=0.3）。

    独立成函数便于测试 monkeypatch；HTTP/超时异常由调用方转 503。
    """
    async with httpx.AsyncClient(timeout=AI_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_vision_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.3, "num_predict": 1024},
            },
        )
        response.raise_for_status()
        return (response.json().get("message", {}).get("content", "") or "").strip()


async def generate_summary(db, collection_id: int) -> dict:
    """生成合集 AI 整理预览（不写库）。

    Raises:
        HTTPException: 404 合集不存在；400 空合集；503 AI 调用失败/超时/空响应。
    """
    collection = await collection_service.get_collection(db, collection_id)
    items, total = await collection_service.list_collection_inspirations(
        db, collection_id, page=1, size=AI_SAMPLE_LIMIT
    )
    if total == 0:
        raise HTTPException(status_code=400, detail="合集为空，无法生成 AI 整理预览")
    items = items[:AI_SAMPLE_LIMIT]
    groups = _rule_groups(items)
    prompt = _build_prompt(collection, items, groups)

    try:
        raw = await _request_ai(prompt)
    except Exception as exc:  # 本地 Ollama 不可达/超时/HTTP 错误 → 503 中文提示
        logger.warning("AI 合集整理调用失败：collection=%s err=%s", collection_id, exc)
        raise HTTPException(
            status_code=503, detail="AI 服务暂不可用，请稍后重试"
        ) from exc
    if not raw:
        raise HTTPException(status_code=503, detail="AI 服务暂不可用，请稍后重试")

    parsed = _parse_ai_json(raw)
    result = _apply_ai_result(parsed, collection, items, groups)
    return {
        **result,
        "model": settings.ollama_vision_model,
        "generated_at": format_utc(utcnow()),
    }
