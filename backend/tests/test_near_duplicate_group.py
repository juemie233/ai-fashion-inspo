"""近似重复分组的纯函数单测：分带索引必须与逐簇线性扫描**结果逐字节一致**。

背景：全库扫描原先逐簇线性比较（实测 23,904 张 → 23,672 簇、2.82 亿次比较、24 秒），
改为分带倒排索引后 0.6 秒。索引只允许缩小候选集（鸽巢：距离 ≤ t 的两个哈希必然至少
共享一个带），因此**分组语义不能变**——这组用例就是这条不变量的回归防线。
"""

import json
import random
from datetime import datetime

from app.services.near_duplicate_service import (
    _clusters_to_groups,
    _group,
    _group_banded,
    _group_linear,
)

HASH_BITS = 768
THR = 32


def _item(
    h: int,
    *,
    i: str = "x",
    score: int = 0,
    created: datetime | None = None,
):
    """构造一条分组入参（created_at 用 datetime：format_utc 要求可 strftime）。"""
    return {
        "id": i,
        "file_path": f"images/{i}.jpg",
        "thumbnail_path": None,
        "is_favorite": False,
        "created_at": created or datetime(2026, 1, 1),
        "phash_int": h,
        "size_bytes": 100,
        "score": score,
    }


def _digest(groups: list[dict]) -> str:
    return json.dumps(groups, ensure_ascii=False, sort_keys=True)


def _both(items: list[dict], threshold: int = THR) -> tuple[list[dict], list[dict]]:
    band_count = threshold + 1
    band_width = HASH_BITS // band_count
    m_band, r_band = _group_banded(items, threshold, band_count, band_width)
    m_lin, r_lin = _group_linear(items, threshold)
    return (
        _clusters_to_groups([list(m) for m in m_band], list(r_band)),
        _clusters_to_groups([list(m) for m in m_lin], list(r_lin)),
    )


def _flip(h: int, bits: list[int]) -> int:
    for b in bits:
        h ^= 1 << b
    return h


def test_banded_matches_linear_on_random_library():
    """随机库 + 精确重复 + 近重复：两条路径输出逐字节一致。"""
    rng = random.Random(20260920)
    items = [_item(rng.getrandbits(HASH_BITS), i=f"r{i}") for i in range(200)]

    # 精确重复（同哈希）与近重复（翻转 5 / 20 / 32 位）
    base = items[0]["phash_int"]
    items.append(_item(base, i="dup1"))
    items.append(_item(base, i="dup2"))
    items.append(_item(_flip(base, rng.sample(range(HASH_BITS), 5)), i="near5"))
    items.append(_item(_flip(base, rng.sample(range(HASH_BITS), 20)), i="near20"))
    items.append(_item(_flip(base, rng.sample(range(HASH_BITS), THR)), i="near32"))

    banded, linear = _both(items)
    assert _digest(banded) == _digest(linear)
    assert len(banded) >= 1


def test_threshold_bits_spread_across_all_bands_still_grouped():
    """差异恰好 32 位、且分散在 32 个不同带：鸽巢保证仍至少共享一个带 → 必须同组。"""
    band_count = THR + 1
    band_width = HASH_BITS // band_count
    a = 0
    bits = [b * band_width for b in range(THR)]  # 每个带各翻转 1 位
    b = _flip(a, bits)
    assert sum(((a ^ b) >> i) & 1 for i in range(HASH_BITS)) == THR

    banded, linear = _both([_item(a, i="A"), _item(b, i="B")])
    assert len(banded) == 1 and len(linear) == 1  # 同组
    assert _digest(banded) == _digest(linear)


def test_difference_beyond_threshold_is_not_grouped():
    """差异 33 位（> 阈值）→ 两条路径都不成组。"""
    bits = list(range(33))
    a, b = 0, _flip(0, bits)
    banded, linear = _both([_item(a, i="A"), _item(b, i="B")])
    assert banded == [] and linear == []


def test_difference_only_in_uncovered_bits_still_grouped():
    """差异落在「带未覆盖的高位」（t=32 时 768 位中被覆盖 759 位）→ 共享全部带 → 同组。"""
    band_count = THR + 1
    band_width = HASH_BITS // band_count
    covered = band_count * band_width
    bits = list(range(covered, HASH_BITS))  # 未覆盖的尾部若干位
    assert bits, "t=32 时应有未被带覆盖的位"
    a, b = 0, _flip(0, bits)
    banded, linear = _both([_item(a, i="A"), _item(b, i="B")])
    assert len(banded) == 1 and _digest(banded) == _digest(linear)


def test_first_cluster_in_creation_order_wins():
    """一个素材同时匹配多个簇时，必须并入**先创建**的那个簇（贪心语义）。"""
    a = 0
    # A 与 B 相距 40 位（> 阈值）→ 各自开簇
    b = _flip(a, list(range(1000, 1040)))
    # D 距 A 20 位、距 B 20 位 → 两个簇都在阈值内，应并入先创建的 A 簇
    d = _flip(a, list(range(1000, 1020)))
    assert ((a ^ b).bit_count(), (a ^ d).bit_count(), (b ^ d).bit_count()) == (40, 20, 20)

    items = [_item(a, i="A"), _item(b, i="B"), _item(d, i="D")]
    banded, linear = _both(items)
    assert _digest(banded) == _digest(linear)
    assert len(banded) == 1
    assert {f["id"] for f in banded[0]["files"]} == {"A", "D"}  # D 并入先创建的 A 簇
    assert {f["id"] for f in linear[0]["files"]} == {"A", "D"}


def test_large_threshold_falls_back_to_linear():
    """阈值过大（带宽 < 9）时走线性回退路径：结果与线性一致，且确实没走分带实现。"""
    import app.services.near_duplicate_service as nd

    items = [_item(0, i="A"), _item(_flip(0, [1, 2, 3]), i="B")]
    called = {"banded": False}
    original = nd._group_banded

    def _spy(*args, **kwargs):
        called["banded"] = True
        return original(*args, **kwargs)

    nd._group_banded = _spy
    try:
        groups = nd._group(items, threshold=200)  # 768 // 201 = 3 < _MIN_BAND_WIDTH
    finally:
        nd._group_banded = original

    assert called["banded"] is False
    assert len(groups) == 1  # 距离 3 ≤ 200 → 同组


def test_keeper_and_wasted_bytes_follow_score_and_time():
    """保留建议与可回收空间：评分最高者留，平局取创建更早，再按 id。"""
    h = 0
    items = [
        _item(h, i="low", score=10, created=datetime(2026, 1, 1)),
        _item(h, i="high", score=110, created=datetime(2026, 2, 1)),
        _item(h, i="tie_b", score=110, created=datetime(2026, 1, 15)),
    ]
    groups = _group(items, THR)
    assert len(groups) == 1
    g = groups[0]
    assert g["keeper_id"] == "tie_b"  # 同分取创建更早
    assert g["wasted_bytes"] == 200  # 另两张各 100
    assert [f["distance"] for f in g["files"]] == [0, 0, 0]
    assert g["rep_phash"] == f"{0:0192x}"
