"""腿部姿态新词表/门控/高风险验证的纯函数测试。"""

from app.services.ai_service.leg_posture_verify import (
    _parse_verdict,
    filter_leg_posture_by_verdict,
)
from app.services.ai_tag_saver import iter_extracted_tags
from app.utils.leg_posture import normalize_leg_posture


def _names(data: dict, category: str = "leg_posture") -> list[str]:
    return [name for name, cat, _conf in iter_extracted_tags(data) if cat == category]


def test_default_standing_no_longer_generates_plain_binglong():
    data = {"attributes": ["全身", "站姿"], "leg_posture": ["并拢"]}
    assert _names(data) == ["双腿并拢"]


def test_half_body_visibility_gate_drops_all_leg_tags():
    data = {"attributes": ["半身", "站姿"], "leg_posture": ["并拢", "跷腿"]}
    assert _names(data) == []


def test_new_vocabulary_maps_legacy_synonyms():
    data = {
        "attributes": ["全身", "坐姿"],
        "Leg_Posture": ["跷腿", "交叉腿"],
    }
    # 多个关系词无法判定主次：保守清空（二次裁剪可兜底）
    assert _names(data) == []

    data = {"Leg_Posture": ["交叉腿"]}
    assert _names(data) == ["双腿交叉"]


def test_together_conflicts_with_other_relation():
    data = {"attributes": ["全身"], "leg_posture": ["双腿并拢", "双腿分开"]}
    assert _names(data) == ["双腿分开"]


def test_together_requires_full_body():
    data = {"attributes": ["坐姿"], "leg_posture": ["双腿并拢"]}
    assert _names(data) == []


def test_attribute_aliases_and_full_body_whitelist():
    assert normalize_leg_posture(["双腿并拢"], ["全身照"]) == ["双腿并拢"]
    assert normalize_leg_posture(["双腿并拢"], ["半身照"]) == []
    assert normalize_leg_posture(["踮脚"], []) == []
    assert normalize_leg_posture(["踮脚"], ["全身"]) == ["踮脚"]
    assert normalize_leg_posture(["单腿抬起"], ["半身照"]) == []
    assert normalize_leg_posture(["脚踝交叉"], ["七分身"]) == []
    assert normalize_leg_posture(["屈膝"], []) == ["屈膝"]


def test_string_and_whitespace_inputs_are_safe():
    assert normalize_leg_posture("并拢", " 全身 ") == ["双腿并拢"]
    assert normalize_leg_posture(" 并拢 ", None) == []
    assert normalize_leg_posture(["  "], ["全身"]) == []
    assert normalize_leg_posture([None, 1, "并拢"], ["全身"]) == ["双腿并拢"]


def test_new_aliases_and_identity_removed():
    assert normalize_leg_posture(["翘二郎腿"], ["全身"]) == ["二郎腿"]
    assert normalize_leg_posture(["双脚交叉"], ["全身"]) == ["脚踝交叉"]
    assert normalize_leg_posture(["脚尖点地"], ["全身"]) == ["踮脚"]
    assert normalize_leg_posture(["岔开腿"], ["全身"]) == ["双腿分开"]
    # 单腿伸展语义不明确，不再错误归一成单腿抬起
    assert normalize_leg_posture(["单腿伸展"], ["全身"]) == []


def test_multiple_relations_and_conflict_are_conservative():
    assert normalize_leg_posture(["并拢", "屈膝"], ["全身"]) == ["屈膝"]
    assert normalize_leg_posture(["二郎腿", "双腿交叉"], ["全身"]) == []
    assert normalize_leg_posture(["跷腿", "二郎腿"], ["全身"]) == ["二郎腿"]


def test_verify_parser_and_filter():
    assert _parse_verdict(
        '{"legs_visible":"full","leg_relation":"together","is_default_standing":false}'
    ) == {
        "legs_visible": "full",
        "leg_relation": "together",
        "is_default_standing": False,
    }
    assert _parse_verdict("no json") is None

    data = {"leg_posture": ["双腿并拢", "二郎腿"]}
    filtered = filter_leg_posture_by_verdict(
        data,
        {"legs_visible": "full", "leg_relation": "together", "is_default_standing": True},
    )
    assert filtered["leg_posture"] == ["二郎腿"]

    filtered = filter_leg_posture_by_verdict(
        data,
        {"legs_visible": "full", "leg_relation": "together", "is_default_standing": False},
    )
    assert filtered["leg_posture"] == ["双腿并拢", "二郎腿"]


def test_filter_handles_both_leg_posture_keys():
    """大小写两个键同时存在时必须都过滤，否则未处理的那个键会让标签复活。"""
    data = {"leg_posture": [], "Leg_Posture": ["双腿并拢"]}
    filtered = filter_leg_posture_by_verdict(
        data,
        {"legs_visible": "full", "leg_relation": "together", "is_default_standing": True},
    )
    assert filtered["leg_posture"] == []
    assert filtered["Leg_Posture"] == []
    # 落库前的统一抽取不应再有「双腿并拢」
    assert _names(filtered) == []
