"""ai_parser 纯函数单测：畸形 JSON 修复、标签提取、截断判断。"""

from app.services.ai_parser import (
    extract_tag_names,
    fix_python_sets,
    looks_truncated,
    parse_analysis_response,
    parse_is_outfit,
    quote_bare_array_words,
    repair_truncated_json,
)


def test_quote_bare_array_words():
    assert quote_bare_array_words("[宽松]") == '["宽松"]'
    assert quote_bare_array_words("[阔腿, 运动风]") == '["阔腿", "运动风"]'
    # 已含引号的不动
    assert quote_bare_array_words('["宽松"]') == '["宽松"]'


def test_fix_python_sets():
    assert fix_python_sets('{"过膝"}') == '"过膝"'
    assert fix_python_sets('{"V领", "短袖"}') == '["V领", "短袖"]'


def test_parse_analysis_response_clean():
    raw = '{"style": ["法式"], "items": [{"type": "连衣裙"}], "dominant_colors": ["#FFFFFF"]}'
    data = parse_analysis_response(raw)
    assert data["style"] == ["法式"]
    assert data["items"][0]["type"] == "连衣裙"


def test_parse_analysis_response_markdown_and_comment():
    raw = '```json\n{"style": ["日系"] /* 注释 */}\n```'
    data = parse_analysis_response(raw)
    assert data["style"] == ["日系"]


def test_parse_analysis_response_python_set():
    raw = '{"style": {"法式", "日系"}, "fit": ["宽松"]}'
    data = parse_analysis_response(raw)
    assert data["style"] == ["法式", "日系"]


def test_parse_analysis_response_garbage_returns_empty():
    assert parse_analysis_response("完全不是 JSON") == {}


def test_repair_truncated_json():
    repaired = repair_truncated_json('{"style": ["法式"], "fit": ["宽')
    assert repaired is not None
    import json

    data = json.loads(repaired)
    assert data["style"] == ["法式"]


def test_looks_truncated():
    assert looks_truncated('{"style": ["法式"]')
    assert not looks_truncated('{"style": ["法式"]}')


def test_parse_is_outfit():
    assert parse_is_outfit(True) is True
    assert parse_is_outfit("是") is True
    assert parse_is_outfit(False) is False
    assert parse_is_outfit("否") is False
    assert parse_is_outfit("maybe") is None
    assert parse_is_outfit(None) is None


def test_extract_tag_names():
    assert extract_tag_names("坐姿") == ["坐姿"]
    assert extract_tag_names(["法式", "日系"]) == ["法式", "日系"]
    # 已知 value 键（type/name/position 等）直接提取
    assert extract_tag_names({"type": "连衣裙"}) == ["连衣裙"]
    assert extract_tag_names({"position": ["沙发"]}) == ["沙发"]
    # 兜底轮：非标准键的所有值递归提取
    assert extract_tag_names({"宽松/修身": "修身"}) == ["修身"]
    assert extract_tag_names("图片中的人物") == []  # 描述句过滤
    assert extract_tag_names("#FFFFFF") == []  # hex 过滤
    assert extract_tag_names(123) == []


def test_extract_tag_names_length_boundary():
    """AI 打标长度过滤与低质命名阈值一致：默认 12 字保留、13 字丢弃。"""
    assert extract_tag_names("一二三四五六七八九十甲乙") == ["一二三四五六七八九十甲乙"]
    assert extract_tag_names("一二三四五六七八九十甲乙丙") == []
