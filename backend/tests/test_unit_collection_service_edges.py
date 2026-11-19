"""合集服务的边界分支测试：损坏筛选条件、分类节点拦截、并发建根兜底、重排与固化。

补充 ``test_unit_collection_service.py`` 未覆盖的分支。合集是用户手工维护的数据，
这些边界（坏 JSON、分类节点误加、拖拽重排含非法 ID、智能合集固化）出错时
要么接口 500、要么素材被塞进「看不见」的分类节点，因此单独锁住。
"""

import json

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.database import async_session
from app.models.collection import Collection
from app.models.inspiration import Inspiration
from app.services import collection_service as svc


async def _insp(
    insp_id: str,
    *,
    deleted: bool = False,
    source: str = "douyin",
    favorite: bool = True,
    thumbnail: str | None = None,
) -> str:
    """直接写一条素材行（服务层测试不经过上传接口，避免文件 I/O）。"""
    from datetime import UTC, datetime

    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type=source,
                file_path=f"images/x/{insp_id}.jpg",
                thumbnail_path=thumbnail,
                media_type="image",
                quality_status="approved",
                is_favorite=favorite,
                deleted_at=datetime.now(UTC) if deleted else None,
            )
        )
        await db.commit()
    return insp_id


async def _smart_collection(name: str, query: dict) -> int:
    async with async_session() as db:
        created = await svc.create_collection(db, name, query_json=query)
    return created["id"]


# ── 损坏的 query_json 降级 ──


async def test_smart_collection_ignores_corrupt_query_json(client):
    """query_json 不是合法 JSON / 不是对象时按「无条件」求值，不能 500。"""
    await _insp("bad1")

    async with async_session() as db:
        broken = Collection(name="坏条件", position=1, query_json="{not-json")
        not_object = Collection(name="条件不是对象", position=2, query_json='"just-a-string"')
        db.add_all([broken, not_object])
        await db.commit()

        items, total = await svc.evaluate_smart_collection(db, broken)
        items2, total2 = await svc.evaluate_smart_collection(db, not_object)
        payload = await svc.collection_to_dict(db, not_object)

    assert total == 1 and len(items) == 1
    assert total2 == 1 and len(items2) == 1
    assert payload["query_json"] == {}  # 坏数据向前兼容成空条件


# ── 智能合集分页 ──


async def test_smart_collection_pagination_slices_result(client):
    """size 非 None 时按 page/size 分页，total 仍是全量匹配数。"""
    for insp_id in ("pg1", "pg2", "pg3"):
        await _insp(insp_id)
    await _insp("other", source="manual_upload")

    async with async_session() as db:
        collection = await svc.create_collection(
            db, "分页智能", query_json={"source_types": ["douyin"]}
        )
        fetched = await svc.get_collection(db, collection["id"])
        page1, total = await svc.evaluate_smart_collection(db, fetched, page=1, size=2)
        page2, _ = await svc.evaluate_smart_collection(db, fetched, page=2, size=2)

    assert total == 3
    assert len(page1) == 2
    assert len(page2) == 1


# ── 分类节点不能直接装素材 ──


async def test_add_inspirations_to_auto_root_rejected(client):
    """同步自动维护的一级分类节点不能直接加素材（素材归位到子收藏夹）。"""
    await _insp("auto1")
    async with async_session() as db:
        created = await svc.create_collection(
            db, "抖音入库自动收藏", auto_source="douyin"
        )
        with pytest.raises(HTTPException) as exc:
            await svc.add_inspirations(db, created["id"], ["auto1"])

    assert exc.value.status_code == 400
    assert "自动维护" in str(exc.value.detail)


async def test_add_inspirations_to_node_with_children_rejected(client):
    """已挂子收藏夹的节点是分类节点，往里加素材等于加到看不见的地方。"""
    await _insp("cat1")
    async with async_session() as db:
        parent = await svc.create_collection(db, "有子夹的一级")
        await svc.create_collection(db, "子夹", parent_id=parent["id"])

        with pytest.raises(HTTPException) as exc:
            await svc.add_inspirations(db, parent["id"], ["cat1"])

    assert exc.value.status_code == 400
    assert "分类节点" in str(exc.value.detail)


# ── 列表批量序列化：显式封面 ──


async def test_list_collections_prefers_explicit_cover(client):
    """批量序列化时显式封面优先，并一次取回缩略图；封面素材已删则回退。"""
    await _insp("cov1", thumbnail="thumbnails/2026-01/cov1.jpg")
    await _insp("cov2", thumbnail="thumbnails/2026-01/cov2.jpg", deleted=True)

    async with async_session() as db:
        collection = await svc.create_collection(db, "显式封面")
        await svc.add_inspirations(db, collection["id"], ["cov1"])
        await svc.update_collection(db, collection["id"], cover_inspiration_id="cov1")

        deleted_cover = await svc.create_collection(db, "封面已删")
        await svc.add_inspirations(db, deleted_cover["id"], ["cov2"])
        await svc.update_collection(
            db, deleted_cover["id"], cover_inspiration_id="cov2"
        )

        rows = {c["id"]: c for c in await svc.list_collections(db)}

    assert rows[collection["id"]]["cover_inspiration_id"] == "cov1"
    assert rows[collection["id"]]["cover_thumbnail_path"] == "thumbnails/2026-01/cov1.jpg"
    # 显式封面素材在垃圾桶里 → 按「无显式封面」处理，回退自动封面（无成员时为空）
    assert rows[deleted_cover["id"]]["cover_inspiration_id"] is None


# ── 并发建根兜底（IntegrityError）──


async def test_ensure_one_handles_concurrent_insert_race(client, monkeypatch):
    """SELECT 到 INSERT 之间被别的进程建出同名根：捕获 IntegrityError 后复用已有行。"""
    created_id = {"value": None}

    async with async_session() as db:
        real_commit = db.commit
        calls = {"n": 0}

        async def _commit_with_race():
            calls["n"] += 1
            if calls["n"] == 1:
                # 模拟另一个进程抢先提交同名一级节点
                async with async_session() as other:
                    row = Collection(name="竞态根", description="其它进程建", position=1)
                    other.add(row)
                    await other.commit()
                    created_id["value"] = row.id
                raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))
            await real_commit()

        monkeypatch.setattr(db, "commit", _commit_with_race)
        root_id = await svc._ensure_one(db, "竞态根", description="本进程建")

    assert root_id == created_id["value"]
    assert calls["n"] == 1


# ── update_collection 守卫 ──


async def test_update_manual_root_guards_name_and_query(client):
    """系统收藏根：改名/改筛选条件 400，但描述可以改（非结构性字段）。"""
    async with async_session() as db:
        roots = await svc.ensure_root_collections(db)
        root_id = roots[svc.MANUAL_ROOT_COLLECTION_NAME]

        with pytest.raises(HTTPException) as rename_exc:
            await svc.update_collection(db, root_id, name="改个名")
        with pytest.raises(HTTPException) as query_exc:
            await svc.update_collection(db, root_id, query_json={"is_favorite": True})

        updated = await svc.update_collection(db, root_id, description="新的说明")

    assert rename_exc.value.status_code == 400
    assert query_exc.value.status_code == 400
    assert updated["description"] == "新的说明"


async def test_update_auto_collection_name_rejected(client):
    """同步自动维护的收藏夹改名会与下一次同步并存两个夹，直接 400。"""
    async with async_session() as db:
        created = await svc.create_collection(db, "同步收藏夹", auto_source="douyin")
        with pytest.raises(HTTPException) as exc:
            await svc.update_collection(db, created["id"], name="用户改名")

    assert exc.value.status_code == 400
    assert "不可修改" in str(exc.value.detail)


async def test_update_cover_missing_returns_404(client):
    async with async_session() as db:
        created = await svc.create_collection(db, "封面404")
        with pytest.raises(HTTPException) as exc:
            await svc.update_collection(
                db, created["id"], cover_inspiration_id="no-such-inspiration"
            )

    assert exc.value.status_code == 404


async def test_update_manual_collection_query_json_rejected(client):
    """手动合集没有筛选条件语义，更新 query_json 必须 400 而不是静默忽略。"""
    async with async_session() as db:
        created = await svc.create_collection(db, "手动改条件")
        with pytest.raises(HTTPException) as exc:
            await svc.update_collection(db, created["id"], query_json={"is_favorite": True})

    assert exc.value.status_code == 400
    assert "手动合集" in str(exc.value.detail)


# ── 成员排序 ──


async def test_reorder_items_skips_duplicates_and_rejects_foreign(client):
    """重复 ID 去重；包含不属于该合集的素材返回 400。"""
    await _insp("r1")
    await _insp("r2")
    await _insp("foreign")

    async with async_session() as db:
        collection = await svc.create_collection(db, "重排成员")
        await svc.add_inspirations(db, collection["id"], ["r1", "r2"])

        result = await svc.reorder_collection_items(
            db, collection["id"], ["r2", "r2", "r1"]
        )
        with pytest.raises(HTTPException) as exc:
            await svc.reorder_collection_items(db, collection["id"], ["r2", "foreign"])

        items, total = await svc.list_collection_inspirations(db, collection["id"])

    assert result["updated"] == 2
    assert exc.value.status_code == 400
    assert [item.id for item in items] == ["r2", "r1"]
    assert total == 2


async def test_reorder_collections_orders_and_appends_rest(client):
    """提交顺序生效；未提交的合集按原 position 追加到末尾。"""
    async with async_session() as db:
        c1 = await svc.create_collection(db, "合集一")
        c2 = await svc.create_collection(db, "合集二")
        c3 = await svc.create_collection(db, "合集三")

        result = await svc.reorder_collections(db, [c3["id"], c1["id"], c1["id"]])
        rows = await svc.list_collections(db)

    assert result["updated"] == 3
    assert [row["id"] for row in rows] == [c3["id"], c1["id"], c2["id"]]


async def test_reorder_collections_unknown_id_returns_404(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await svc.reorder_collections(db, [424242])
    assert exc.value.status_code == 404


# ── 合集内容分页 ──


async def test_list_collection_inspirations_manual_paginates(client):
    """手动合集按 position 升序分页，total 为成员总数（排除垃圾桶）。"""
    await _insp("m1")
    await _insp("m2")
    await _insp("m3")
    await _insp("m4", deleted=True)

    async with async_session() as db:
        collection = await svc.create_collection(db, "分页手动")
        await svc.add_inspirations(db, collection["id"], ["m1", "m2", "m3", "m4"])

        page1, total = await svc.list_collection_inspirations(db, collection["id"], page=1, size=2)
        page2, _ = await svc.list_collection_inspirations(db, collection["id"], page=2, size=2)

    assert total == 3  # 垃圾桶成员不计入内容
    assert len(page1) == 2
    assert len(page2) == 1


# ── 智能合集固化 ──


async def test_solidify_smart_collection_becomes_manual(client):
    """固化：把当前匹配素材写进成员表并清空 query_json，之后可手动增删。"""
    await _insp("s1")
    await _insp("s2")
    await _insp("s3", source="manual_upload")

    async with async_session() as db:
        created = await svc.create_collection(
            db, "固化目标", query_json={"source_types": ["douyin"]}
        )
        solidified = await svc.solidify_collection(db, created["id"])
        items, total = await svc.list_collection_inspirations(db, created["id"])

        # 固化后再固化（已成手动）应 400
        with pytest.raises(HTTPException) as exc:
            await svc.solidify_collection(db, created["id"])

    assert solidified["kind"] == "manual"
    assert solidified["query_json"] is None
    assert solidified["item_count"] == 2
    assert total == 2 and len(items) == 2
    assert {item.id for item in items} == {"s1", "s2"}
    assert exc.value.status_code == 400


async def test_solidify_preserves_query_json_contract(client):
    """固化不删除合集，只把条件转成成员；JSON 序列化后的 query_json 为 null。"""
    await _insp("so1")
    async with async_session() as db:
        created = await svc.create_collection(
            db, "固化为空", query_json={"source_types": ["manual_upload"]}
        )
        solidified = await svc.solidify_collection(db, created["id"])

    assert solidified["query_json"] is None
    assert solidified["item_count"] == 0
    assert json.dumps(solidified["query_json"]) == "null"
