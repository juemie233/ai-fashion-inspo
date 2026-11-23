"""半智能合集 /「按此扩充」写路径（`collection_service.py`）服务层补测。

既有 `tests/test_collections.py`、`tests/test_collection_expansion.py` 走接口主路径；
这里直接调服务函数，覆盖：enable_semi_manual（幂等/类型限制/系统收藏根）、
require_suggestable 与 require_expandable 的全部拒绝分支、内容 ID 查询（手动顺序 /
智能最新 / 半智能并集）、半智能内容「规则 ∪ 成员」各自的垃圾桶排除与去重、
item_count 口径、序列化 semi_manual、扩充批次写入/去重/撤销与审计留痕、
半智能扩充后撤销仍保留规则命中、移出成员 vs 规则命中的 removed/skipped 语义、
半智能固化保留成员与规则并集等。全部数据用临时库 + 真实服务调用，不触网。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database import async_session
from app.models.audit import AuditLog
from app.models.collection import (
    MANUAL_ROOT_COLLECTION_NAME,
    Collection,
    CollectionItem,
)
from app.models.inspiration import Inspiration
from app.services import collection_service
from app.utils.time import utcnow


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


def _set_favorite(client, inspiration_id: str, value: bool = True) -> None:
    resp = client.patch(
        f"/api/inspirations/{inspiration_id}", json={"is_favorite": value}
    )
    assert resp.status_code == 200, resp.text


async def _raw_collection(
    name: str,
    *,
    auto_source: str | None = None,
    parent_id: int | None = None,
    query_json: str | None = None,
) -> int:
    """直接建库造特殊合集（系统收藏根 / 抖音同步 / 分类节点）。"""
    async with async_session() as db:
        collection = Collection(
            name=name,
            auto_source=auto_source,
            parent_id=parent_id,
            query_json=query_json,
        )
        db.add(collection)
        await db.commit()
        await db.refresh(collection)
        return collection.id


async def _get(cid: int) -> Collection:
    async with async_session() as db:
        collection = await db.get(Collection, cid)
        assert collection is not None
        return collection


async def _dict(cid: int) -> dict:
    async with async_session() as db:
        return await collection_service.collection_to_dict(
            db, await collection_service.get_collection(db, cid)
        )


async def _content_ids(cid: int, **kwargs) -> tuple[list[str], int]:
    async with async_session() as db:
        items, total = await collection_service.list_collection_inspirations(
            db, cid, **kwargs
        )
        return [item.id for item in items], total


async def _expand(cid: int, inspiration_ids: list[str]) -> dict:
    async with async_session() as db:
        return await collection_service.expand_collection(db, cid, inspiration_ids)


async def _undo(cid: int, batch_id: str) -> dict:
    async with async_session() as db:
        return await collection_service.undo_expansion(db, cid, batch_id)


async def _audits(action: str) -> list[AuditLog]:
    async with async_session() as db:
        return list(
            (
                await db.execute(
                    select(AuditLog).where(AuditLog.action == action)
                )
            ).scalars().all()
        )


async def _member_rows(cid: int) -> list[CollectionItem]:
    async with async_session() as db:
        return list(
            (
                await db.execute(
                    select(CollectionItem).where(CollectionItem.collection_id == cid)
                )
            ).scalars().all()
        )


# ══════════════════════════════════════════════════════════════
#  enable_semi_manual：转换 / 幂等 / 类型限制 / 系统收藏根
# ══════════════════════════════════════════════════════════════


async def test_enable_semi_manual_converts_and_idempotent(client, upload):
    fav = _upload(client, upload)
    _set_favorite(client, fav)
    cid = _create_collection(client, "转半智能直调", {"is_favorite": True})

    async with async_session() as db:
        first = await collection_service.enable_semi_manual(db, cid)

    assert first["kind"] == "smart"
    assert first["semi_manual"] is True
    assert first["item_count"] == 1  # 规则 ∪ 成员
    assert first["query_json"] == {"is_favorite": True}

    before = await _get(cid)
    assert before.semi_manual is True
    updated_at = before.updated_at

    # 幂等：重复调用仍返回 semi_manual，且不再写库（updated_at 不变）
    async with async_session() as db:
        second = await collection_service.enable_semi_manual(db, cid)
    after = await _get(cid)
    assert second["semi_manual"] is True
    assert after.updated_at == updated_at


async def test_enable_semi_manual_rejects_manual_and_missing(client):
    manual = _create_collection(client, "手动合集")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await collection_service.enable_semi_manual(db, manual)
        assert exc.value.status_code == 400
        assert exc.value.detail == "手动合集无需转为半智能"

        with pytest.raises(HTTPException) as missing:
            await collection_service.enable_semi_manual(db, 999999)
        assert missing.value.status_code == 404


async def test_enable_semi_manual_rejects_manual_root():
    root_id = await _raw_collection(
        MANUAL_ROOT_COLLECTION_NAME, query_json='{"is_favorite": true}'
    )
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await collection_service.enable_semi_manual(db, root_id)
        assert exc.value.status_code == 400
        assert "系统收藏根" in exc.value.detail
    assert (await _get(root_id)).semi_manual is False


# ══════════════════════════════════════════════════════════════
#  require_suggestable / require_expandable：资格矩阵
# ══════════════════════════════════════════════════════════════


async def test_require_suggestable_allows_manual_smart_semi(client, upload):
    manual = _create_collection(client, "预览手动")
    smart = _create_collection(client, "预览智能", {"is_favorite": True})
    semi = _create_collection(client, "预览半智能", {"is_favorite": True})
    client.post(f"/api/collections/{semi}/to-semi")

    async with async_session() as db:
        for cid in (manual, smart, semi):
            got = await collection_service.require_suggestable(db, cid)
            assert got.id == cid


async def test_require_suggestable_rejects_auto_root_container_missing(client):
    smart_root = await _raw_collection(
        MANUAL_ROOT_COLLECTION_NAME, query_json='{"is_favorite": true}'
    )
    auto_id = await _raw_collection("抖音同步夹", auto_source="douyin")
    parent = _create_collection(client, "分类父")
    child = client.post("/api/collections", json={"name": "子夹", "parent_id": parent})
    assert child.status_code == 201, child.text

    async with async_session() as db:
        for cid, keyword in (
            (smart_root, "系统收藏根"),
            (auto_id, "抖音同步"),
            (parent, "分类节点"),
        ):
            with pytest.raises(HTTPException) as exc:
                await collection_service.require_suggestable(db, cid)
            assert exc.value.status_code == 400
            assert keyword in exc.value.detail

        with pytest.raises(HTTPException) as missing:
            await collection_service.require_suggestable(db, 999999)
        assert missing.value.status_code == 404


async def test_require_expandable_allows_manual_and_semi(client):
    manual = _create_collection(client, "写入手动")
    semi = _create_collection(client, "写入半智能", {"is_favorite": True})
    client.post(f"/api/collections/{semi}/to-semi")

    async with async_session() as db:
        assert (await collection_service.require_expandable(db, manual)).id == manual
        assert (await collection_service.require_expandable(db, semi)).id == semi


async def test_require_expandable_rejects_pure_smart_and_special(client):
    pure_smart = _create_collection(client, "纯智能写入", {"is_favorite": True})
    manual_root = await _raw_collection(MANUAL_ROOT_COLLECTION_NAME)
    auto_id = await _raw_collection("抖音同步夹2", auto_source="douyin")
    parent = _create_collection(client, "分类父2")
    child = client.post("/api/collections", json={"name": "子夹2", "parent_id": parent})
    assert child.status_code == 201, child.text

    async with async_session() as db:
        with pytest.raises(HTTPException) as smart_exc:
            await collection_service.require_expandable(db, pure_smart)
        assert smart_exc.value.status_code == 400
        assert "半智能" in smart_exc.value.detail

        for cid, keyword in (
            (manual_root, "系统收藏根"),
            (auto_id, "抖音同步"),
            (parent, "分类节点"),
        ):
            with pytest.raises(HTTPException) as exc:
                await collection_service.require_expandable(db, cid)
            assert exc.value.status_code == 400
            assert keyword in exc.value.detail

        with pytest.raises(HTTPException) as missing:
            await collection_service.require_expandable(db, 999999)
        assert missing.value.status_code == 404


# ══════════════════════════════════════════════════════════════
#  content_id_query：手动 position / 智能最新 / 半智能并集
# ══════════════════════════════════════════════════════════════


async def test_content_id_query_manual_keeps_position_order(client, upload):
    a = _upload(client, upload)
    b = _upload(client, upload)
    cid = _create_collection(client, "ID 手动顺序")
    async with async_session() as db:
        db.add_all(
            [
                CollectionItem(collection_id=cid, inspiration_id=b, position=0),
                CollectionItem(collection_id=cid, inspiration_id=a, position=1),
            ]
        )
        await db.commit()

    collection = await _get(cid)
    async with async_session() as db:
        rows = (
            await db.execute(collection_service.content_id_query(collection))
        ).scalars().all()
    assert list(rows) == [b, a]


async def test_content_id_query_smart_newest_first(client, upload):
    old = _upload(client, upload)
    new = _upload(client, upload)
    for insp_id, created_at in ((old, utcnow() - timedelta(days=2)), (new, utcnow())):
        async with async_session() as db:
            row = await db.get(Inspiration, insp_id)
            row.created_at = created_at
            row.is_favorite = True
            await db.commit()
    cid = _create_collection(client, "ID 智能最新", {"is_favorite": True})

    collection = await _get(cid)
    async with async_session() as db:
        rows = (
            await db.execute(collection_service.content_id_query(collection))
        ).scalars().all()
    assert list(rows) == [new, old]


async def test_content_id_query_semi_is_rule_union_members(client, upload):
    fav = _upload(client, upload)
    _set_favorite(client, fav)
    member = _upload(client, upload)
    cid = _create_collection(client, "ID 半智能并集", {"is_favorite": True})
    client.post(f"/api/collections/{cid}/to-semi")
    async with async_session() as db:
        db.add(CollectionItem(collection_id=cid, inspiration_id=member, position=0))
        await db.commit()

    collection = await _get(cid)
    async with async_session() as db:
        rows = (
            await db.execute(collection_service.content_id_query(collection))
        ).scalars().all()
    assert set(rows) == {fav, member}


# ══════════════════════════════════════════════════════════════
#  半智能内容口径：规则 ∪ 成员、去重、两侧各自排除垃圾桶
# ══════════════════════════════════════════════════════════════


async def test_semi_content_union_dedupes_and_excludes_trash_each_side(
    client, upload
):
    fav = _upload(client, upload)
    member = _upload(client, upload)
    trashed_rule = _upload(client, upload)
    _set_favorite(client, fav)
    _set_favorite(client, trashed_rule)
    cid = _create_collection(client, "半智能并集去重", {"is_favorite": True})
    client.post(f"/api/collections/{cid}/to-semi")
    # fav 既是规则命中又是成员 → 只能出现一次
    async with async_session() as db:
        db.add_all(
            [
                CollectionItem(collection_id=cid, inspiration_id=fav, position=0),
                CollectionItem(collection_id=cid, inspiration_id=member, position=1),
            ]
        )
        await db.commit()

    ids, total = await _content_ids(cid)
    assert total == 3
    assert set(ids) == {fav, member, trashed_rule}
    assert ids.count(fav) == 1  # 规则命中 + 成员行只出现一次

    # 成员进垃圾桶 → 成员侧排除；规则命中进垃圾桶 → 规则侧排除
    client.post(f"/api/inspirations/{member}/trash")
    client.post(f"/api/inspirations/{trashed_rule}/trash")
    ids, total = await _content_ids(cid)
    assert ids == [fav] and total == 1

    # evaluate_smart_collection 与内容页同口径
    collection = await _get(cid)
    async with async_session() as db:
        items, eval_total = await collection_service.evaluate_smart_collection(
            db, collection
        )
    assert eval_total == 1
    assert [item.id for item in items] == [fav]


async def test_semi_item_count_matches_content_and_batch_serialize(client, upload):
    fav = _upload(client, upload)
    member = _upload(client, upload)
    _set_favorite(client, fav)
    cid = _create_collection(client, "半智能计数", {"is_favorite": True})
    client.post(f"/api/collections/{cid}/to-semi")
    async with async_session() as db:
        db.add(CollectionItem(collection_id=cid, inspiration_id=member, position=0))
        await db.commit()

    direct = await _dict(cid)
    assert direct["semi_manual"] is True
    assert direct["kind"] == "smart"
    assert direct["item_count"] == 2
    assert direct["item_count"] == (await _content_ids(cid))[1]

    # 批量序列化路径（列表接口）同样是 union 口径
    async with async_session() as db:
        listed = await collection_service.list_collections(db)
    item = next(row for row in listed if row["id"] == cid)
    assert item["item_count"] == 2
    assert item["semi_manual"] is True


async def test_add_inspirations_semi_allowed_and_pure_smart_rejected(
    client, upload
):
    fav = _upload(client, upload)
    extra = _upload(client, upload)
    _set_favorite(client, fav)
    pure = _create_collection(client, "纯智能加入", {"is_favorite": True})
    semi = _create_collection(client, "半智能加入", {"is_favorite": True})
    client.post(f"/api/collections/{semi}/to-semi")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await collection_service.add_inspirations(db, pure, [extra])
        assert exc.value.status_code == 400

        result = await collection_service.add_inspirations(db, semi, [extra])
    assert result["added"] == 1
    rows = await _member_rows(semi)
    assert [(row.inspiration_id, row.added_by) for row in rows] == [(extra, "manual")]


# ══════════════════════════════════════════════════════════════
#  扩充批次：写入 / 去重 / 无新成员 / 审计
# ══════════════════════════════════════════════════════════════


async def test_expand_success_writes_batch_positions_and_audit(client, upload):
    manual_member = _upload(client, upload)
    a = _upload(client, upload)
    b = _upload(client, upload)
    cid = _create_collection(client, "扩充批次直调")
    async with async_session() as db:
        db.add(
            CollectionItem(
                collection_id=cid, inspiration_id=manual_member, position=5
            )
        )
        await db.commit()

    result = await _expand(cid, [a, b])

    assert result["added"] == 2
    assert result["skipped"] == 0
    assert result["not_found"] == 0
    assert set(result["added_ids"]) == {a, b}
    assert result["batch_id"]

    rows = {row.inspiration_id: row for row in await _member_rows(cid)}
    assert rows[a].added_by == "ai_suggest"
    assert rows[b].added_by == "ai_suggest"
    assert rows[a].added_batch == result["batch_id"]
    assert rows[b].added_batch == result["batch_id"]
    assert sorted([rows[a].position, rows[b].position]) == [6, 7]  # append 到 max+1

    logs = await _audits("collection_expand")
    assert len(logs) == 1
    assert logs[0].count == 2
    assert result["batch_id"] in logs[0].detail


async def test_expand_dedupes_and_reports_skipped_not_found(client, upload):
    existing = _upload(client, upload)
    new = _upload(client, upload)
    trashed = _upload(client, upload)
    cid = _create_collection(client, "扩充去重直调")
    client.post(f"/api/inspirations/{trashed}/trash")
    async with async_session() as db:
        db.add(CollectionItem(collection_id=cid, inspiration_id=existing, position=0))
        await db.commit()

    result = await _expand(cid, [new, new, existing, trashed, "no-such-id"])

    assert result["added"] == 1  # 请求内去重后只剩 new 可加
    assert result["added_ids"] == [new]
    assert result["skipped"] == 1  # 已在夹内
    assert result["not_found"] == 2  # 垃圾桶 + 不存在
    assert result["batch_id"]


async def test_expand_no_new_members_returns_no_batch_no_audit(client, upload):
    existing = _upload(client, upload)
    cid = _create_collection(client, "扩充无新成员")
    async with async_session() as db:
        db.add(CollectionItem(collection_id=cid, inspiration_id=existing, position=0))
        await db.commit()

    result = await _expand(cid, [existing])

    assert result["added"] == 0
    assert result["batch_id"] is None
    assert result["added_ids"] == []
    assert await _audits("collection_expand") == []  # 没有可撤销批次就不写审计


async def test_expand_allows_semi_and_rejects_pure_smart(client, upload):
    fav = _upload(client, upload)
    extra = _upload(client, upload)
    _set_favorite(client, fav)
    pure = _create_collection(client, "纯智能扩充直调", {"is_favorite": True})
    semi = _create_collection(client, "半智能扩充直调", {"is_favorite": True})
    client.post(f"/api/collections/{semi}/to-semi")

    with pytest.raises(HTTPException) as exc:
        await _expand(pure, [extra])
    assert exc.value.status_code == 400

    result = await _expand(semi, [extra])
    assert result["added"] == 1
    ids, _total = await _content_ids(semi)
    assert set(ids) == {fav, extra}  # 规则命中 ∪ 扩充成员


# ══════════════════════════════════════════════════════════════
#  撤销扩充：只删本批次 ai_suggest、半智能可撤销、404
# ══════════════════════════════════════════════════════════════


async def test_undo_removes_only_batch_ai_suggest_members(client, upload):
    manual_member = _upload(client, upload)
    sync_member = _upload(client, upload)
    batch_a = _upload(client, upload)
    batch_b = _upload(client, upload)
    cid = _create_collection(client, "撤销批次直调")
    async with async_session() as db:
        db.add_all(
            [
                CollectionItem(
                    collection_id=cid,
                    inspiration_id=manual_member,
                    position=0,
                    added_by="manual",
                ),
                CollectionItem(
                    collection_id=cid,
                    inspiration_id=sync_member,
                    position=1,
                    added_by="sync",
                ),
            ]
        )
        await db.commit()

    batch_id = (await _expand(cid, [batch_a, batch_b]))["batch_id"]
    result = await _undo(cid, batch_id)

    assert result == {"removed": 2, "batch_id": batch_id}
    rows = await _member_rows(cid)
    assert {row.inspiration_id for row in rows} == {manual_member, sync_member}
    logs = await _audits("collection_expand_undo")
    assert len(logs) == 1
    assert logs[0].count == 2

    # 同一批次已无可撤销记录 → 404
    with pytest.raises(HTTPException) as exc:
        await _undo(cid, batch_id)
    assert exc.value.status_code == 404


async def test_undo_expansion_works_on_semi_manual(client, upload):
    """半智能可扩充也可撤销：撤销后规则命中仍在内容里、扩充成员消失。"""
    fav = _upload(client, upload)
    extra = _upload(client, upload)
    _set_favorite(client, fav)
    cid = _create_collection(client, "半智能撤销直调", {"is_favorite": True})
    client.post(f"/api/collections/{cid}/to-semi")

    batch_id = (await _expand(cid, [extra]))["batch_id"]
    ids, _total = await _content_ids(cid)
    assert set(ids) == {fav, extra}

    result = await _undo(cid, batch_id)
    assert result["removed"] == 1
    ids, total = await _content_ids(cid)
    assert ids == [fav] and total == 1


async def test_undo_rejects_pure_smart_and_unknown_batch(client, upload):
    pure = _create_collection(client, "纯智能撤销", {"is_favorite": True})
    with pytest.raises(HTTPException) as exc:
        await _undo(pure, "any-batch")
    assert exc.value.status_code == 400
    assert "半智能" in exc.value.detail

    manual = _create_collection(client, "撤销未知批次")
    with pytest.raises(HTTPException) as missing:
        await _undo(manual, "not-exist-batch")
    assert missing.value.status_code == 404


# ══════════════════════════════════════════════════════════════
#  移出成员 vs 规则命中：removed / skipped 语义
# ══════════════════════════════════════════════════════════════


async def test_remove_inspirations_semi_member_vs_rule_match(client, upload):
    fav = _upload(client, upload)
    member = _upload(client, upload)
    _set_favorite(client, fav)
    cid = _create_collection(client, "半智能移出", {"is_favorite": True})
    client.post(f"/api/collections/{cid}/to-semi")
    async with async_session() as db:
        db.add(CollectionItem(collection_id=cid, inspiration_id=member, position=0))
        await db.commit()

    async with async_session() as db:
        result = await collection_service.remove_inspirations(
            db, cid, [member, member, fav]
        )

    # 成员 member 删除并去重；规则命中的 fav 没有成员行 → skipped
    assert result == {"removed": 1, "skipped": 1}
    ids, total = await _content_ids(cid)
    assert ids == [fav] and total == 1


# ══════════════════════════════════════════════════════════════
#  半智能固化：保留成员与扩充批次、只补规则命中
# ══════════════════════════════════════════════════════════════


async def test_solidify_semi_preserves_members_and_rule_matches(client, upload):
    fav = _upload(client, upload)
    manual_member = _upload(client, upload)
    batch_member = _upload(client, upload)
    _set_favorite(client, fav)
    cid = _create_collection(client, "半智能固化直调", {"is_favorite": True})
    client.post(f"/api/collections/{cid}/to-semi")
    async with async_session() as db:
        db.add(
            CollectionItem(
                collection_id=cid, inspiration_id=manual_member, position=0
            )
        )
        await db.commit()
    batch_id = (await _expand(cid, [batch_member]))["batch_id"]

    async with async_session() as db:
        out = await collection_service.solidify_collection(db, cid)

    assert out["kind"] == "manual"
    assert out["semi_manual"] is False
    assert out["query_json"] is None
    assert out["item_count"] == 3

    rows = {row.inspiration_id: row for row in await _member_rows(cid)}
    assert set(rows) == {fav, manual_member, batch_member}
    # 扩充批次的来源信息保留（撤销能力不受固化影响）
    assert rows[batch_member].added_by == "ai_suggest"
    assert rows[batch_member].added_batch == batch_id
    assert rows[manual_member].added_by == "manual"

    # 固化后规则不再生效：再收藏一条不会自动进入内容
    later_fav = _upload(client, upload)
    _set_favorite(client, later_fav)
    ids, total = await _content_ids(cid)
    assert total == 3 and later_fav not in ids


# ══════════════════════════════════════════════════════════════
#  半智能内容列表：筛选 / 排序 / 分页
# ══════════════════════════════════════════════════════════════


async def test_semi_content_filters_sort_and_pagination(client, upload):
    f1 = _upload(client, upload)
    f2 = _upload(client, upload)
    member = _upload(client, upload)
    _set_favorite(client, f1)
    _set_favorite(client, f2)
    async with async_session() as db:
        row1 = await db.get(Inspiration, f1)
        row1.rating = 5
        row1.caption = "海边度假穿搭"
        row1.created_at = utcnow() - timedelta(days=2)
        row2 = await db.get(Inspiration, f2)
        row2.rating = 3
        row2.created_at = utcnow() - timedelta(days=1)
        row3 = await db.get(Inspiration, member)
        row3.caption = "通勤关键词"
        row3.created_at = utcnow()
        await db.commit()

    cid = _create_collection(client, "半智能内容筛选", {"is_favorite": True})
    client.post(f"/api/collections/{cid}/to-semi")
    async with async_session() as db:
        db.add(CollectionItem(collection_id=cid, inspiration_id=member, position=0))
        await db.commit()

    assert (await _content_ids(cid))[1] == 3
    newest, total = await _content_ids(cid, sort="newest")
    assert total == 3
    assert newest[0] == member  # 最新在前
    oldest, _ = await _content_ids(cid, sort="oldest")
    assert oldest[0] == f1

    keyword_ids, keyword_total = await _content_ids(cid, q="通勤关键词")
    assert (keyword_ids, keyword_total) == ([member], 1)
    rated, rated_total = await _content_ids(cid, rating_min=4)
    assert (rated, rated_total) == ([f1], 1)
    grabbed, favorite_total = await _content_ids(cid, is_favorite=True)
    assert favorite_total == 2 and member not in grabbed

    page1, _ = await _content_ids(cid, page=1, size=2)
    page2, _ = await _content_ids(cid, page=2, size=2)
    assert len(page1) == 2 and len(page2) == 1

    async with async_session() as db:
        with pytest.raises(HTTPException) as bad_sort:
            await collection_service.list_collection_inspirations(
                db, cid, sort="bogus"
            )
        assert bad_sort.value.status_code == 400
        with pytest.raises(HTTPException) as manual_sort:
            await collection_service.list_collection_inspirations(
                db, cid, sort="manual"
            )
        assert manual_sort.value.status_code == 400
        assert "manual 排序" in manual_sort.value.detail


async def test_evaluate_smart_collection_manual_rejected_and_pagination(client, upload):
    manual = _create_collection(client, "求值手动拒绝")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await collection_service.evaluate_smart_collection(
                db, await collection_service.get_collection(db, manual)
            )
        assert exc.value.status_code == 400
        assert "手动合集" in exc.value.detail

    ids = []
    for _ in range(3):
        insp_id = _upload(client, upload)
        _set_favorite(client, insp_id)
        ids.append(insp_id)
    smart = _create_collection(client, "求值分页", {"is_favorite": True})
    collection = await _get(smart)
    async with async_session() as db:
        page, total = await collection_service.evaluate_smart_collection(
            db, collection, page=2, size=2
        )
    assert total == 3
    assert len(page) == 1


# ══════════════════════════════════════════════════════════════
#  同函数非半智能配套分支（避免重构后残留未覆盖分支）
# ══════════════════════════════════════════════════════════════


async def test_solidify_rejects_manual_and_pure_smart_replaces_members(
    client, upload
):
    """固化：手动合集 400；纯智能（非半智能）走覆盖式写入。"""
    manual = _create_collection(client, "固化手动拒绝")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await collection_service.solidify_collection(db, manual)
        assert exc.value.status_code == 400
        assert exc.value.detail == "手动合集无需固化"

    fav = _upload(client, upload)
    _set_favorite(client, fav)
    stale_member = _upload(client, upload)
    pure = _create_collection(client, "固化纯智能", {"is_favorite": True})
    # 纯智能正常没有成员行，塞一条历史脏行验证「覆盖式写入」会清掉它
    async with async_session() as db:
        db.add(
            CollectionItem(collection_id=pure, inspiration_id=stale_member, position=0)
        )
        await db.commit()

    async with async_session() as db:
        out = await collection_service.solidify_collection(db, pure)

    assert out["kind"] == "manual"
    assert out["semi_manual"] is False
    assert out["item_count"] == 1
    rows = await _member_rows(pure)
    assert [row.inspiration_id for row in rows] == [fav]


async def test_manual_content_sort_reranks_members(client, upload):
    """手动合集内容页显式排序：先去掉 position 排序再按上传时间重排。"""
    old = _upload(client, upload)
    new = _upload(client, upload)
    cid = _create_collection(client, "手动排序分支")
    async with async_session() as db:
        db.add_all(
            [
                CollectionItem(collection_id=cid, inspiration_id=new, position=0),
                CollectionItem(collection_id=cid, inspiration_id=old, position=1),
            ]
        )
        row_old = await db.get(Inspiration, old)
        row_old.created_at = utcnow() - timedelta(days=2)
        row_new = await db.get(Inspiration, new)
        row_new.created_at = utcnow()
        await db.commit()

    manual_order, _ = await _content_ids(cid)
    assert manual_order == [new, old]  # position 顺序

    newest, total = await _content_ids(cid, sort="newest")
    assert total == 2
    assert newest == [new, old]

    oldest, _ = await _content_ids(cid, sort="oldest")
    assert oldest == [old, new]
