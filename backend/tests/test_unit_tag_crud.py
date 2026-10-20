"""标签 CRUD 服务层用例（补齐 `tag_crud.py` 30% 覆盖率的盲区）。

为什么需要这一层：这里面全是**破坏性写路径**——
- `merge_tags` 会把源标签的关联改指目标、删掉重复关联、**搬迁别名**（别名若被
  cascade 物理删除就是数据丢失）、最后删源标签；
- `delete_unused_tags` / `batch_delete_tags` 会级联删除关联，回滚全靠快照里的
  `deleted_links`；
- `update_tag` 改名要查主标签/别名冲突、要登记向量重建、要写操作历史；
- `move_tags` 带**批内环检测**（A→B 且 B→A 同批必须双拒）。

API 用例只验证「列表里还有没有这个标签」，而回滚快照、别名搬迁、审计与历史行
都只在这里可见。这些用例于 2026-10-20 首次覆盖该模块的核心写路径。
"""

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.audit import AuditLog
from app.models.tag import InspirationTag, Tag, TagAlias
from app.models.tag_history import TagHistory
from app.services import tag_crud


async def _make_tag(name: str, category: str = "free") -> int:
    """建一个标签并提交，返回 ID。"""
    async with async_session() as db:
        tag = await tag_crud.create_tag(db, name, category)
        await db.commit()
        return int(tag.id)


async def _link(insp_id: str, tag_id: int, source: str = "manual") -> None:
    async with async_session() as db:
        db.add(
            InspirationTag(
                inspiration_id=insp_id, tag_id=tag_id, confidence=1.0, source=source
            )
        )
        await db.commit()


async def _linked_names(insp_id: str) -> list[str]:
    async with async_session() as db:
        rows = (
            await db.execute(
                select(Tag.name)
                .join(InspirationTag, InspirationTag.tag_id == Tag.id)
                .where(InspirationTag.inspiration_id == insp_id)
            )
        ).scalars().all()
    return sorted(rows)


async def _tag_names() -> set[str]:
    async with async_session() as db:
        rows = (await db.execute(select(Tag.name))).scalars().all()
    return set(rows)


async def _aliases(tag_id: int) -> set[str]:
    async with async_session() as db:
        rows = (
            await db.execute(select(TagAlias.alias).where(TagAlias.tag_id == tag_id))
        ).scalars().all()
    return set(rows)


async def _audit_actions() -> list[tuple[str, int]]:
    async with async_session() as db:
        rows = (await db.execute(select(AuditLog.action, AuditLog.count))).all()
    return [(str(r[0]), int(r[1])) for r in rows]


async def _history_ops() -> list[str]:
    async with async_session() as db:
        rows = (await db.execute(select(TagHistory.operation))).scalars().all()
    return [str(r) for r in rows]


async def test_merge_tags_moves_links_dedupes_and_deletes_source(client, upload):
    """合并：关联改指目标、重复关联删除、源标签消失、审计与历史留痕。"""
    insp_a = upload().json()["id"]
    insp_b = upload().json()["id"]
    src_id = await _make_tag("源标签")
    dst_id = await _make_tag("目标标签")
    await _link(insp_a, src_id)
    await _link(insp_b, src_id)
    await _link(insp_a, dst_id)  # 与源标签的关联重复：合并后只应留一条

    async with async_session() as db:
        await tag_crud.merge_tags(db, src_id, dst_id)

    # a 只保留目标标签一条（重复关联被删），b 的关联被改指到目标
    assert await _linked_names(insp_a) == ["目标标签"]
    assert await _linked_names(insp_b) == ["目标标签"]
    assert await _tag_names() == {"目标标签"}  # 源标签已删除
    assert ("merge_tags", 2) in await _audit_actions()  # count = 受影响素材数
    assert "merge" in await _history_ops()


async def test_merge_tags_moves_aliases_instead_of_dropping_them(client):
    """合并必须**搬迁**源标签的别名（cascade delete-orphan 会物理删除，等于丢数据）。

    （合并逻辑里「目标已有同名别名 → 删除源的那条」分支在当前 schema 下不可达：
    `tag_aliases.alias` 是全局唯一索引，两条同名的别名本来就存不进去。它是并发
    场景的防御代码，这里只验证可构造的「搬迁」路径。）
    """
    src_id = await _make_tag("源标签")
    dst_id = await _make_tag("目标标签")
    async with async_session() as db:
        db.add(TagAlias(tag_id=src_id, alias="源独有别"))
        db.add(TagAlias(tag_id=dst_id, alias="目标已有别名"))
        await db.commit()

    async with async_session() as db:
        await tag_crud.merge_tags(db, src_id, dst_id)

    assert await _aliases(dst_id) == {"源独有别", "目标已有别名"}
    async with async_session() as db:
        total = len((await db.execute(select(TagAlias.id))).scalars().all())
    assert total == 2  # 别名没有随源标签一起被物理删除


async def test_update_tag_rename_conflicts_and_history(client, upload):
    """改名：写 rename 历史；与主标签/别名冲突抛 TagConflictError；不存在抛 TagNotFoundError。"""
    insp_id = upload().json()["id"]
    tag_id = await _make_tag("旧名字")
    await _link(insp_id, tag_id)
    other_id = await _make_tag("另一个标签")
    async with async_session() as db:
        db.add(TagAlias(tag_id=other_id, alias="别人家的别名"))
        await db.commit()

    async with async_session() as db:
        tag = await tag_crud.update_tag(db, tag_id, name="新名字")
        await db.commit()
    assert tag.name == "新名字"
    assert "rename" in await _history_ops()

    async with async_session() as db:
        with pytest.raises(tag_crud.TagConflictError):
            await tag_crud.update_tag(db, tag_id, name="另一个标签")

    async with async_session() as db:
        with pytest.raises(tag_crud.TagConflictError):
            await tag_crud.update_tag(db, tag_id, name="别人家的别名")

    async with async_session() as db:
        with pytest.raises(tag_crud.TagNotFoundError):
            await tag_crud.update_tag(db, 999999, name="随便")


async def test_update_tag_category_and_flags(client):
    """类别/置顶/排序/备注：字段生效，历史操作记为 category_change。"""
    tag_id = await _make_tag("穿搭", "free")

    async with async_session() as db:
        tag = await tag_crud.update_tag(
            db,
            tag_id,
            category="style",
            pinned=True,
            sort_order=7,
            description="备注文本",
        )
        await db.commit()

    assert tag.category == "style"
    assert tag.pinned is True
    assert tag.sort_order == 7
    assert tag.description == "备注文本"
    assert "category_change" in await _history_ops()


async def test_delete_unused_tags_keeps_linked_and_audits(client, upload):
    """只删「没有任何在用素材关联」的标签；二次调用为空。"""
    insp_id = upload().json()["id"]
    used_id = await _make_tag("在用的")
    unused_id = await _make_tag("没人用的")
    await _link(insp_id, used_id)

    async with async_session() as db:
        deleted = await tag_crud.delete_unused_tags(db)
    assert [t.id for t in deleted] == [unused_id]
    assert await _tag_names() == {"在用的"}
    assert ("delete_unused_tags", 1) in await _audit_actions()
    assert "delete" in await _history_ops()

    async with async_session() as db:
        assert await tag_crud.delete_unused_tags(db) == []


async def test_batch_delete_tags_cascades_links(client, upload):
    """批量删除：标签与关联一并删除，审计计数与历史快照落库。"""
    insp_id = upload().json()["id"]
    keep_id = await _make_tag("保留的")
    del_a = await _make_tag("删A")
    del_b = await _make_tag("删B")
    await _link(insp_id, keep_id)
    await _link(insp_id, del_a)

    async with async_session() as db:
        deleted = await tag_crud.batch_delete_tags(db, [del_a, del_b])
    assert sorted(t.name for t in deleted) == ["删A", "删B"]
    assert await _tag_names() == {"保留的"}
    assert await _linked_names(insp_id) == ["保留的"]
    assert ("batch_delete_tags", 2) in await _audit_actions()
    assert "delete" in await _history_ops()


async def test_reorder_tags_reports_missing_ids(client):
    """排序：全部命中才写库；有缺失 ID 时整体不生效并回报缺失列表。"""
    a = await _make_tag("排序A")
    b = await _make_tag("排序B")

    async with async_session() as db:
        count, missing = await tag_crud.reorder_tags(db, {a: 5, b: 1})
    assert (count, missing) == (2, [])

    async with async_session() as db:
        orders = dict((await db.execute(select(Tag.id, Tag.sort_order))).all())
    assert orders == {a: 5, b: 1}

    # 含不存在的 ID：整批不生效（避免「一半写了、一半没写」的中间态）
    async with async_session() as db:
        count, missing = await tag_crud.reorder_tags(db, {a: 99, 999999: 2})
    assert (count, missing) == (0, [999999])
    async with async_session() as db:
        assert (await db.get(Tag, a)).sort_order == 5


async def test_move_tags_cycle_detection_and_valid_moves(client):
    """层级移动：合法移动生效并写历史；自身/后代/不存在的父标签都要挡住。"""
    root = await _make_tag("根")
    child = await _make_tag("子")
    grand = await _make_tag("孙")

    async with async_session() as db:
        moved, errors = await tag_crud.move_tags(
            db, [{"tag_id": child, "parent_id": root}, {"tag_id": grand, "parent_id": child}]
        )
    assert (moved, errors) == (2, [])
    async with async_session() as db:
        assert (await db.get(Tag, grand)).parent_id == child
    assert "move" in await _history_ops()

    async with async_session() as db:
        moved, errors = await tag_crud.move_tags(
            db,
            [
                {"tag_id": root, "parent_id": grand},  # 成环：根挂到自己的孙子下
                {"tag_id": child, "parent_id": child},  # 挂到自己下面
                {"tag_id": grand, "parent_id": 999999},  # 父标签不存在
                {"tag_id": 999999, "parent_id": root},  # 自己不存在
            ],
        )
    assert moved == 0
    assert len(errors) == 4
    assert {e["tag_id"] for e in errors} == {root, child, grand, 999999}

    async with async_session() as db:
        moved, errors = await tag_crud.move_tags(db, [])
    assert moved == 0
    assert errors[0]["tag_id"] is None
