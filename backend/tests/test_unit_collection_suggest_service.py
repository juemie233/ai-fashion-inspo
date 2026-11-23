"""合集「按此扩充」推荐服务（`app/services/collection_suggest_service.py`）补测。

既有 `tests/test_collection_expansion.py` 走接口主路径（向量/标签回退、批次写入、
撤销、特殊合集 400）；这里直接调服务与纯函数，补齐：归一化/质心边界、命中理由
文案、种子与成员口径（手动/半智能）、标签画像与自动合集排除、候选打分（视觉
0.6 + 标签 0.4、标签分封顶）、min_similarity/limit、已有成员与垃圾桶排除、
outfit_only 硬约束、向量异常降级、空合集/无候选等分支。向量库全部 mock。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.collection import Collection, CollectionItem
from app.services import collection_suggest_service as suggest


@pytest.fixture(autouse=True)
def _isolate_vector_store(monkeypatch):
    """默认关闭真实 LanceDB（测试不加载向量库；需要向量路径的用例自行覆盖）。"""
    monkeypatch.setattr(suggest.vector_store, "is_lancedb_available", lambda: False)


# ── 构造助手 ──


def _upload(client, upload) -> str:
    resp = upload()
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_collection(client, name: str, query_json: dict | None = None) -> int:
    body: dict = {"name": name}
    if query_json is not None:
        body["query_json"] = query_json
    resp = client.post("/api/collections", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _tag(client, inspiration_id: str, names: list[str], category: str = "free") -> None:
    resp = client.post(
        f"/api/inspirations/{inspiration_id}/tags",
        json={"names": names, "category": category, "source": "manual"},
    )
    assert resp.status_code == 200, resp.text


def _add_members(client, collection_id: int, inspiration_ids: list[str]) -> dict:
    resp = client.post(
        f"/api/collections/{collection_id}/inspirations",
        json={"inspiration_ids": inspiration_ids},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _raw_collection(
    name: str, *, auto_source: str | None = None
) -> int:
    async with async_session() as db:
        collection = Collection(name=name, auto_source=auto_source)
        db.add(collection)
        await db.commit()
        await db.refresh(collection)
        return collection.id


async def _get_collection(collection_id: int) -> Collection:
    async with async_session() as db:
        collection = await db.get(Collection, collection_id)
        assert collection is not None
        return collection


async def _suggest(collection_id: int, **kwargs) -> dict:
    async with async_session() as db:
        return await suggest.suggest_items(db, collection_id, **kwargs)


# ══════════════════════════════════════════════════════════════
#  纯函数：_normalize / _centroid / _reason
# ══════════════════════════════════════════════════════════════


def test_normalize_returns_unit_vector():
    out = suggest._normalize([3.0, 4.0])
    assert out is not None
    assert out == pytest.approx([0.6, 0.8])
    assert sum(x * x for x in out) ** 0.5 == pytest.approx(1.0)


@pytest.mark.parametrize("vector", [[], [0.0, 0.0], [-0.0, 0.0]])
def test_normalize_zero_vector_returns_none(vector):
    assert suggest._normalize(vector) is None


def test_centroid_requires_min_seed_vectors(monkeypatch):
    monkeypatch.setattr(suggest, "MIN_SEED_VECTORS", 5)
    assert suggest._centroid([]) is None
    # 只有 4 条可用向量 → 不足最小种子数
    assert suggest._centroid([[1.0, 0.0]] * 4) is None
    # 空向量被过滤后同样不够
    assert suggest._centroid([[1.0, 0.0]] * 3 + [[], []]) is None

    monkeypatch.setattr(suggest, "MIN_SEED_VECTORS", 2)
    out = suggest._centroid([[1.0, 0.0], [1.0, 0.0]])
    assert out == pytest.approx([1.0, 0.0])


def test_centroid_rejects_dimension_mismatch():
    assert suggest._centroid([[1.0, 0.0]] * 5 + [[1.0, 0.0, 0.0]]) is None


def test_centroid_zero_mean_returns_none(monkeypatch):
    monkeypatch.setattr(suggest, "MIN_SEED_VECTORS", 2)
    assert suggest._centroid([[1.0, 0.0], [-1.0, 0.0]]) is None


@pytest.mark.parametrize(
    ("visual", "shared_tags", "outfit_tags", "expected"),
    [
        (0.456, [], [], "视觉相似 46%"),
        (None, ["法式", "通勤"], [], "共同标签：法式、通勤"),
        (None, [], ["通勤", "约会"], "同大标签：通勤、约会"),
        (None, ["法式"], ["通勤"], "同大标签：通勤"),  # 大标签优先于共同标签
        (None, [], [], "标签相近"),
        (0.9, ["法式"], ["通勤"], "视觉相似 90% · 同大标签：通勤"),
        (0.333, ["通勤"], [], "视觉相似 33% · 共同标签：通勤"),
    ],
)
def test_reason_variants(visual, shared_tags, outfit_tags, expected):
    assert suggest._reason(visual, shared_tags, outfit_tags) == expected


# ══════════════════════════════════════════════════════════════
#  种子 / 标签画像 / 自动合集成员 / 候选标签
# ══════════════════════════════════════════════════════════════


async def test_seed_and_members_manual_orders_and_excludes_trash(client, upload):
    a = _upload(client, upload)
    b = _upload(client, upload)
    trashed = _upload(client, upload)
    collection_id = _create_collection(client, "种子手动夹")
    client.post(f"/api/inspirations/{trashed}/trash")
    # 直接写成员行以固定 position 顺序；垃圾桶成员用于验证内容口径会过滤
    async with async_session() as db:
        db.add_all(
            [
                CollectionItem(collection_id=collection_id, inspiration_id=a, position=0),
                CollectionItem(collection_id=collection_id, inspiration_id=b, position=1),
                CollectionItem(
                    collection_id=collection_id, inspiration_id=trashed, position=2
                ),
            ]
        )
        await db.commit()

    collection = await _get_collection(collection_id)
    async with async_session() as db:
        seeds, members = await suggest._seed_and_members(db, collection)

    assert seeds == [a, b]
    assert members == {a, b}


async def test_seed_and_members_semi_uses_rule_union(client, upload):
    fav = _upload(client, upload)
    client.patch(f"/api/inspirations/{fav}", json={"is_favorite": True})
    extra = _upload(client, upload)
    collection_id = _create_collection(client, "种子半智能", {"is_favorite": True})
    assert (
        client.post(f"/api/collections/{collection_id}/to-semi").status_code == 200
    )
    _add_members(client, collection_id, [extra])

    collection = await _get_collection(collection_id)
    async with async_session() as db:
        seeds, members = await suggest._seed_and_members(db, collection)

    assert set(seeds) == {fav, extra}
    assert members == {fav, extra}
    # 规则命中的素材也在成员集合里 → 不会被当成候选重复推荐
    assert fav in members


async def test_tag_profile_empty_seeds():
    async with async_session() as db:
        assert await suggest._tag_profile(db, []) == ([], [])


async def test_tag_profile_orders_by_count_then_name_and_limits(client, upload):
    seed = _upload(client, upload)
    _tag(client, seed, ["法式", "通勤"], category="style")
    _tag(client, seed, ["约会"], category="outfit")
    other = _upload(client, upload)
    _tag(client, other, ["法式"], category="style")

    async with async_session() as db:
        profile, outfit_names = await suggest._tag_profile(db, [seed, other])

    names = [name for _tid, name, _cat in profile]
    # 「法式」出现 2 次排第一；其余按 name 升序
    assert names[0] == "法式"
    assert set(names) == {"法式", "通勤", "约会"}
    assert len(profile) <= suggest.TAG_PROFILE_LIMIT
    assert outfit_names == ["约会"]
    assert all(len(row) == 3 for row in profile)


async def test_auto_collection_members_only_auto_source(client, upload):
    normal = _upload(client, upload)
    auto_item = _upload(client, upload)
    manual_collection = _create_collection(client, "普通手动夹")
    _add_members(client, manual_collection, [normal])
    auto_collection = await _raw_collection("抖音自动夹", auto_source="douyin")
    async with async_session() as db:
        db.add(
            CollectionItem(
                collection_id=auto_collection, inspiration_id=auto_item, position=0
            )
        )
        await db.commit()

    async with async_session() as db:
        members = await suggest._auto_collection_members(db)

    assert members == {auto_item}


async def test_candidate_tag_map_empty_and_mapping(client, upload):
    async with async_session() as db:
        assert await suggest._candidate_tag_map(db, []) == {}

    one = _upload(client, upload)
    two = _upload(client, upload)
    _tag(client, one, ["法式"], category="style")
    _tag(client, one, ["约会"], category="outfit")
    _tag(client, two, ["通勤"], category="style")

    async with async_session() as db:
        mapping = await suggest._candidate_tag_map(db, [one, two])

    assert set(mapping[one]) == {("法式", "style"), ("约会", "outfit")}
    assert mapping[two] == [("通勤", "style")]


# ══════════════════════════════════════════════════════════════
#  suggest_items：前置校验 / 空合集 / 标签回退 / 门槛与上限
# ══════════════════════════════════════════════════════════════


async def test_suggest_empty_manual_collection(client):
    collection_id = _create_collection(client, "空手动夹")
    result = await _suggest(collection_id)

    assert result == {
        "items": [],
        "total": 0,
        "profile": {
            "top_tags": [],
            "outfit_tags": [],
            "seeds_used": 0,
            "vector_available": False,
        },
    }


async def test_suggest_rejects_special_collections(client, upload):
    _upload(client, upload)
    # 纯智能合集允许「只读预览」候选（真正写入时才转半智能），不再 400
    pure_smart = _create_collection(client, "纯智能", {"is_favorite": True})
    data = await _suggest(pure_smart)
    assert "items" in data and "profile" in data

    auto_id = await _raw_collection("抖音夹", auto_source="douyin")
    with pytest.raises(HTTPException) as auto_exc:
        await _suggest(auto_id)
    assert auto_exc.value.status_code == 400
    assert "抖音同步" in auto_exc.value.detail

    with pytest.raises(HTTPException) as missing_exc:
        await _suggest(999999)
    assert missing_exc.value.status_code == 404


async def test_suggest_tag_fallback_scoring_reason_and_profile(client, upload):
    seed = _upload(client, upload)
    candidate = _upload(client, upload)
    _tag(client, seed, ["通勤"], category="outfit")
    _tag(client, candidate, ["通勤"], category="outfit")
    collection_id = _create_collection(client, "标签扩充")
    _add_members(client, collection_id, [seed])

    result = await _suggest(collection_id, min_similarity=0.3)

    assert result["total"] == 1
    item = result["items"][0]
    assert item["inspiration"].id == candidate
    assert item["match_source"] == "tag"
    assert item["similarity"] == pytest.approx(round(1 / 3, 4))
    assert item["shared_tags"] == ["通勤"]
    assert item["outfit_tags"] == ["通勤"]
    assert item["reason"] == "同大标签：通勤"  # 大标签命中优先展示
    profile = result["profile"]
    assert profile["top_tags"] == ["通勤"]
    assert profile["outfit_tags"] == ["通勤"]
    assert profile["seeds_used"] == 1
    assert profile["vector_available"] is False


async def test_suggest_min_similarity_threshold(client, upload):
    seed = _upload(client, upload)
    candidate = _upload(client, upload)
    _tag(client, seed, ["通勤"], category="style")
    _tag(client, candidate, ["通勤"], category="style")
    collection_id = _create_collection(client, "门槛扩充")
    _add_members(client, collection_id, [seed])

    # 纯标签命中得分 1/3 ≈ 0.333：门槛 0.34 排除、0.33 保留
    excluded = await _suggest(collection_id, min_similarity=0.34)
    assert excluded["items"] == [] and excluded["total"] == 0

    included = await _suggest(collection_id, min_similarity=0.33)
    assert [i["inspiration"].id for i in included["items"]] == [candidate]


async def test_suggest_tag_score_caps_at_three_shared_tags(client, upload):
    seed = _upload(client, upload)
    candidate = _upload(client, upload)
    _tag(client, seed, ["a", "b", "c", "d"], category="style")
    _tag(client, candidate, ["a", "b", "c", "d"], category="style")
    collection_id = _create_collection(client, "封顶扩充")
    _add_members(client, collection_id, [seed])

    # 共享 4 个标签也只按 3 个算满分 1.0；门槛 1.0 仍保留
    result = await _suggest(collection_id, min_similarity=1.0)

    assert result["total"] == 1
    assert result["items"][0]["similarity"] == pytest.approx(1.0)


async def test_suggest_limit_truncates(client, upload):
    seed = _upload(client, upload)
    _tag(client, seed, ["通勤"], category="style")
    candidates = []
    for _ in range(3):
        candidate = _upload(client, upload)
        _tag(client, candidate, ["通勤"], category="style")
        candidates.append(candidate)
    collection_id = _create_collection(client, "限量扩充")
    _add_members(client, collection_id, [seed])

    result = await _suggest(collection_id, limit=1, min_similarity=0.3)
    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["inspiration"].id in candidates


async def test_suggest_excludes_members_and_trashed(client, upload):
    seed = _upload(client, upload)
    member = _upload(client, upload)
    trashed = _upload(client, upload)
    free = _upload(client, upload)
    for insp_id in (seed, member, trashed, free):
        _tag(client, insp_id, ["通勤"], category="style")
    collection_id = _create_collection(client, "排除扩充")
    _add_members(client, collection_id, [seed, member])
    client.post(f"/api/inspirations/{trashed}/trash")
    # 未收藏素材同样可以成为候选（扩充不限于收藏）
    client.patch(f"/api/inspirations/{free}", json={"is_favorite": False})

    result = await _suggest(collection_id, min_similarity=0.3)
    ids = [item["inspiration"].id for item in result["items"]]

    assert seed not in ids  # 种子/成员
    assert member not in ids  # 已在夹内
    assert trashed not in ids  # 垃圾桶
    assert free in ids


async def test_suggest_outfit_only_hard_filter(client, upload):
    seed = _upload(client, upload)
    outfit_candidate = _upload(client, upload)
    style_candidate = _upload(client, upload)
    _tag(client, seed, ["通勤"], category="outfit")
    _tag(client, seed, ["休闲"], category="style")
    _tag(client, outfit_candidate, ["通勤"], category="outfit")
    _tag(client, style_candidate, ["休闲"], category="style")
    collection_id = _create_collection(client, "大标签扩充")
    _add_members(client, collection_id, [seed])

    normal = await _suggest(collection_id, min_similarity=0.3)
    normal_ids = {item["inspiration"].id for item in normal["items"]}
    assert {outfit_candidate, style_candidate} <= normal_ids

    outfit_only = await _suggest(collection_id, min_similarity=0.3, outfit_only=True)
    ids = [item["inspiration"].id for item in outfit_only["items"]]
    assert ids == [outfit_candidate]
    assert outfit_only["items"][0]["outfit_tags"] == ["通勤"]


async def test_suggest_excludes_other_auto_collections_flag(client, upload):
    seed = _upload(client, upload)
    candidate = _upload(client, upload)
    _tag(client, seed, ["通勤"], category="style")
    _tag(client, candidate, ["通勤"], category="style")
    collection_id = _create_collection(client, "自动排除扩充")
    _add_members(client, collection_id, [seed])

    auto_collection = await _raw_collection("抖音自动夹", auto_source="douyin")
    async with async_session() as db:
        db.add(
            CollectionItem(
                collection_id=auto_collection, inspiration_id=candidate, position=0
            )
        )
        await db.commit()

    excluded = await _suggest(collection_id, min_similarity=0.3)
    assert candidate not in [item["inspiration"].id for item in excluded["items"]]

    included = await _suggest(
        collection_id, min_similarity=0.3, exclude_other_auto=False
    )
    assert candidate in [item["inspiration"].id for item in included["items"]]


# ══════════════════════════════════════════════════════════════
#  向量召回：混合打分 / 降级 / 质心不可用 / 空候选
# ══════════════════════════════════════════════════════════════


def _patch_vectors(monkeypatch, *, vectors=None, hits=None, error=None):
    """把向量库替换为假实现（is_lancedb_available=True）。"""
    monkeypatch.setattr(suggest.vector_store, "is_lancedb_available", lambda: True)
    monkeypatch.setattr(suggest, "MIN_SEED_VECTORS", 1)

    async def fake_get_vectors(kind, ids):
        if error is not None:
            raise error
        return vectors if vectors is not None else {insp_id: [1.0, 0.0] for insp_id in ids}

    async def fake_search(kind, vector, top_k):
        return hits if hits is not None else []

    monkeypatch.setattr(suggest.vector_store, "get_vectors_batch", fake_get_vectors)
    monkeypatch.setattr(suggest.vector_store, "search_vectors", fake_search)


async def test_suggest_vector_hybrid_scores_and_sort(client, upload, monkeypatch):
    seed = _upload(client, upload)
    visual_candidate = _upload(client, upload)
    tag_candidate = _upload(client, upload)
    _tag(client, seed, ["通勤"], category="style")
    _tag(client, tag_candidate, ["通勤"], category="style")
    collection_id = _create_collection(client, "混合扩充")
    _add_members(client, collection_id, [seed])

    _patch_vectors(
        monkeypatch,
        hits=[{"inspiration_id": visual_candidate, "score": 0.9}],
    )
    result = await _suggest(collection_id, min_similarity=0.3)

    assert result["profile"]["vector_available"] is True
    items = {item["inspiration"].id: item for item in result["items"]}
    visual_hit = items[visual_candidate]
    # 0.6*0.9 + 0.4*0（无共同标签）= 0.54
    assert visual_hit["similarity"] == pytest.approx(0.54)
    assert visual_hit["match_source"] == "hybrid"
    assert visual_hit["shared_tags"] == []
    assert visual_hit["reason"] == "视觉相似 90%"
    # 纯标签候选得分 1/3，低于视觉候选 → 排在后面
    assert items[tag_candidate]["match_source"] == "tag"
    assert result["items"][0]["inspiration"].id == visual_candidate


async def test_suggest_vector_exception_falls_back_to_tags(
    client, upload, monkeypatch, caplog
):
    seed = _upload(client, upload)
    candidate = _upload(client, upload)
    _tag(client, seed, ["通勤"], category="style")
    _tag(client, candidate, ["通勤"], category="style")
    collection_id = _create_collection(client, "降级扩充")
    _add_members(client, collection_id, [seed])

    _patch_vectors(monkeypatch, error=RuntimeError("lancedb 炸了"))
    result = await _suggest(collection_id, min_similarity=0.3)

    assert result["profile"]["vector_available"] is False
    assert [item["inspiration"].id for item in result["items"]] == [candidate]


async def test_suggest_centroid_unavailable_keeps_tag_path(client, upload, monkeypatch):
    """种子向量不足 MIN_SEED_VECTORS → 质心不可用，但标签召回不受影响。"""
    seed = _upload(client, upload)
    candidate = _upload(client, upload)
    _tag(client, seed, ["通勤"], category="style")
    _tag(client, candidate, ["通勤"], category="style")
    collection_id = _create_collection(client, "质心不足扩充")
    _add_members(client, collection_id, [seed])

    monkeypatch.setattr(suggest.vector_store, "is_lancedb_available", lambda: True)
    # 默认 MIN_SEED_VECTORS=5，仅 1 条种子向量 → 质心 None
    async def fake_get_vectors(kind, ids):
        return {ids[0]: [1.0, 0.0]}

    called: list[int] = []

    async def fail_search(*args, **kwargs):
        called.append(1)
        return []

    monkeypatch.setattr(suggest.vector_store, "get_vectors_batch", fake_get_vectors)
    monkeypatch.setattr(suggest.vector_store, "search_vectors", fail_search)

    result = await _suggest(collection_id, min_similarity=0.3)

    assert called == []  # 质心不可用不发起向量检索
    assert result["profile"]["vector_available"] is False
    assert [item["inspiration"].id for item in result["items"]] == [candidate]


async def test_suggest_returns_empty_when_vector_hits_all_dead(
    client, upload, monkeypatch
):
    """向量命中全是已删素材（无标签召回）→ live 过滤后直接空结果，仍报告向量可用。"""
    seed = _upload(client, upload)
    collection_id = _create_collection(client, "死候选扩充")
    _add_members(client, collection_id, [seed])

    _patch_vectors(
        monkeypatch,
        hits=[{"inspiration_id": "no-such-inspiration", "score": 0.99}],
    )
    result = await _suggest(collection_id, min_similarity=0.3)

    assert result["items"] == [] and result["total"] == 0
    assert result["profile"]["vector_available"] is True
    assert result["profile"]["seeds_used"] == 1


async def test_suggest_uses_settings_weights(client, upload, monkeypatch):
    """打分权重来自 settings（0.6 视觉 / 0.4 标签），改配置后得分随之变化。"""
    from app.config import settings

    seed = _upload(client, upload)
    visual_candidate = _upload(client, upload)
    collection_id = _create_collection(client, "权重复用扩充")
    _add_members(client, collection_id, [seed])

    _patch_vectors(
        monkeypatch,
        hits=[{"inspiration_id": visual_candidate, "score": 0.5}],
    )
    monkeypatch.setattr(settings, "vector_similarity_weight", 0.8)
    monkeypatch.setattr(settings, "vector_tag_weight", 0.2)

    result = await _suggest(collection_id, min_similarity=0.3)
    assert result["items"][0]["similarity"] == pytest.approx(0.4)


async def test_suggest_batch_row_created_by_expand_is_excluded(client, upload):
    """扩充写入的成员也是「已在夹内」，不再出现在下一次推荐里（幂等语义）。"""
    seed = _upload(client, upload)
    candidate = _upload(client, upload)
    _tag(client, seed, ["通勤"], category="style")
    _tag(client, candidate, ["通勤"], category="style")
    collection_id = _create_collection(client, "扩充幂等夹")
    _add_members(client, collection_id, [seed])

    first = await _suggest(collection_id, min_similarity=0.3)
    assert [item["inspiration"].id for item in first["items"]] == [candidate]

    resp = client.post(
        f"/api/collections/{collection_id}/expansions",
        json={"inspiration_ids": [candidate]},
    )
    assert resp.status_code == 200, resp.text

    second = await _suggest(collection_id, min_similarity=0.3)
    assert second["items"] == [] and second["total"] == 0


async def test_suggest_profile_top_tags_from_rule_union(client, upload):
    """半智能合集：画像取「规则 ∪ 成员」的标签（规则命中素材的标签同样进画像）。"""
    fav = _upload(client, upload)
    member = _upload(client, upload)
    client.patch(f"/api/inspirations/{fav}", json={"is_favorite": True})
    _tag(client, fav, ["法式"], category="style")
    _tag(client, member, ["通勤"], category="style")
    collection_id = _create_collection(client, "半智能画像", {"is_favorite": True})
    assert (
        client.post(f"/api/collections/{collection_id}/to-semi").status_code == 200
    )
    _add_members(client, collection_id, [member])

    result = await _suggest(collection_id, min_similarity=0.0)
    assert set(result["profile"]["top_tags"]) == {"法式", "通勤"}
    assert result["profile"]["seeds_used"] == 2


async def test_suggest_returns_items_sorted_by_similarity(client, upload):
    seed = _upload(client, upload)
    strong = _upload(client, upload)
    weak = _upload(client, upload)
    _tag(client, seed, ["a", "b", "c"], category="style")
    _tag(client, strong, ["a", "b", "c"], category="style")  # 3 个共同 → 1.0
    _tag(client, weak, ["a"], category="style")  # 1 个共同 → 0.333
    collection_id = _create_collection(client, "排序扩充")
    _add_members(client, collection_id, [seed])

    result = await _suggest(collection_id, min_similarity=0.0)
    assert [item["inspiration"].id for item in result["items"]] == [strong, weak]
    assert result["items"][0]["similarity"] > result["items"][1]["similarity"]
