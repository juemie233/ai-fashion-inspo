"""tag_normalizer 纯函数单测：同义词归一化、相似度、标签名校验。"""

from app.utils.tag_normalizer import (
    normalize_tag_name,
    string_similarity,
    validate_tag_name,
)


def test_synonym_normalization():
    assert normalize_tag_name("纯白") == "白色"
    assert normalize_tag_name("奶白") == "白色"
    assert normalize_tag_name("jk") == "JK制服"
    assert normalize_tag_name("JK") == "JK制服"
    assert normalize_tag_name("藏青") == "海军蓝"
    assert normalize_tag_name("对镜拍") == "对镜自拍"


def test_unknown_name_passthrough():
    assert normalize_tag_name("法式") == "法式"
    assert normalize_tag_name("不存在的标签") == "不存在的标签"


def test_string_similarity():
    assert string_similarity("白色", "白色") == 1.0
    assert string_similarity("白色", "黑色") < 1.0
    assert 0.0 <= string_similarity("a", "b") <= 1.0


def test_string_similarity_fallback_without_rapidfuzz(monkeypatch):
    """rapidfuzz 缺失时回退 difflib 仍可计算（最小环境不挂测试）。"""
    import sys

    # 把 rapidfuzz 伪装为缺失：sys.modules 中为 None 时 import 会抛 ImportError
    monkeypatch.setitem(sys.modules, "rapidfuzz", None)
    assert string_similarity("白色", "白色") == 1.0
    assert string_similarity("白色", "黑色") < 1.0
    assert 0.0 <= string_similarity("法式", "法式风") <= 1.0


def test_validate_tag_name_ok():
    assert validate_tag_name("法式") == (True, None)
    assert validate_tag_name(" V领 ") == (True, None)
    assert validate_tag_name("y2k") == (True, None)  # 允许的英文专有名词


def test_validate_tag_name_rejects():
    # 空 / 过长（默认阈值 12 字，13 字过长）
    assert validate_tag_name(" ")[0] is False
    assert validate_tag_name("一二三四五六七八九十甲乙丙")[0] is False
    # 描述句 / 标点 / hex
    assert validate_tag_name("图片中的人物穿着")[0] is False
    assert validate_tag_name("带标点！")[0] is False
    assert validate_tag_name("#FFFFFF")[0] is False
    # 纯英文普通词
    assert validate_tag_name("hello")[0] is False


def test_validate_tag_name_length_boundary():
    """长度阈值可配置：默认 12 字合法、13 字过长；显式 max_length 可覆盖。"""
    # 默认阈值（settings.tag_name_max_length = 12）
    assert validate_tag_name("一二三四五六七八九十甲乙") == (True, None)  # 12 字合法
    assert validate_tag_name("一二三四五六七八九十甲乙丙")[0] is False  # 13 字过长
    # 显式覆盖阈值
    assert validate_tag_name("一二三四五六七八九十甲乙", max_length=10)[0] is False
    assert validate_tag_name("一二三四五六七八九十", max_length=10) == (True, None)
