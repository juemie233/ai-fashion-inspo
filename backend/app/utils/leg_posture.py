"""腿部姿态标签的落库前规则（与提示词口径共用一份）。

2026-11-24 审计结论：`并拢` 在全身站姿里绝大多数是默认状态，不是可检索姿态。
因此新口径：
- 不再生成泛化的 `并拢`；
- 半身/七分/腿部不可见 → 不输出任何腿部姿态；
- 只保留少量可验证的关系词；
- 矛盾组合、多个关系词一律保守丢弃，交给高风险裁剪二次验证兜底。

纯函数，不读写数据库；`ai_tag_saver` 在生成标签关联和结构化快照时共用。
"""

from __future__ import annotations

LEG_POSTURE_CATEGORY = "leg_posture"

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
        "双腿并拢",
    }
)

# 历史/近义词 → 新词表；历史 `并拢` 只作为归一化输入，不再作为新输出
LEGACY_ALIASES: dict[str, str] = {
    "并拢": "双腿并拢",
    "跷腿": "二郎腿",
    "翘腿": "二郎腿",
    "交叉腿": "双腿交叉",
    "腿交叉": "双腿交叉",
    "腿部交叉": "双腿交叉",
    "腿交叉站立": "双腿交叉",
    "分开": "双腿分开",
    "分腿": "双腿分开",
    "抬腿": "单腿抬起",
    "单腿抬起": "单腿抬起",
    "单腿伸展": "单腿抬起",
    "单腿抬起腿": "单腿抬起",
    "单脚抬起": "单腿抬起",
    "踮脚": "踮脚",
    "踮起脚尖": "踮脚",
    "屈膝": "屈膝",
    "弯曲": "屈膝",
    "膝盖弯曲": "屈膝",
}

# 只看到这些取景时，腿部姿态不落库（与 Prompt 前置门控一致）
VISIBILITY_BLOCKING_ATTRS = frozenset({"半身", "七分"})
# 允许 `双腿并拢` 的取景；没有全身标记时不自动落库（保守）
FULL_BODY_ATTR = "全身"
# `双腿并拢` 与这些关系互斥
RELATION_CONFLICTS = frozenset(
    {"二郎腿", "双腿交叉", "脚踝交叉", "单腿抬起", "双腿分开", "踮脚", "屈膝"}
)


def _normalized_names(values) -> list[str]:
    """从解析值中取出标签名（兼容 list/str/dict 由调用方先 flatten）。"""
    result: list[str] = []
    for value in values or []:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if not name:
            continue
        name = LEGACY_ALIASES.get(name, name)
        if name in CANONICAL_POSTURES and name not in result:
            result.append(name)
    return result


def normalize_leg_posture(names, attributes) -> list[str]:
    """把模型输出规整为最多一个可落库的腿部姿态。

    Args:
        names: 原始腿部姿态名列表（可含历史近义词）。
        attributes: 图片属性标签集合（如 ``{"全身", "站姿"}``）。

    Returns:
        规整后的标签名列表；无法安全判断时返回空列表。
    """
    attrs = {str(a).strip() for a in (attributes or []) if str(a).strip()}
    if attrs & VISIBILITY_BLOCKING_ATTRS:
        return []

    normalized = _normalized_names(list(names or []))
    if not normalized:
        return []

    # 高门槛：`双腿并拢` 只在全身取景下才允许自动落库
    if "双腿并拢" in normalized and FULL_BODY_ATTR not in attrs:
        normalized = [n for n in normalized if n != "双腿并拢"]

    if "双腿并拢" in normalized and any(n in RELATION_CONFLICTS for n in normalized):
        normalized = [n for n in normalized if n != "双腿并拢"]

    # 只保留一个关系词；多个关系词无法判定主次时保守清空
    if len(normalized) > 1:
        return []
    return normalized
