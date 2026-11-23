"""合集「按此扩充」测试（2026-11-22）：候选生成 + 批次写入 + 撤销。

覆盖：
- 只允许用户自建手动合集：抖音同步合集 / 智能合集 / 分类节点 400；
- 扩充写入带 batch_id 与 added_by='ai_suggest'；撤销只删该批次；
- 手工成员不受撤销影响；
- 向量不可用时退化为纯标签候选，且门槛按标签分生效。
"""

from sqlalchemy import select

from app.database import async_session
from app.models.collection import Collection, CollectionItem


def _create_collection(client, name: str, query_json: dict | None = None) -> int:
    body: dict = {"name": name}
    if query_json is not None:
        body["query_json"] = query_json
    resp = client.post("/api/collections", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_raw_collection(
    name: str, *, auto_source: str | None = None, parent_id: int | None = None
) -> int:
    """直接建库造自动维护合集（用户接口不暴露 auto_source）。"""
    async with async_session() as db:
        collection = Collection(name=name, auto_source=auto_source, parent_id=parent_id)
        db.add(collection)
        await db.commit()
        await db.refresh(collection)
        return collection.id


def _member_ids(client, collection_id: int) -> list[str]:
    resp = client.get(f"/api/collections/{collection_id}/inspirations")
    assert resp.status_code == 200, resp.text
    return [item["id"] for item in resp.json()["items"]]


def _expand(client, collection_id: int, ids: list[str]):
    return client.post(
        f"/api/collections/{collection_id}/expansions",
        json={"inspiration_ids": ids},
    )


def _suggest(client, collection_id: int, **overrides):
    payload = {"limit": 20, "min_similarity": 0.3, **overrides}
    return client.post(
        f"/api/collections/{collection_id}/suggest-items", json=payload
    )


async def test_expand_then_undo_removes_only_batch(client, upload):
    collection_id = _create_collection(client, "扩充目标")
    manual_id = upload().json()["id"]
    a = upload().json()["id"]
    b = upload().json()["id"]

    # 手工先加一条（撤销不应动它）
    resp = client.post(
        f"/api/collections/{collection_id}/inspirations",
        json={"inspiration_ids": [manual_id]},
    )
    assert resp.status_code == 200, resp.text

    resp = _expand(client, collection_id, [a, b])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["added"] == 2
    batch_id = data["batch_id"]
    assert batch_id
    assert set(data["added_ids"]) == {a, b}

    # 落库字段：来源与批次
    async with async_session() as db:
        rows = (
            await db.execute(
                select(CollectionItem).where(
                    CollectionItem.inspiration_id.in_([a, b])
                )
            )
        ).scalars().all()
    assert {row.added_by for row in rows} == {"ai_suggest"}
    assert {row.added_batch for row in rows} == {batch_id}

    assert set(_member_ids(client, collection_id)) == {manual_id, a, b}

    # 撤销：只删本批次
    resp = client.delete(f"/api/collections/{collection_id}/expansions/{batch_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 2
    assert _member_ids(client, collection_id) == [manual_id]

    # 再撤销同一批次：已无可撤销记录
    assert (
        client.delete(f"/api/collections/{collection_id}/expansions/{batch_id}").status_code
        == 404
    )


async def test_expand_rejects_special_collections(client, upload):
    insp_id = upload().json()["id"]

    auto_id = await _create_raw_collection("抖音夹", auto_source="douyin")
    resp = _expand(client, auto_id, [insp_id])
    assert resp.status_code == 400
    assert "抖音同步" in resp.json()["detail"]

    smart_id = _create_collection(client, "智能夹", {"is_favorite": True})
    resp = _expand(client, smart_id, [insp_id])
    assert resp.status_code == 400

    # 分类节点：先在父夹下建子夹，父夹就不能再装素材/扩充
    parent_id = _create_collection(client, "父夹")
    child_resp = client.post(
        "/api/collections", json={"name": "子夹", "parent_id": parent_id}
    )
    assert child_resp.status_code == 201, child_resp.text
    resp = _expand(client, parent_id, [insp_id])
    assert resp.status_code == 400


async def test_undo_never_removes_sync_or_manual_members(client, upload):
    # 抖音自动维护的二级收藏夹（一级是纯分类节点，不能直接装素材）
    root_id = await _create_raw_collection("同步根", auto_source="douyin")
    auto_id = await _create_raw_collection(
        "同步夹", auto_source="douyin", parent_id=root_id
    )
    manual_id = upload().json()["id"]
    expanded_id = upload().json()["id"]

    # 模拟同步写入（生产由 f2_finalize 以 added_by='sync' 调用）
    from app.services import collection_service

    async with async_session() as db:
        await collection_service.add_inspirations(
            db, auto_id, [manual_id], added_by="sync"
        )
        await db.commit()

    # 扩充只能写手动合集，所以这里另建一个手动合集做批次
    manual_collection = _create_collection(client, "手动扩充夹")
    resp = _expand(client, manual_collection, [expanded_id])
    assert resp.status_code == 200, resp.text
    batch_id = resp.json()["batch_id"]

    resp = client.delete(
        f"/api/collections/{manual_collection}/expansions/{batch_id}"
    )
    assert resp.status_code == 200, resp.text

    # 同步夹的成员不受影响
    assert _member_ids(client, auto_id) == [manual_id]


async def test_suggest_rejects_special_collections(client, upload):
    upload()
    auto_id = await _create_raw_collection("抖音夹2", auto_source="douyin")
    assert _suggest(client, auto_id).status_code == 400

    # 纯智能合集允许「只读预览」候选（写入时才转半智能），不再 400
    smart_id = _create_collection(client, "智能夹2", {"is_favorite": True})
    assert _suggest(client, smart_id).status_code == 200
    # 但真正写入（扩充）仍要求半智能/手动
    insp_id = upload().json()["id"]
    assert _expand(client, smart_id, [insp_id]).status_code == 400


async def test_suggest_falls_back_to_tags_without_vectors(client, upload, monkeypatch):
    """向量不可用时按标签召回：共享大标签的未入夹素材应出现在候选里。"""
    from app.services import collection_suggest_service as suggest_service

    monkeypatch.setattr(
        suggest_service.vector_store, "is_lancedb_available", lambda: False
    )

    seed_id = upload().json()["id"]
    candidate_id = upload().json()["id"]
    for insp_id in (seed_id, candidate_id):
        resp = client.post(
            f"/api/inspirations/{insp_id}/tags",
            json={"names": ["通勤"], "category": "outfit", "source": "manual"},
        )
        assert resp.status_code == 200, resp.text

    collection_id = _create_collection(client, "按标签扩充")
    client.post(
        f"/api/collections/{collection_id}/inspirations",
        json={"inspiration_ids": [seed_id]},
    )

    resp = _suggest(client, collection_id)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["profile"]["vector_available"] is False
    ids = [item["inspiration"]["id"] for item in data["items"]]
    assert candidate_id in ids
    assert seed_id not in ids  # 已在夹内
    hit = next(item for item in data["items"] if item["inspiration"]["id"] == candidate_id)
    assert hit["match_source"] == "tag"
    assert "通勤" in hit["shared_tags"]
    assert hit["reason"]

    # 大标签硬约束：候选共享 outfit 标签，应保留
    resp = _suggest(client, collection_id, outfit_only=True)
    assert resp.status_code == 200
    ids = [item["inspiration"]["id"] for item in resp.json()["items"]]
    assert candidate_id in ids


async def test_expand_with_no_new_members_returns_no_batch(client, upload):
    """全部已在合集内时不返回可撤销批次（前端据此不显示撤销条）。"""
    collection_id = _create_collection(client, "重复扩充")
    insp_id = upload().json()["id"]

    first = _expand(client, collection_id, [insp_id])
    assert first.status_code == 200
    assert first.json()["added"] == 1
    assert first.json()["batch_id"]

    again = _expand(client, collection_id, [insp_id])
    assert again.status_code == 200
    body = again.json()
    assert body["added"] == 0
    assert body["batch_id"] is None


async def test_suggest_uses_vectors_when_available(client, upload, monkeypatch):
    """向量可用时走混合打分（回归：suggest 服务必须用 vector.store，不是薄壳）。"""
    from app.services import collection_suggest_service as suggest_service

    # 薄壳 app/services/vector_store.py 没有 get_vectors_batch；这条断言防止退回薄壳
    assert hasattr(suggest_service.vector_store, "get_vectors_batch")

    seed_id = upload().json()["id"]
    candidate_id = upload().json()["id"]
    collection_id = _create_collection(client, "向量扩充")
    client.post(
        f"/api/collections/{collection_id}/inspirations",
        json={"inspiration_ids": [seed_id]},
    )

    monkeypatch.setattr(suggest_service, "MIN_SEED_VECTORS", 1)
    monkeypatch.setattr(
        suggest_service.vector_store, "is_lancedb_available", lambda: True
    )

    async def fake_vectors(kind, ids):
        return {insp_id: [1.0, 0.0] for insp_id in ids}

    async def fake_search(kind, vector, top_k):
        return [{"inspiration_id": candidate_id, "score": 0.9}]

    monkeypatch.setattr(suggest_service.vector_store, "get_vectors_batch", fake_vectors)
    monkeypatch.setattr(suggest_service.vector_store, "search_vectors", fake_search)

    resp = _suggest(client, collection_id, min_similarity=0.5)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["profile"]["vector_available"] is True
    assert data["profile"]["seeds_used"] == 1
    hit = next(
        (item for item in data["items"] if item["inspiration"]["id"] == candidate_id),
        None,
    )
    assert hit is not None
    assert hit["match_source"] == "hybrid"
    assert hit["similarity"] >= 0.5


async def test_expand_into_semi_manual_collection(client, upload):
    """半智能合集可扩充（规则匹配 ∪ 扩充成员）；纯智能仍 400。"""
    smart_id = _create_collection(client, "半智能扩充", {"is_favorite": True})
    insp_id = upload().json()["id"]

    # 纯智能：400
    assert _expand(client, smart_id, [insp_id]).status_code == 400

    # 转半智能后可扩充
    assert client.post(f"/api/collections/{smart_id}/to-semi").status_code == 200
    resp = _expand(client, smart_id, [insp_id])
    assert resp.status_code == 200, resp.text
    assert resp.json()["added"] == 1

    assert insp_id in _member_ids(client, smart_id)


async def test_suggest_for_semi_uses_rule_matches_as_seeds(
    client, upload, monkeypatch
):
    """半智能扩种：种子取「规则匹配 ∪ 成员」，规则命中的素材不再当候选。"""
    from app.services import collection_suggest_service as suggest_service

    monkeypatch.setattr(
        suggest_service.vector_store, "is_lancedb_available", lambda: False
    )

    fav = upload().json()["id"]
    client.patch(f"/api/inspirations/{fav}", json={"is_favorite": True})
    collection_id = _create_collection(client, "半智能种子", {"is_favorite": True})
    assert client.post(f"/api/collections/{collection_id}/to-semi").status_code == 200

    resp = _suggest(client, collection_id, min_similarity=0.0)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["profile"]["seeds_used"] == 1
    assert fav not in [item["inspiration"]["id"] for item in data["items"]]


async def test_undo_expansion_works_on_semi_manual(client, upload):
    """回归：半智能扩充后必须能撤销（此前 undo 仍用 _require_manual 直接 400）。"""
    collection_id = _create_collection(client, "半智能撤销", {"is_favorite": True})
    insp_id = upload().json()["id"]
    assert client.post(f"/api/collections/{collection_id}/to-semi").status_code == 200

    resp = _expand(client, collection_id, [insp_id])
    assert resp.status_code == 200, resp.text
    batch_id = resp.json()["batch_id"]

    resp = client.delete(f"/api/collections/{collection_id}/expansions/{batch_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 1
