"""ai_parser 纯函数单测：畸形 JSON 修复、标签提取、截断判断。"""

from app.services.ai_parser import (
    extract_tag_names,
    fix_python_sets,
    is_empty_tag_payload,
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


class TestIsEmptyTagPayload:
    """「合法但全空」判定：模型对没有穿搭内容的图片会正常返回全空 JSON。

    这条判定决定了两件事：这类结果**不算分析失败**（否则素材永远挂在未分析里），
    以及批量分析是否物理删除该图片。误判成 False 会让非穿搭图片赖在库里，
    误判成 True 会删掉真素材——所以边界要逐条钉住。
    """

    def test_all_empty_lists_is_empty(self):
        assert is_empty_tag_payload(
            {
                "style": [],
                "items": [],
                "fit": [],
                "dominant_colors": [],
            }
        )

    def test_any_non_empty_list_is_not_empty(self):
        assert not is_empty_tag_payload({"style": [], "items": ["阔腿裤"]})
        assert not is_empty_tag_payload({"dominant_colors": ["#ffffff"]})

    def test_mixed_scalar_values(self):
        """空串/None/0 都算空；非空字符串算有内容。"""
        assert is_empty_tag_payload({"negative": None, "note": "", "count": 0})
        assert not is_empty_tag_payload({"note": "有内容"})

    def test_unparsed_payload_is_not_empty(self):
        """解析失败（空 dict / None / 非 dict）不算「合法但全空」——仍按失败处理。"""
        assert not is_empty_tag_payload({})
        assert not is_empty_tag_payload(None)  # type: ignore[arg-type]
        assert not is_empty_tag_payload(["a"])  # type: ignore[arg-type]
