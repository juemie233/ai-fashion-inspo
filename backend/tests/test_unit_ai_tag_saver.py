"""ai_tag_saver 纯函数单测：标签提取迭代、跨大类近似去重。"""

from app.services.ai_tag_saver import iter_extracted_tags


def _names_by_category(data: dict) -> dict[str, list[str]]:
    """把 iter_extracted_tags 的输出按类别聚合成 {category: [names]}。"""
    result: dict[str, list[str]] = {}
    for name, category, _conf in iter_extracted_tags(data):
        result.setdefault(category, []).append(name)
    return result


def test_atmosphere_dropped_when_similar_to_style():
    """氛围标签与风格标签近似时，只保留风格标签。"""
    data = {
        "style": ["甜美", "法式"],
        "Atmosphere": ["甜美风", "法式感", "浪漫"],
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("style") == ["甜美", "法式"]
    # 甜美风 ≈ 甜美、法式感 ≈ 法式，均被去重；浪漫与风格无关则保留
    assert by_cat.get("atmosphere") == ["浪漫"]


def test_atmosphere_kept_when_no_style_overlap():
    """风格与氛围无近似时，氛围标签正常保留。"""
    data = {
        "style": ["通勤"],
        "Atmosphere": ["浪漫", "优雅"],
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("style") == ["通勤"]
    assert by_cat.get("atmosphere") == ["浪漫", "优雅"]


def test_exact_duplicate_across_style_and_atmosphere():
    """完全相同的标签同时出现在风格和氛围时，氛围侧被丢弃。"""
    data = {
        "style": ["学院风"],
        "Atmosphere": ["学院风"],
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("style") == ["学院风"]
    assert by_cat.get("atmosphere") is None


def test_no_atmosphere_key_is_safe():
    """响应缺少 Atmosphere 字段时不报错，风格标签正常提取。"""
    data = {"style": ["甜美"]}
    by_cat = _names_by_category(data)
    assert by_cat.get("style") == ["甜美"]
    assert by_cat.get("atmosphere") is None


def test_bare_hosiery_type_dropped():
    """袜类 type 无颜色前缀（裸词「丝袜」「过膝袜」）时丢弃 type。"""
    data = {
        "items": [
            {"type": "丝袜", "color": ""},
            {"type": "过膝袜", "color": ""},
            {"type": "黑色丝袜", "color": ""},
            {"type": "黑色过膝袜", "color": ""},
            {"type": "白丝", "color": ""},
        ]
    }
    by_cat = _names_by_category(data)
    # 裸词被丢弃；含颜色前缀的保留
    assert by_cat.get("item_type") == ["黑色丝袜", "黑色过膝袜", "白丝"]


def test_hosiery_color_still_kept_when_type_dropped():
    """type 为裸袜词被丢弃时，同单品的 color 字段仍正常产出 color 标签。"""
    data = {
        "items": [
            {"type": "丝袜", "color": "黑色"},
            {"type": "过膝袜", "color": "白色"},
        ]
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("item_type") is None
    assert by_cat.get("color") == ["黑色", "白色"]


def test_non_hosiery_item_types_unaffected():
    """非袜类单品不受裸词过滤影响。"""
    data = {
        "items": [
            {"type": "针织衫", "color": ""},
            {"type": "西装外套", "color": ""},
            {"type": "JK制服", "color": ""},
        ]
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("item_type") == ["针织衫", "西装外套", "JK制服"]
