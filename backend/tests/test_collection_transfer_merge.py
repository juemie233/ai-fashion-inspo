"""合集成员批量移动/复制/合并测试（2026-11-19 收藏增强 P1）。

覆盖：
- move：从源移除并 append 到多个目标；copy：源不动；
- 目标去重、目标已存在跳过、ids 必须属于源、跨源合并去重；
- 源/目标限制：智能合集、自动维护合集、系统收藏根、分类节点（有子夹）→ 400；
- 合并后源合集删除、目标保留；非法源时不产生部分写入（整单事务）。
"""

from sqlalchemy import select

from app.database import async_session
from app.models.collection import (
    MANUAL_ROOT_COLLECTION_NAME,
    Collection,
    CollectionItem,
)
from app.models.inspiration import Inspiration
from app.utils.time import utcnow


def _create_collection(client, name: str, query_json: dict | None = None) -> int:
    body: dict = {"name": name}
    if query_json is not None:
        body["query_json"] = query_json
    resp = client.post("/api/collections", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _add_material(insp_id: str) -> None:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="manual_upload",
                file_path=f"images/{insp_id}.jpg",
                media_type="image",
                quality_status="approved",
            )
        )
        await db.commit()


async def _add_members(collection_id: int, inspiration_ids: list[str]) -> None:
    async with async_session() as db:
        for position, insp_id in enumerate(inspiration_ids):
            db.add(
                CollectionItem(
                    collection_id=collection_id,
                    inspiration_id=insp_id,
                    position=position,
                )
            )
        await db.commit()


async def _create_raw_collection(
    name: str,
    *,
    auto_source: str | None = None,
    parent_id: int | None = None,
) -> int:
    """直接建库造「自动维护 / 系统收藏根」等特殊合集。"""
    async with async_session() as db:
        collection = Collection(
            name=name, auto_source=auto_source, parent_id=parent_id
        )
        db.add(collection)
        await db.commit()
        await db.refresh(collection)
        return collection.id


def _member_ids(client, collection_id: int) -> list[str]:
    resp = client.get(f"/api/collections/{collection_id}/inspirations")
    assert resp.status_code == 200, resp.text
    return [item["id"] for item in resp.json()["items"]]


def _transfer(client, source_id: int, payload: dict):
    return client.post(
        f"/api/collections/{source_id}/inspirations/transfer", json=payload
    )


def _merge(client, target_id: int, payload: dict):
    return client.post(f"/api/collections/{target_id}/merge", json=payload)


async def test_transfer_move_to_multiple_targets(client):
    source = _create_collection(client, "源夹")
    target1 = _create_collection(client, "目标1")
    target2 = _create_collection(client, "目标2")
    for insp_id in ("a", "b", "c"):
        await _add_material(insp_id)
    await _add_members(source, ["a", "b", "c"])
    await _add_members(target1, ["b"])  # 已存在 → 跳过

    resp = _transfer(
        client,
        source,
        {"ids": ["a", "b"], "target_ids": [target1, target2], "mode": "move"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["source_id"] == source
    assert data["mode"] == "move"
    assert data["target_ids"] == [target1, target2]
    # target1 新增 a（b 跳过）；target2 新增 a、b → added=3, skipped=1
    assert data["added"] == 3
    assert data["skipped"] == 1
    assert data["removed_from_source"] == 2
    assert _member_ids(client, source) == ["c"]
    assert _member_ids(client, target1) == ["b", "a"]
    assert _member_ids(client, target2) == ["a", "b"]


async def test_transfer_copy_keeps_source(client):
    source = _create_collection(client, "复制源")
    target = _create_collection(client, "复制目标")
    await _add_material("a")
    await _add_members(source, ["a"])

    resp = _transfer(
        client,
        source,
        {"ids": ["a"], "target_ids": [target], "mode": "copy"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["added"] == 1
    assert data["removed_from_source"] == 0
    assert _member_ids(client, source) == ["a"]
    assert _member_ids(client, target) == ["a"]


async def test_transfer_target_ids_deduped(client):
    source = _create_collection(client, "去重源")
    target = _create_collection(client, "去重目标")
    await _add_material("a")
    await _add_members(source, ["a"])

    data = _transfer(
        client,
        source,
        {"ids": ["a"], "target_ids": [target, target], "mode": "copy"},
    ).json()
    assert data["target_ids"] == [target]
    assert data["added"] == 1


async def test_transfer_rejects_ids_not_in_source_atomically(client):
    source = _create_collection(client, "源")
    target = _create_collection(client, "目标")
    await _add_material("a")
    await _add_material("b")
    await _add_members(source, ["a"])

    resp = _transfer(
        client,
        source,
        {"ids": ["a", "b"], "target_ids": [target], "mode": "move"},
    )
    assert resp.status_code == 400
    assert "不属于源合集" in resp.json()["detail"]
    # 整单事务：源/目标都没有被改动
    assert _member_ids(client, source) == ["a"]
    assert _member_ids(client, target) == []


async def test_transfer_and_merge_reject_special_collections(client):
    smart = _create_collection(client, "智能夹", {"is_favorite": True})
    manual = _create_collection(client, "手动夹")
    auto_root = await _create_raw_collection("抖音自动根", auto_source="douyin")
    manual_root = await _create_raw_collection(MANUAL_ROOT_COLLECTION_NAME)
    parent = _create_collection(client, "分类父")
    # 建子夹 → parent 变成「有子夹的分类节点」
    child_resp = client.post(
        "/api/collections", json={"name": "子夹", "parent_id": parent}
    )
    assert child_resp.status_code == 201, child_resp.text
    child = child_resp.json()["id"]

    await _add_material("a")
    await _add_members(manual, ["a"])

    # 源非法
    for bad_source in (smart, auto_root, manual_root, parent):
        resp = _transfer(
            client,
            bad_source,
            {"ids": ["a"], "target_ids": [manual], "mode": "copy"},
        )
        assert resp.status_code == 400, (bad_source, resp.text)
    # 目标非法
    for bad_target in (smart, auto_root, manual_root, parent):
        resp = _transfer(
            client,
            manual,
            {"ids": ["a"], "target_ids": [bad_target], "mode": "copy"},
        )
        assert resp.status_code == 400, (bad_target, resp.text)
    # 目标含源
    resp = _transfer(
        client, manual, {"ids": ["a"], "target_ids": [manual], "mode": "copy"}
    )
    assert resp.status_code == 400
    # 自动维护的二级收藏夹同样不允许
    auto_child = await _create_raw_collection(
        "抖音收藏夹", auto_source="douyin", parent_id=auto_root
    )
    resp = _transfer(
        client, manual, {"ids": ["a"], "target_ids": [auto_child], "mode": "copy"}
    )
    assert resp.status_code == 400
    # 子夹本身是普通手动合集，可以作为目标
    resp = _transfer(
        client, manual, {"ids": ["a"], "target_ids": [child], "mode": "copy"}
    )
    assert resp.status_code == 200, resp.text

    # 合并：目标/源非法
    resp = _merge(client, smart, {"source_ids": [manual]})
    assert resp.status_code == 400
    resp = _merge(client, manual, {"source_ids": [smart]})
    assert resp.status_code == 400
    resp = _merge(client, manual, {"source_ids": [manual]})
    assert resp.status_code == 400


async def test_merge_appends_dedups_and_deletes_sources(client):
    target = _create_collection(client, "合并目标")
    source1 = _create_collection(client, "源1")
    source2 = _create_collection(client, "源2")
    for insp_id in ("a", "b", "c"):
        await _add_material(insp_id)
    await _add_members(target, ["a"])
    await _add_members(source1, ["b", "c"])
    await _add_members(source2, ["a", "b"])

    resp = _merge(
        client,
        target,
        {"source_ids": [source1, source2, source1]},  # 请求内重复源去重
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["target_id"] == target
    assert data["merged_collections"] == 2
    # 源1 新增 b,c；源2 中 a 已存在、b 在源1 刚加入 → added=2, skipped=2
    assert data["added"] == 2
    assert data["skipped"] == 2
    assert data["deleted_sources"] == [source1, source2]

    # 目标保留且按「目标原成员 → 源1 → 源2」顺序追加
    assert _member_ids(client, target) == ["a", "b", "c"]
    # 源合集已删除（内容接口 404，列表里也没有）
    assert client.get(f"/api/collections/{source1}/inspirations").status_code == 404
    assert client.get(f"/api/collections/{source2}/inspirations").status_code == 404
    names = [c["name"] for c in client.get("/api/collections").json()]
    assert "源1" not in names and "源2" not in names

    # 成员关系确实被清理（不留孤儿行）
    async with async_session() as db:
        rows = (
            await db.execute(
                select(CollectionItem).where(
                    CollectionItem.collection_id.in_([source1, source2])
                )
            )
        ).scalars().all()
    assert rows == []


async def test_merge_skips_trashed_members(client):
    """源合集里已移入垃圾桶的素材不搬进目标：否则会多出指向已删除素材的幽灵成员。"""
    target = _create_collection(client, "合并目标2")
    source = _create_collection(client, "含垃圾桶素材的源")
    for insp_id in ("live", "trashed"):
        await _add_material(insp_id)
    await _add_members(source, ["live", "trashed"])

    async with async_session() as db:
        row = await db.get(Inspiration, "trashed")
        assert row is not None
        row.deleted_at = utcnow()
        row.trash_reason = "重复"
        row.trash_source = "manual"
        await db.commit()

    # 复制：垃圾桶成员视为不属于源（在处理掉源合集之前先验证）
    target2 = _create_collection(client, "复制目标2")
    resp = _transfer(
        client,
        source,
        {"mode": "copy", "target_ids": [target2], "ids": ["trashed"]},
    )
    assert resp.status_code == 400
    assert "不属于源合集" in resp.json()["detail"]

    resp = _merge(client, target, {"source_ids": [source]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["added"] == 1
    assert _member_ids(client, target) == ["live"]


async def test_merge_invalid_source_keeps_everything(client):
    target = _create_collection(client, "目标")
    valid_source = _create_collection(client, "有效源")
    await _add_material("a")
    await _add_material("b")
    await _add_members(target, ["a"])
    await _add_members(valid_source, ["b"])

    resp = _merge(client, target, {"source_ids": [valid_source, 999999]})
    assert resp.status_code == 404
    # 整单事务：目标未被追加、有效源仍在
    assert _member_ids(client, target) == ["a"]
    assert client.get(f"/api/collections/{valid_source}/inspirations").status_code == 200


async def test_transfer_merge_validation(client):
    cid = _create_collection(client, "校验夹")
    await _add_material("a")
    await _add_members(cid, ["a"])

    assert _transfer(client, cid, {"ids": [], "target_ids": [cid], "mode": "move"}).status_code == 422
    assert _transfer(client, cid, {"ids": ["a"], "target_ids": [], "mode": "move"}).status_code == 422
    assert _transfer(client, cid, {"ids": ["a"], "target_ids": [cid], "mode": "rename"}).status_code == 422
    assert _merge(client, cid, {"source_ids": []}).status_code == 422
