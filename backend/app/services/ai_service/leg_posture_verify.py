"""高风险腿部姿态二次验证：下半身裁剪 + 定向提问。

审计结论：泛化的 `并拢` 最容易被模型按“常见站姿”联想出来。``双腿并拢``
现已改为**仅人工维护**、不再由 AI prompt 自动输出；本验证保留用于历史数据
/人工导入的兼容兜底：

- 先用规则门控确认「当前标签最终只会是 `双腿并拢`」，避免白花一次 VLM 调用；
- 只有双腿/双脚完整可见、确实主动靠拢时才保留；
- 半身/局部、关系不符、普通站姿 → 删除；
- 裁剪、调用、解析失败 → 也删除该高风险标签（与 leg_posture.py 的保守丢弃一致），
  但不阻断整条分析。

其他关系词（二郎腿/交叉/分开/抬腿/踮脚/屈膝）不触发二次验证。
"""

from __future__ import annotations

import json
from typing import Any

from app.services.ai_service.analyze import _call_ollama_vision
from app.services.ai_service.common import logger
from app.services.ai_service.sock_verify import _crop_lower_body
from app.utils.leg_posture import (
    LEGACY_ALIASES,
    TOGETHER_TAG as LEG_POSTURE_TAG,
    normalize_leg_posture,
)

_LEG_KEYS = ("leg_posture", "Leg_Posture")
_CONFIDENCE_MIN = 0.6
# 允许裁剪验证保留的关系值；不再使用 is_default_standing 这个重复字段
_VALID_RELATIONS = {
    "none",
    "together",
    "crossed",
    "apart",
    "one_leg_raised",
    "tiptoe",
    "bent",
    "other",
}

VERIFY_PROMPT = (
    "这是同一套穿搭的腿部放大裁剪图。只根据图中可见证据输出一个 JSON 对象，不要其他文字：\n"
    '{"legs_visible":"none|partial|full",'
    '"leg_relation":"none|together|crossed|apart|one_leg_raised|tiptoe|bent|other",'
    '"confidence":0.0}\n'
    "规则：\n"
    "- 半身/腰部以上、看不到腿：legs_visible=none；只能看到大腿或膝盖以上：partial；双腿双脚完整可见：full。\n"
    "- 两腿之间有明确间隙：leg_relation=apart。\n"
    "- 交叉腿/二郎腿：leg_relation=crossed。\n"
    "- 一条腿离地/抬起：leg_relation=one_leg_raised。\n"
    "- 双脚或双膝主动明显靠拢、且不是普通站直：leg_relation=together。\n"
    "- 普通站立、双脚自然靠近但没有主动并拢：leg_relation=none。\n"
    "- 看不清/不确定：confidence 给低值，不要猜 together。"
)


def _as_leg_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _leg_values(tags_data: dict) -> list:
    """同时取出 snake_case/PascalCase 两个键，避免只看第一个非空键。"""
    if not isinstance(tags_data, dict):
        return []
    values: list = []
    for key in _LEG_KEYS:
        values.extend(_as_leg_list(tags_data.get(key)))
    return values


def _is_together(value: Any) -> bool:
    """按归一化口径判断，兼容历史 `并拢`，而不是只匹配 `双腿并拢`。"""
    text = str(value).strip()
    return text == LEG_POSTURE_TAG or LEGACY_ALIASES.get(text) == LEG_POSTURE_TAG


def has_high_risk_leg_tag(tags_data: dict) -> bool:
    """是否含需要裁剪验证的高风险并拢标签（兼容两个键与历史 `并拢`）。"""
    return any(_is_together(value) for value in _leg_values(tags_data))


def _load_json_object(raw: str | None) -> dict | None:
    """从文本中找第一个可解析的 JSON 对象，避免贪婪正则在多对象/思考文本上失败。"""
    text = raw or ""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text[index:])
        except (ValueError, TypeError) as exc:
            logger.debug("腿部姿态验证跳过不可解析的 JSON 片段: %s", exc)
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _parse_bool(value: Any) -> bool | None:
    """兼容 bool/0/1/"true"/"false"；不再把字符串形式的布尔值当非法。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "是"}:
            return True
        if lowered in {"false", "0", "no", "否"}:
            return False
    return None


def _parse_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        try:
            return max(0.0, min(1.0, float(value.strip())))
        except ValueError as exc:
            logger.debug("腿部姿态验证 confidence 非法: %s", exc)
            return None
    return None


def _parse_verdict(raw: str | None) -> dict | None:
    """解析验证 JSON；非法返回 None，由调用方按保守口径丢弃高风险标签。"""
    obj = _load_json_object(raw)
    if obj is None:
        return None
    visible = str(obj.get("legs_visible", "")).strip()
    relation = str(obj.get("leg_relation", "")).strip()
    if visible not in {"none", "partial", "full"}:
        return None
    if relation not in _VALID_RELATIONS:
        return None
    confidence = _parse_confidence(obj.get("confidence"))
    if confidence is None:
        return None
    # 兼容旧验证字段；字段已从 Prompt 移除，但历史日志可能仍带。
    default = obj.get("is_default_standing")
    if isinstance(default, str):
        parsed_default = _parse_bool(default)
    else:
        parsed_default = _parse_bool(default) if default is not None else None
    return {
        "legs_visible": visible,
        "leg_relation": relation,
        "is_default_standing": parsed_default,
        "confidence": confidence,
    }


def _copy_leg_values(tags_data: dict) -> dict:
    """浅拷贝 dict，但把腿部列表也复制，避免调用方修改影响入参。"""
    result = dict(tags_data)
    for key in _LEG_KEYS:
        if key in result:
            result[key] = list(_as_leg_list(result[key]))
    return result


def _remove_together(tags_data: dict) -> dict:
    """删除两个键中的全部并拢写法，返回副本。"""
    result = _copy_leg_values(tags_data)
    for key in _LEG_KEYS:
        if key not in result:
            continue
        values = _as_leg_list(result[key])
        result[key] = [value for value in values if not _is_together(value)]
    return result


def filter_leg_posture_by_verdict(tags_data: dict, verdict: dict) -> dict:
    """按二次验证删除不成立的双腿并拢；其余标签不动。"""
    confidence = verdict.get("confidence", 0.0)
    keep = (
        verdict.get("legs_visible") == "full"
        and verdict.get("leg_relation") == "together"
        and verdict.get("is_default_standing") is not True
        and isinstance(confidence, (int, float))
        and confidence >= _CONFIDENCE_MIN
    )
    return _copy_leg_values(tags_data) if keep else _remove_together(tags_data)


async def maybe_verify_leg_posture(
    file_path: str,
    tags_data: dict,
    model_cfg: dict,
    model_name: str,
    inspiration_id: str,
) -> dict:
    """对高风险 `双腿并拢` 做裁剪二次验证；任何失败都丢弃该标签。"""
    if not isinstance(tags_data, dict):
        return tags_data

    # 先跑规则门控：归一化后不是恰好 `双腿并拢` 时，后续不会落该标签，不必调用 VLM。
    normalized = normalize_leg_posture(
        _leg_values(tags_data),
        tags_data.get("attributes"),
    )
    if normalized != [LEG_POSTURE_TAG]:
        return tags_data

    try:
        crop_data = await _crop_lower_body(file_path)
    except Exception as exc:
        logger.warning(
            "腿部姿态验证裁剪失败，丢弃高风险标签 %s: %s",
            inspiration_id,
            exc,
            exc_info=True,
        )
        return _remove_together(tags_data)

    try:
        raw = await _call_ollama_vision(
            crop_data,
            VERIFY_PROMPT,
            model_cfg,
            file_size_mb=0.0,
            inspiration_id=f"{inspiration_id}#leg",
            model_name=model_name,
        )
    except Exception as exc:
        logger.warning(
            "腿部姿态验证调用失败，丢弃高风险标签 %s: %s",
            inspiration_id,
            exc,
            exc_info=True,
        )
        return _remove_together(tags_data)

    verdict = _parse_verdict(raw)
    if verdict is None:
        logger.warning(
            "腿部姿态验证无有效判定，丢弃高风险标签 %s: %s",
            inspiration_id,
            (raw or "")[:120],
        )
        return _remove_together(tags_data)

    original_has_together = has_high_risk_leg_tag(tags_data)
    result = filter_leg_posture_by_verdict(tags_data, verdict)
    removed = original_has_together and not has_high_risk_leg_tag(result)
    logger.info(
        "腿部姿态二次验证 %s: visible=%s relation=%s confidence=%s removed_together=%s",
        inspiration_id,
        verdict["legs_visible"],
        verdict["leg_relation"],
        verdict["confidence"],
        removed,
    )
    return result
