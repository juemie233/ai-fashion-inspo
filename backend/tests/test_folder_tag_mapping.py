"""抖音收藏夹 → 穿搭大标签映射测试（2026-11-24）。

覆盖：
- 首次同步前预配置（还没有本地合集时也能建映射）；
- 未配置 → 同名自动建标签 + 打标 + 待确认；
- 同名标签类别冲突 409，两条修复路径（改类别 / 另起名）；
- skip / pending；重复打标幂等；
- 成员移出后标签保留（快照语义）；
- 关联来源 ``collection`` 不会被 AI 重分析清除（写入值断言）。
"""

from urllib.parse import quote

from sqlalchemy import select

from app.database import async_session
from app.models.collection import Collection, CollectionItem
from app.models.folder_tag import DouyinFolderTagMap
from app.models.tag import InspirationTag, Tag
from app.services import folder_tag_service
from app.services.inspiration_tags import batch_add_tags


def _path(folder_name: str) -> str:
    """URL 里的中文收藏夹名做百分号编码。"""
    return quote(folder_name, safe="")


async def _create_raw_douyin_folder(
    name: str, *, parent_id: int | None = None
) -> int:
    async with async_session() as db:
        row = Collection(
            name=name, auto_source="douyin", parent_id=parent_id
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def _add_member(collection_id: int, insp_id: str) -> None:
    async with async_session() as db:
        db.add(
            CollectionItem(
                collection_id=collection_id, inspiration_id=insp_id, position=1
            )
        )
        await db.commit()


async def _create_other_category_tag(name: str, category: str = "style") -> int:
    async with async_session() as db:
        tag = Tag(name=name, category=category, source="manual")
        db.add(tag)
        await db.commit()
        await db.refresh(tag)
        return tag.id


def _upsert(client, **payload):
    return client.post("/api/admin/douyin-folder-tags", json=payload)


def test_preconfigure_mapping_before_first_sync(client):
    """还没有本地合集/同步记录时，也能按收藏夹名预配置映射。"""
    resp = _upsert(client, folder_name="通勤穿搭", mode="tag")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "tag"
    assert body["tag_name"] == "通勤穿搭"
    assert body["tag_category"] == "outfit"
    assert body["needs_confirm"] is False

    listed = client.get("/api/admin/douyin-folder-tags").json()
    row = next(r for r in listed if r["folder_name"] == "通勤穿搭")
    assert row["mode"] == "tag"
    assert row["tag_name"] == "通勤穿搭"


async def test_conflict_returns_409_then_reuse_changes_category(client):
    """同名标签已属 style 类：409 → 复用并改为 outfit 是两条明确路径。"""
    await _create_other_category_tag("极简风", "style")

    conflict = _upsert(client, folder_name="极简", mode="tag", new_tag_name="极简风")
    assert conflict.status_code == 409
    assert "已被" in conflict.json()["detail"]

    # 路径 A：改映射到已有标签（不同名）——不会撞名
    renamed = _upsert(client, folder_name="极简", mode="tag", new_tag_name="极简穿搭")
    assert renamed.status_code == 200, renamed.text

    # 路径 B：复用同名标签并把类别改为 outfit（显式确认）
    reused = _upsert(
        client,
        folder_name="极简",
        mode="tag",
        new_tag_name="极简风",
        reuse_existing_category=True,
    )
    assert reused.status_code == 200, reused.text
    assert reused.json()["tag_category"] == "outfit"

    async with async_session() as db:
        tag = (
            await db.execute(select(Tag).where(Tag.name == "极简风"))
        ).scalar_one()
    assert tag.category == "outfit"


async def test_conflict_via_tag_id(client):
    """直接选已有标签时同样的冲突规则。"""
    tag_id = await _create_other_category_tag("复古", "atmosphere")
    conflict = _upsert(client, folder_name="复古夹", mode="tag", tag_id=tag_id)
    assert conflict.status_code == 409
    ok = _upsert(
        client,
        folder_name="复古夹",
        mode="tag",
        tag_id=tag_id,
        reuse_existing_category=True,
    )
    assert ok.status_code == 200, ok.text


def test_skip_and_reset_pending(client):
    assert _upsert(client, folder_name="不想打标", mode="skip").json()["mode"] == "skip"
    assert (
        _upsert(client, folder_name="不想打标", mode="pending").json()["mode"]
        == "pending"
    )


async def test_apply_tags_folder_members_with_collection_source(client, upload):
    """重打：只给该夹未删除成员打标，关联来源为 collection。"""
    root_id = await _create_raw_douyin_folder("抖音入库自动收藏")
    folder_id = await _create_raw_douyin_folder("通勤", parent_id=root_id)
    insp_id = upload().json()["id"]
    await _add_member(folder_id, insp_id)

    assert _upsert(client, folder_name="通勤", mode="tag").status_code == 200
    resp = client.post(f"/api/admin/douyin-folder-tags/{_path('通勤')}/apply")
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == 1

    async with async_session() as db:
        link = (
            await db.execute(
                select(InspirationTag).where(
                    InspirationTag.inspiration_id == insp_id
                )
            )
        ).scalars().all()
        tag = (
            await db.execute(select(Tag).where(Tag.name == "通勤"))
        ).scalar_one()
    assert len(link) == 1
    assert link[0].tag_id == tag.id
    assert link[0].source == "collection"

    # 幂等：再重打一次不新增关联
    again = client.post(f"/api/admin/douyin-folder-tags/{_path('通勤')}/apply").json()
    assert again["applied"] == 0

    # 快照语义：成员被移出合集后，标签关联保留
    client.request(
        "DELETE",
        f"/api/collections/{folder_id}/inspirations",
        json={"inspiration_ids": [insp_id]},
    )
    async with async_session() as db:
        remains = (
            await db.execute(
                select(InspirationTag).where(
                    InspirationTag.inspiration_id == insp_id,
                    InspirationTag.tag_id == tag.id,
                )
            )
        ).scalar_one_or_none()
    assert remains is not None


async def test_sync_auto_creates_tag_and_pending(client, upload):
    """未配置时同步入口：同名建标签 + 打标 + needs_confirm=True。"""
    insp_id = upload().json()["id"]
    async with async_session() as db:
        result = await folder_tag_service.apply_on_sync(db, "未配置夹", [insp_id])
    assert result["applied"] == 1
    assert result["needs_confirm"] is True

    async with async_session() as db:
        row = (
            await db.execute(
                select(DouyinFolderTagMap).where(
                    DouyinFolderTagMap.folder_name == "未配置夹"
                )
            )
        ).scalar_one()
        assert row.mode == "tag"
        assert row.needs_confirm is True
        tag = await db.get(Tag, row.tag_id)
        assert tag is not None and tag.category == "outfit"


async def test_sync_conflict_marks_pending_without_tagging(client, upload):
    """同名标签类别冲突：不建关联，映射置 pending 等用户选择。"""
    await _create_other_category_tag("冲突夹", "style")
    insp_id = upload().json()["id"]
    async with async_session() as db:
        result = await folder_tag_service.apply_on_sync(db, "冲突夹", [insp_id])
    assert result["applied"] == 0
    assert result.get("conflict")

    async with async_session() as db:
        row = (
            await db.execute(
                select(DouyinFolderTagMap).where(
                    DouyinFolderTagMap.folder_name == "冲突夹"
                )
            )
        ).scalar_one()
        assert row.mode == "pending"
        assert row.tag_id is None


async def test_batch_add_tags_link_source(client, upload):
    """关联来源可指定：收藏夹自动打标写 collection，AI 重分析不会清除。"""
    insp_id = upload().json()["id"]
    async with async_session() as db:
        await batch_add_tags(
            db,
            [insp_id],
            ["来源测试"],
            category="outfit",
            source="manual",
            link_source="collection",
        )
        link = (
            await db.execute(
                select(InspirationTag).where(
                    InspirationTag.inspiration_id == insp_id
                )
            )
        ).scalar_one()
    assert link.source == "collection"


async def test_tag_impact(client):
    tag_id = await _create_other_category_tag("影响面", "outfit")
    assert (
        _upsert(client, folder_name="影响面夹", mode="tag", tag_id=tag_id).status_code
        == 200
    )
    data = client.get(f"/api/admin/douyin-folder-tags/tag-impact/{tag_id}")
    assert data.status_code == 200
    body = data.json()
    assert body["tag_id"] == tag_id
    assert body["usage_count"] >= 0
    # 该标签至少被刚建的映射引用（共享测试库下不强制列表长度）
    assert "影响面夹" in body["mapped_folders"]


async def test_deleted_tag_degrades_mapping_to_pending(client, upload):
    """映射的标签被删（FK 置空）后：重打要降级为待确认，而不是永远「未配置」。"""
    root_id = await _create_raw_douyin_folder("抖音入库自动收藏")
    folder_id = await _create_raw_douyin_folder("删标签夹", parent_id=root_id)
    insp_id = upload().json()["id"]
    await _add_member(folder_id, insp_id)

    mapped = _upsert(client, folder_name="删标签夹", mode="tag").json()
    assert mapped["tag_id"]

    # 模拟"标签被删除"：外键 ondelete=SET NULL 把 tag_id 置空、mode 仍为 tag
    async with async_session() as db:
        row = (
            await db.execute(
                select(DouyinFolderTagMap).where(
                    DouyinFolderTagMap.folder_name == "删标签夹"
                )
            )
        ).scalar_one()
        row.tag_id = None
        await db.commit()

    resp = client.post(f"/api/admin/douyin-folder-tags/{_path('删标签夹')}/apply")
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == 0

    async with async_session() as db:
        row = (
            await db.execute(
                select(DouyinFolderTagMap).where(
                    DouyinFolderTagMap.folder_name == "删标签夹"
                )
            )
        ).scalar_one()
    assert row.mode == "pending"
    assert row.needs_confirm is True
    assert row.tag_id is None
