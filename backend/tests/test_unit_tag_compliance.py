"""tag_compliance 公共规则单测：四类裸词判定与不合规原因分类。

该模块是「打标落库过滤」与「存量标签治理扫描」共用的口径来源，规则一旦被
误改会同时影响两处，故单独锁边界（回归集 test_tag_rule_regression.py 用真实
模型响应覆盖端到端行为，本文件覆盖规则本身的边界）。
"""

import pytest

from app.utils.tag_compliance import (
    NONCOMPLIANT_REASONS,
    classify_noncompliant,
    is_bare_garment_word,
    is_bare_hosiery_word,
    is_bare_shoe_word,
    is_bare_skirt_word,
    is_lengthless_silk_word,
)


@pytest.mark.parametrize("name", ["丝袜", "过膝袜", "吊带袜", "连裤袜", "袜套", "堆堆袜"])
def test_bare_hosiery_hit(name: str) -> None:
    """无颜色袜类词判为裸词。"""
    assert is_bare_hosiery_word(name)


@pytest.mark.parametrize(
    "name", ["黑色丝袜", "黑色过膝袜", "黑色连裤袜", "白丝", "黑丝", "白色堆堆袜"]
)
def test_bare_hosiery_miss(name: str) -> None:
    """带颜色或非袜类后缀（白丝/黑丝）不判为袜类裸词。"""
    assert not is_bare_hosiery_word(name)


@pytest.mark.parametrize(
    "name", ["黑色丝袜", "肉色半透明肤色丝袜", "哑光肤色丝袜", "白色肉色丝袜"]
)
def test_lengthless_silk_hit(name: str) -> None:
    """以「丝袜」结尾但无长度维度 → 缺长度泛称。"""
    assert is_lengthless_silk_word(name)


@pytest.mark.parametrize(
    "name", ["黑色连裤丝袜", "黑色透肉连裤袜", "肉色过膝丝袜", "黑丝", "白丝"]
)
def test_lengthless_silk_miss(name: str) -> None:
    """写明长度维度（或非「丝袜」结尾）不判为缺长度泛称。"""
    assert not is_lengthless_silk_word(name)


@pytest.mark.parametrize("name", ["短裙", "迷你短裙", "超短裙", "迷你裙"])
def test_bare_skirt_hit(name: str) -> None:
    """仅由长度词 + 裙构成 → 裸裙词。"""
    assert is_bare_skirt_word(name)


@pytest.mark.parametrize(
    "name",
    [
        "长裙", "半身裙", "连衣裙", "格纹百褶短裙", "黑色百褶短裙",
        "高腰包臀短裙", "A字短裙", "藏青格纹百褶短裙",
    ],
)
def test_bare_skirt_miss(name: str) -> None:
    """含颜色/款式/图案修饰或非长度裸词的裙装名保留。"""
    assert not is_bare_skirt_word(name)


@pytest.mark.parametrize(
    "name",
    [
        "高跟鞋", "凉鞋", "高跟凉鞋", "乐福鞋", "牛津鞋", "德比鞋",
        "马丁靴", "高筒靴", "骑士靴", "凉拖",
    ],
)
def test_bare_shoe_hit(name: str) -> None:
    """整名恰为固有鞋靴品类名 → 裸鞋词（含无颜色的跟型组合词如「高跟凉鞋」）。"""
    assert is_bare_shoe_word(name)


@pytest.mark.parametrize(
    "name",
    [
        "黑色高跟鞋", "尖头细跟高跟鞋", "黑色乐福鞋", "黑色牛津鞋",
        "小白鞋", "黑色尖头细跟高跟靴", "黑色高跟凉鞋",
        "豹纹尖头细跟高跟凉鞋", "尖头细跟高跟凉鞋",
    ],
)
def test_bare_shoe_miss(name: str) -> None:
    """带颜色/款式修饰的整名不判为裸鞋词。"""
    assert not is_bare_shoe_word(name)


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("丝袜", "bare_hosiery"),
        ("黑色丝袜", "lengthless_silk"),
        ("短裙", "bare_skirt"),
        ("高跟鞋", "bare_shoe"),
    ],
)
def test_classify_noncompliant(name: str, reason: str) -> None:
    """原因码分类稳定（同一名称恒为同一原因），且都在说明表中登记。"""
    assert classify_noncompliant(name) == reason
    assert reason in NONCOMPLIANT_REASONS


@pytest.mark.parametrize(
    "name",
    ["黑色过膝袜", "黑色连裤丝袜", "黑色百褶短裙", "尖头细跟高跟鞋", "针织衫"],
)
def test_classify_noncompliant_none(name: str) -> None:
    """合规标签返回 None。"""
    assert classify_noncompliant(name) is None
    assert not is_bare_garment_word(name)


@pytest.mark.parametrize(
    "name", ["丝袜", "黑色丝袜", "短裙", "高跟鞋"]
)
def test_is_bare_garment_word_union(name: str) -> None:
    """组合判定：四类任一命中即为需丢弃的裸词。"""
    assert is_bare_garment_word(name)
