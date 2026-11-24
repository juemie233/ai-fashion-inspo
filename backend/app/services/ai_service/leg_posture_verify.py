"""高风险腿部姿态二次验证：下半身裁剪 + 定向提问。

审计结论：`并拢` 最容易被模型按“常见站姿”联想出来。新词表里
``双腿并拢`` 是唯一保留的高门槛并拢标签，因此落库前再做一次腿部裁剪验证：
- 只有双腿/双脚完整可见、确实主动靠拢、且不是普通站姿时才保留；
- 半身/局部、关系不符、普通站姿 → 删除该标签；
- 裁剪/调用/解析失败 → 保守保留第一轮结果，不阻断分析。

其他关系词（二郎腿/交叉/分开/抬腿/踮脚/屈膝）不触发二次验证，先由纯规则
过滤，避免每张图都增加一次 VLM 调用。
"""

from __future__ import annotations

import json
import re

from app.services.ai_service.common import logger

LEG_POSTURE_TAG = "双腿并拢"

VERIFY_PROMPT = (
    "这是同一套穿搭的腿部放大裁剪图。只根据图中可见证据输出一个 JSON 对象，不要其他文字：\n"
    '{"legs_visible":"none|partial|full",'
    '"leg_relation":"none|together|crossed|apart|one_leg_raised|tiptoe|bent|other",'
    '"is_default_standing":true,'
    '"confidence":0.0}\n'
    "规则：\n"
    "- 半身/腰部以上、看不到腿：legs_visible=none；只能看到大腿或膝盖以上：partial；双腿双脚完整可见：full。\n"
    "- 两腿之间有明确间隙：apart。\n"
    "- 交叉腿/二郎腿：crossed。\n"
    "- 一条腿离地/抬起：one_leg_raised。\n"
    "- 双脚或双膝主动明显靠拢：together。\n"
    "- 普通站立、双脚自然靠近但没有主动并拢：relation=none 且 is_default_standing=true。\n"
    "- 只有 relation=together 且 is_default_standing=false 才代表有检索价值的并拢姿态。\n"
    "- 看不清/不确定：confidence 给低值。"
)


def has_high_risk_leg_tag(tags_data: dict) -> bool:
    """是否含需要裁剪验证的高风险并拢标签。"""
    if not isinstance(tags_data, dict):
        return False
    values = tags_data.get("leg_posture") or tags_data.get("Leg_Posture") or []
    if not isinstance(values, list):
        values = [values]
    return any(LEG_POSTURE_TAG in str(v) for v in values)


def _parse_verdict(raw: str) -> dict | None:
    """解析验证 JSON；非法返回 None，由调用方保守保留原结果。"""
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except (ValueError, TypeError) as exc:
        logger.debug("腿部姿态验证 JSON 解析失败（保持原结果）：%s", exc)
        return None
    if not isinstance(obj, dict):
        return None
    visible = str(obj.get("legs_visible", "")).strip()
    relation = str(obj.get("leg_relation", "")).strip()
    default = obj.get("is_default_standing")
    if visible not in {"none", "partial", "full"}:
        return None
    if relation not in {
        "none",
        "together",
        "crossed",
        "apart",
        "one_leg_raised",
        "tiptoe",
        "bent",
        "other",
    }:
        return None
    if not isinstance(default, bool):
        return None
    return {
        "legs_visible": visible,
        "leg_relation": relation,
        "is_default_standing": default,
    }


def filter_leg_posture_by_verdict(tags_data: dict, verdict: dict) -> dict:
    """按二次验证删掉不成立的双腿并拢；其余标签不动。"""
    values = tags_data.get("leg_posture") or tags_data.get("Leg_Posture") or []
    if not isinstance(values, list):
        values = [values]
    keep = (
        verdict["legs_visible"] == "full"
        and verdict["leg_relation"] == "together"
        and not verdict["is_default_standing"]
    )
    if keep:
        return tags_data
    filtered = [v for v in values if LEG_POSTURE_TAG not in str(v)]
    result = dict(tags_data)
    if "Leg_Posture" in result and "leg_posture" not in result:
        result["Leg_Posture"] = filtered
    else:
        result["leg_posture"] = filtered
    return result


async def maybe_verify_leg_posture(
    file_path: str,
    tags_data: dict,
    model_cfg: dict,
    model_name: str,
    inspiration_id: str,
) -> dict:
    """对高风险 ``双腿并拢`` 做裁剪二次验证；失败时返回原数据。"""
    if not has_high_risk_leg_tag(tags_data):
        return tags_data

    try:
        from app.services.ai_service.sock_verify import _crop_lower_body

        crop_data = await _crop_lower_body(file_path)
    except Exception as exc:
        logger.warning(f"腿部姿态验证裁剪失败（保持原结果）{inspiration_id}: {exc}")
        return tags_data

    try:
        from app.services.ai_service.analyze import _call_ollama_vision

        raw = await _call_ollama_vision(
            crop_data,
            VERIFY_PROMPT,
            model_cfg,
            0.0,
            f"{inspiration_id}#leg",
            model_name,
        )
    except Exception as exc:
        logger.warning(f"腿部姿态验证调用失败（保持原结果）{inspiration_id}: {exc}")
        return tags_data

    verdict = _parse_verdict(raw)
    if verdict is None:
        logger.info(f"腿部姿态验证无有效判定（保持原结果）{inspiration_id}: {raw[:120]}")
        return tags_data

    original = tags_data.get("leg_posture") or tags_data.get("Leg_Posture") or []
    result = filter_leg_posture_by_verdict(tags_data, verdict)
    filtered = result.get("leg_posture") or result.get("Leg_Posture") or []
    removed = len(filtered) < len(original)
    logger.info(
        f"腿部姿态二次验证 {inspiration_id}: visible={verdict['legs_visible']} "
        f"relation={verdict['leg_relation']} default={verdict['is_default_standing']} "
        f"removed_together={removed}"
    )
    return result
