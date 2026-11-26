"""腿部姿态标签的落库前规则（与提示词口径共用一份）。

2026-11-24 审计结论：泛化的 `并拢` 在全身站姿里大多是默认状态，直接作为
默认标签没有检索价值；但业务确认「刻意并拢」仍有检索需求。因此新口径：

- 不再生成泛化的 `并拢`，只保留高门槛的 `双腿并拢`；
- `双腿并拢` 必须同时有 `全身` 取景，且不能和交叉/分开/二郎腿等关系冲突；
- 半身/七分/腿部不可见 → 不输出任何腿部姿态；
- 需要看到脚的关系词（`踮脚`/`脚踝交叉`/`单腿抬起`）同样要求全身；
- 矛盾组合、多个关系词保守丢弃，交给高风险裁剪二次验证兜底。

纯函数，不读写数据库；`ai_tag_saver` 在生成标签关联和结构化快照时共用。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

logger = logging.getLogger(__name__)

LEG_POSTURE_CATEGORY = "leg_posture"
TOGETHER_TAG = "双腿并拢"

# 新词表：只保留有区分度的关系词
CANONICAL_POSTURES = frozenset(
    {
        "二郎腿",
        "双腿交叉",
        "脚踝交叉",
        "单腿抬起",
        "双腿分开",
        "踮脚",
        "屈膝",
        TOGETHER_TAG,
    }
)

# 历史/近义词 → 新词表；历史 `并拢` 只作为归一化输入，不再作为新输出。
# 恒等映射不需要写：规范名本身会被识别。
LEGACY_ALIASES: dict[str, str] = {
    "并拢": TOGETHER_TAG,
    "并膝": TOGETHER_TAG,
    "双膝并拢": TOGETHER_TAG,
    "膝盖并拢": TOGETHER_TAG,
    "跷腿": "二郎腿",
    "翘腿": "二郎腿",
    "翘二郎腿": "二郎腿",
    "跷二郎腿": "二郎腿",
    "叠腿": "二郎腿",
    "交叉腿": "双腿交叉",
    "腿交叉": "双腿交叉",
    "腿部交叉": "双腿交叉",
    "腿交叉站立": "双腿交叉",
    "分开": "双腿分开",
    "分腿": "双腿分开",
    "岔开腿": "双腿分开",
    "双腿张开": "双腿分开",
    "抬腿": "单腿抬起",
    "单脚抬起": "单腿抬起",
    "双脚交叉": "脚踝交叉",
    "脚踝交叠": "脚踝交叉",
    "脚腕交叉": "脚踝交叉",
    "踮起脚尖": "踮脚",
    "脚尖点地": "踮脚",
    "弯曲": "屈膝",
    "膝盖弯曲": "屈膝",
}

# 属性别名：模型/历史数据可能输出「半身照」「七分身」「全身照」等。
ATTRIBUTE_ALIASES: dict[str, str] = {
    "半身照": "半身",
    "上半身": "半身",
    "半身像": "半身",
    "半身图": "半身",
    "七分身": "七分",
    "七分照": "七分",
    "七分像": "七分",
    "全身照": "全身",
    "全身像": "全身",
    "全身图": "全身",
}

# 只看到这些取景时，腿部姿态不落库（与 Prompt 前置门控一致）
VISIBILITY_BLOCKING_ATTRS = frozenset({"半身", "七分"})
FULL_BODY_ATTR = "全身"
# 需要看到完整双腿/双脚才可信的关系词；没有全身属性时统一丢弃。
FULL_BODY_REQUIRED = frozenset({TOGETHER_TAG, "脚踝交叉", "踮脚", "单腿抬起"})
# 这些关系与 `双腿并拢` 同时出现时保守丢弃并拢。
# 注意：这只表示「不被当前规则信任」，不代表姿势逻辑上绝对互斥
# （例如并腿屈膝、并腿踮脚在现实中都成立，当前仍按保守口径处理）。
RELATION_CONFLICTS = frozenset(CANONICAL_POSTURES - {TOGETHER_TAG})


def _as_sequence(value: Iterable[str] | str | None) -> list:
    """把单个字符串/可迭代对象/None 统一成列表；避免字符串被逐字符遍历。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _normalize_attributes(attributes: Iterable[str] | str | None) -> set[str]:
    """归一化属性词（半身照→半身、全身照→全身等）。"""
    result: set[str] = set()
    for raw in _as_sequence(attributes):
        if not isinstance(raw, str):
            continue
        name = raw.strip()
        if not name:
            continue
        result.add(ATTRIBUTE_ALIASES.get(name, name))
    return result


def _normalized_names(values: Iterable[str] | str | None) -> list[str]:
    """提取规范名；只接受字符串元素，未知词/非字符串元素静默跳过并 debug 记录。"""
    result: list[str] = []
    for value in _as_sequence(values):
        if not isinstance(value, str):
            logger.debug("leg posture 忽略非字符串元素: %r", value)
            continue
        raw = value.strip()
        if not raw:
            continue
        name = LEGACY_ALIASES.get(raw, raw)
        if name not in CANONICAL_POSTURES:
            logger.debug("leg posture 未知词已丢弃: %r", raw)
            continue
        if name not in result:
            result.append(name)
    return result


def normalize_leg_posture(
    names: Iterable[str] | str | None,
    attributes: Iterable[str] | str | None,
) -> list[str]:
    """把模型输出规整为最多一个可落库的腿部姿态。

    Args:
        names: 原始腿部姿态名（列表/单个字符串；可含历史近义词）。
        attributes: 图片属性（列表/单个字符串，如 ``{"全身", "站姿"}``）。

    Returns:
        规整后的标签名列表；无法安全判断时返回空列表。
    """
    attrs = _normalize_attributes(attributes)
    if attrs & VISIBILITY_BLOCKING_ATTRS:
        logger.debug("leg posture 半身/七分门控丢弃，attrs=%s", attrs)
        return []

    normalized = _normalized_names(names)
    if not normalized:
        return []

    # 需要完整腿部/脚部的关系词：没有全身属性时统一丢弃
    if FULL_BODY_ATTR not in attrs:
        filtered = [name for name in normalized if name not in FULL_BODY_REQUIRED]
        if len(filtered) != len(normalized):
            logger.debug("leg posture 缺全身属性，丢弃: %s", set(normalized) - set(filtered))
        normalized = filtered
        if not normalized:
            return []

    # `双腿并拢` 与其它关系词同时出现时保守丢弃并拢
    if TOGETHER_TAG in normalized and any(
        name in RELATION_CONFLICTS for name in normalized
    ):
        logger.debug("leg posture 并拢与其他关系冲突，丢弃并拢")
        normalized = [name for name in normalized if name != TOGETHER_TAG]

    # 只保留一个关系词；多个关系词无法判定主次时保守清空
    if len(normalized) > 1:
        logger.debug("leg posture 多个关系词无法判定主次，保守清空: %s", normalized)
        return []
    return normalized
