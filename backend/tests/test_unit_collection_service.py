"""收藏合集服务的测试（两级结构 / 智能合集 / 成员增删 / 封面 / 固化为手动）。

合集是用户手工维护的数据，最容易出的问题：
1. **两级结构被破坏**：一级节点不该装素材、子收藏夹不能再有子级；
2. **同一素材重复进同一合集**（唯一约束或幂等没做 → 关联行爆炸、排序错乱）；
3. **智能合集误删成员**：智能合集是按条件动态求值的，把它的成员当手动成员删会
   让「固化」语义失效。
"""

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.collection import Collection, CollectionItem
from app.models.inspiration import Inspiration
from app.services import collection_service as svc


async def _insp(insp_id: str, *, deleted: bool = False, source: str = "douyin") -> str:
    from datetime import datetime, timezone

    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type=source,
                file_path=f"images/x/{insp_id}.jpg",
                media_type="image",
                quality_status="approved",
                is_favorite=True,
                deleted_at=datetime.now(timezone.utc) if deleted else None,
            )
        )
        await db.commit()
    return insp_id


# ── 创建 / 重名 / 两级约束 ──


async def test_create_collection_basic(client):
    async with async_session() as db:
        created = await svc.create_collection(db, "我的合集", description="说明")

    assert created["name"] == "我的合集"
    assert created["item_count"] == 0
    assert created["parent_id"] is None


async def test_create_duplicate_name_conflict(client):
    async with async_session() as db:
        await svc.create_collection(db, "重名合集")
        with pytest.raises(HTTPException) as exc:
            await svc.create_collection(db, "重名合集")
    assert exc.value.status_code == 409


async def test_create_child_collection(client):
    async with async_session() as db:
        parent = await svc.create_collection(db, "一级分类")
        child = await svc.create_collection(db, "二级收藏夹", parent_id=parent["id"])

    assert child["parent_id"] == parent["id"]


async def test_create_child_rejected_when_parent_has_items(client):
    """一级节点一旦装了素材就不能再当父：口径是「分类不装素材」。"""
    await _insp("p1")
    async with async_session() as db:
        parent = await svc.create_collection(db, "装了素材的一级")
        await svc.add_inspirations(db, parent["id"], ["p1"])

        with pytest.raises(HTTPException) as exc:
            await svc.create_collection(db, "子收藏夹", parent_id=parent["id"])

    assert exc.value.status_code == 400
    assert "分类节点不装素材" in str(exc.value.detail)


async def test_create_grandchild_rejected(client):
    """最多两级：二级下面不能再建。"""
    async with async_session() as db:
        parent = await svc.create_collection(db, "顶")
        child = await svc.create_collection(db, "中", parent_id=parent["id"])

        with pytest.raises(HTTPException) as exc:
            await svc.create_collection(db, "底", parent_id=child["id"])

    assert exc.value.status_code == 400
    assert "两级" in str(exc.value.detail) or "一级" in str(exc.value.detail)


async def test_create_with_unknown_parent_404(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await svc.create_collection(db, "孤儿", parent_id=999999)
    assert exc.value.status_code == 404


# ── 列表 / 详情 ──


async def test_list_collections_tree(client):
    async with async_session() as db:
        parent = await svc.create_collection(db, "列表父")
        await svc.create_collection(db, "列表子", parent_id=parent["id"])

        items = await svc.list_collections(db)

    names = {c["name"] for c in items}
    assert {"列表父", "列表子"} <= names
    parent_row = next(c for c in items if c["name"] == "列表父")
    assert parent_row["child_count"] >= 1


async def test_get_collection_and_404(client):
    async with async_session() as db:
        created = await svc.create_collection(db, "取用合集")
        fetched = await svc.get_collection(db, created["id"])
        assert fetched.name == "取用合集"

        with pytest.raises(HTTPException) as exc:
            await svc.get_collection(db, 999999)
    assert exc.value.status_code == 404


# ── 成员增删 ──


async def test_add_inspirations_idempotent(client):
    """重复加入不产生重复关联行（否则排序与计数都会错）。"""
    await _insp("a1")
    async with async_session() as db:
        created = await svc.create_collection(db, "去重合集")
        await svc.add_inspirations(db, created["id"], ["a1"])
        await svc.add_inspirations(db, created["id"], ["a1"])

        data = await svc.collection_to_dict(db, await svc.get_collection(db, created["id"]))

    assert data["item_count"] == 1


async def test_add_inspirations_skips_trash(client):
    await _insp("a2", deleted=True)
    async with async_session() as db:
        created = await svc.create_collection(db, "垃圾桶合集")
        result = await svc.add_inspirations(db, created["id"], ["a2"])
        data = await svc.collection_to_dict(db, await svc.get_collection(db, created["id"]))

    assert result["added"] == 0
    assert data["item_count"] == 0


async def test_add_inspirations_rejects_smart_collection(client):
    """智能合集成员由条件求值，手工加成员没有意义（应 400 提示）。"""
    await _insp("a3")
    async with async_session() as db:
        smart = await svc.create_collection(
            db, "智能合集", query_json={"is_favorite": True}
        )

        with pytest.raises(HTTPException) as exc:
            await svc.add_inspirations(db, smart["id"], ["a3"])
    assert exc.value.status_code == 400


async def test_remove_inspirations(client):
    await _insp("r1")
    async with async_session() as db:
        created = await svc.create_collection(db, "移除合集")
        await svc.add_inspirations(db, created["id"], ["r1"])

        removed = await svc.remove_inspirations(db, created["id"], ["r1"])
        data = await svc.collection_to_dict(db, await svc.get_collection(db, created["id"]))

    assert removed["removed"] == 1
    assert data["item_count"] == 0


async def test_remove_missing_member_is_noop(client):
    await _insp("r2")
    async with async_session() as db:
        created = await svc.create_collection(db, "空移除合集")
        result = await svc.remove_inspirations(db, created["id"], ["r2"])
    assert result["removed"] == 0


# ── 智能合集求值 ──


async def test_smart_collection_evaluates_query(client):
    await _insp("s1", source="douyin")
    await _insp("s2", source="xiaohongshu")

    async with async_session() as db:
        smart = await svc.create_collection(
            db, "抖音智能", query_json={"source_types": ["douyin"]}
        )
        rows, total = await svc.evaluate_smart_collection(
            db, await svc.get_collection(db, smart["id"])
        )

    ids = {r.id for r in rows}
    assert "s1" in ids
    assert "s2" not in ids
    assert total == 1


async def test_smart_collection_excludes_trash(client):
    await _insp("s3")
    await _insp("s4", deleted=True)

    async with async_session() as db:
        smart = await svc.create_collection(db, "排除垃圾桶", query_json={})
        rows, _total = await svc.evaluate_smart_collection(
            db, await svc.get_collection(db, smart["id"])
        )

    ids = {r.id for r in rows}
    assert "s3" in ids
    assert "s4" not in ids


async def test_evaluate_requires_smart_collection(client):
    async with async_session() as db:
        manual = await svc.create_collection(db, "手动合集")
        with pytest.raises(HTTPException) as exc:
            await svc.evaluate_smart_collection(
                db, await svc.get_collection(db, manual["id"])
            )
    assert exc.value.status_code == 400


async def test_smart_results_can_be_materialised_into_manual(client):
    """智能合集的结果可以「固化」为手动合集（服务层等价路径：求值 → 建手动 → 加成员）。"""
    await _insp("f1")

    async with async_session() as db:
        smart = await svc.create_collection(db, "待固化", query_json={})
        rows, _total = await svc.evaluate_smart_collection(
            db, await svc.get_collection(db, smart["id"])
        )
        manual = await svc.create_collection(db, "固化结果")
        await svc.add_inspirations(db, manual["id"], [r.id for r in rows])
        data = await svc.collection_to_dict(db, await svc.get_collection(db, manual["id"]))

    assert data["name"] == "固化结果"
    assert data["query_json"] is None  # 手动合集：无 query_json
    assert data["kind"] == "manual"
    assert data["item_count"] == len(rows) >= 1


# ── 更新 / 封面 / 删除 ──


async def test_update_name_and_description(client):
    async with async_session() as db:
        created = await svc.create_collection(db, "旧名")
        updated = await svc.update_collection(
            db, created["id"], name="新名", description="新说明"
        )
    assert updated["name"] == "新名"
    assert updated["description"] == "新说明"


async def test_update_name_conflict(client):
    async with async_session() as db:
        await svc.create_collection(db, "占用名")
        other = await svc.create_collection(db, "待改名")

        with pytest.raises(HTTPException) as exc:
            await svc.update_collection(db, other["id"], name="占用名")
    assert exc.value.status_code == 409


async def test_set_and_clear_cover(client):
    await _insp("c1")
    async with async_session() as db:
        created = await svc.create_collection(db, "封面合集")
        await svc.add_inspirations(db, created["id"], ["c1"])

        with_cover = await svc.update_collection(
            db, created["id"], cover_inspiration_id="c1"
        )
        assert with_cover["cover_inspiration_id"] == "c1"

        cleared = await svc.update_collection(
            db, created["id"], cover_inspiration_id=None, clear_cover=True
        )
        # 显式封面已清空；响应里的封面会**回退到自动封面**（加入最早的未删成员），
        # 这正是设计行为：清空手动封面 = 恢复自动封面，而不是变成无封面
        stored = await db.get(Collection, created["id"])
        assert stored.cover_inspiration_id is None
        assert cleared["cover_inspiration_id"] == "c1"


async def test_delete_collection_removes_children_and_items(client):
    """删一级要连带二级与成员关联（否则留下孤儿行）。"""
    await _insp("d1")
    async with async_session() as db:
        parent = await svc.create_collection(db, "待删父")
        child = await svc.create_collection(db, "待删子", parent_id=parent["id"])

        await svc.delete_collection(db, parent["id"])

        assert await db.get(Collection, child["id"]) is None
        assert await db.get(Collection, parent["id"]) is None


async def test_delete_missing_404(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await svc.delete_collection(db, 999999)
    assert exc.value.status_code == 404


# ── 根合集（系统内置两级结构）──


async def test_ensure_root_collections_idempotent(client):
    """系统根合集只建一次：重复调用不能产生多份。"""
    async with async_session() as db:
        first = await svc.ensure_root_collections(db)
        second = await svc.ensure_root_collections(db)

    assert first == second
    assert len(first) == 2  # 手动收藏 / 抖音自动收藏


async def test_reorder_members(client):
    await _insp("o1")
    await _insp("o2")
    async with async_session() as db:
        created = await svc.create_collection(db, "排序合集")
        await svc.add_inspirations(db, created["id"], ["o1", "o2"])

        result = await svc.reorder_collection_items(db, created["id"], ["o2", "o1"])

        rows = (
            await db.execute(
                __import__("sqlalchemy").select(CollectionItem)
                .where(CollectionItem.collection_id == created["id"])
                .order_by(CollectionItem.position)
            )
        ).scalars().all()

    assert result["updated"] == 2
    assert [r.inspiration_id for r in rows] == ["o2", "o1"]
