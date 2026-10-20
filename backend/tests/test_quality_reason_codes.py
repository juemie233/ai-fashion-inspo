"""原因码归类规则（历史 rejected 原因文本 → quality_reason_code）。

规则必须是**纯函数**且可预期：回填脚本按它批量改写 881 条历史数据，一旦规则含糊，
「按原因筛选」的统计就不可信。
"""

import pytest

from app.services.quality_reason_codes import FALLBACK_CODE, classify_quality_reason


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("初筛器判定为垃圾素材（置信度 0.92）", "prefilter_reject"),
        ("文件缺失", "low_quality"),
        ("图片模糊，看不清", "low_quality"),
        ("纯色占位图", "low_quality"),
        ("广告图，带二维码", "ad"),
        ("仅单品特写，没有整体搭配", "single_item"),
        ("身体被大幅裁切，只有腿", "partial"),
        ("不是穿搭内容", "not_outfit"),
        ("无人物，平铺图", "not_outfit"),
    ],
)
def test_classify_hits_expected_code(text, expected):
    assert classify_quality_reason(text) == expected


def test_classify_order_prefers_more_specific_rule():
    """同时命中多类时按「越具体越先」的顺序：初筛器优先于其它关键词。"""
    assert classify_quality_reason("初筛器判定为垃圾（纯色）") == "prefilter_reject"


def test_classify_empty_returns_fallback():
    """空文本不猜成 not_outfit（避免污染「不是穿搭」的统计）。"""
    assert classify_quality_reason(None) == FALLBACK_CODE
    assert classify_quality_reason("") == FALLBACK_CODE
    assert classify_quality_reason("   ") == FALLBACK_CODE


def test_classify_unknown_text_returns_fallback():
    """无法归类的文本落到 other，便于事后按样本扩充规则。"""
    assert classify_quality_reason("模型认为这条不够有特色") == FALLBACK_CODE


def test_fallback_code_is_valid_enum():
    """兜底值必须属于原因码白名单，否则前端筛选下拉匹配不到。"""
    from app.services.ai_service.quality import REASON_CODES

    assert FALLBACK_CODE in REASON_CODES
