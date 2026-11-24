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
