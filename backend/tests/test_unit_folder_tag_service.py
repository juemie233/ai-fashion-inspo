"""抖音收藏夹 → 穿搭大标签映射服务（`app/services/folder_tag_service.py`）补测。

既有 `tests/test_folder_tag_mapping.py` 走接口主路径；这里直接调服务函数，覆盖：
类别中文名、映射查询/序列化/总览合并（映射 ∪ 本地夹 ∪ f2 已扫描夹名）、
标签解析（同名建标签 / 类别冲突 / 显式复用改类别）、upsert 三种模式与参数校验、
删除映射、成员 ID（未删除过滤）、按映射重打（幂等 / 标签被删降级 / skip 与未配置）、
同步入口空名单与 skip、全量重打与单夹重打、标签影响面等分支与 DB 副作用。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.collection import Collection, CollectionItem
from app.models.folder_tag import (
    MODE_PENDING,
    MODE_SKIP,
    MODE_TAG,
    DouyinFolderTagMap,
)
from app.models.tag import InspirationTag, Tag
from app.services import folder_tag_service as svc
from app.services.inspiration_tags import batch_add_tags


# ── 构造助手 ──


@pytest.fixture(autouse=True)
def _clean_folder_tag_maps():
    """``douyin_folder_tag_map`` 尚未纳入 conftest 的清表清单：本文件自行清理，避免用例串扰。"""
    db_path = settings.storage_root.parent / "fashion_inspo.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DELETE FROM douyin_folder_tag_map")
        conn.commit()
    finally:
        conn.close()


def _upload(client, upload) -> str:
    resp = upload()
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _raw_folder(
    name: str, *, parent_id: int | None = 1, auto_source: str | None = "douyin"
) -> int:
    async with async_session() as db:
        row = Collection(name=name, auto_source=auto_source, parent_id=parent_id)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def _add_member(collection_id: int, inspiration_id: str) -> None:
    async with async_session() as db:
        db.add(
            CollectionItem(
                collection_id=collection_id, inspiration_id=inspiration_id, position=0
            )
        )
        await db.commit()


def _tag(name: str, category: str = "outfit") -> Tag:
    return Tag(name=name, category=category, source="manual")


async def _setup_folder_with_member(client, upload, name: str) -> tuple[int, str]:
    # 同名一级根会撞同层唯一索引：每个夹用自己的根名，互不影响
    root = await _raw_folder(f"{name}-同步根", parent_id=None)
    folder = await _raw_folder(name, parent_id=root)
    insp_id = _upload(client, upload)
    await _add_member(folder, insp_id)
    return folder, insp_id


async def _get_row(folder_name: str) -> DouyinFolderTagMap | None:
    async with async_session() as db:
        return (
            await db.execute(
                select(DouyinFolderTagMap).where(
                    DouyinFolderTagMap.folder_name == folder_name
                )
            )
        ).scalar_one_or_none()


async def _tag_by_name(name: str) -> Tag | None:
    async with async_session() as db:
        return (
            await db.execute(select(Tag).where(Tag.name == name))
        ).scalar_one_or_none()


async def _upsert(folder_name: str, **kwargs) -> dict:
    async with async_session() as db:
        return await svc.upsert_mapping(db, folder_name, **kwargs)


# ══════════════════════════════════════════════════════════════
#  基础：类别中文名 / 查询 / 序列化 / 总览合并
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("style", "风格"),
        ("outfit", "穿搭大标签"),
        ("free", "自定义"),
        ("unknown_cat", "unknown_cat"),  # 未登记类别原样透传
        (None, "未知"),
        ("", "未知"),
    ],
)
def test_category_label(category, expected):  # noqa: ANN001
    assert svc._category_label(category) == expected


async def test_get_mapping_strips_name_and_handles_missing():
    with pytest.raises(HTTPException) as empty_exc:
        await _upsert("   ", mode=MODE_PENDING)
    assert empty_exc.value.status_code == 400
    assert empty_exc.value.detail == "收藏夹名称不能为空"

    await _upsert("通勤夹", mode=MODE_TAG, new_tag_name="通勤")
    async with async_session() as db:
        row = await svc.get_mapping(db, "  通勤夹  ")
        assert row is not None and row.folder_name == "通勤夹"
        assert await svc.get_mapping(db, "不存在的夹") is None


async def test_local_folder_collections_filters_roots_and_other_sources():
    await _raw_folder("抖音一级根", parent_id=None)
    second = await _raw_folder("抖音二级夹", parent_id=1)
    await _raw_folder("别的自动源", parent_id=1, auto_source="f2_other")
    manual = await _raw_folder("手动合集", parent_id=1, auto_source=None)

    async with async_session() as db:
        local = await svc._local_folder_collections(db)
    assert set(local) == {"抖音二级夹"}
    assert local["抖音二级夹"].id == second
    assert manual not in [c.id for c in local.values()]


async def test_known_folder_names_success_and_failure(monkeypatch):
    from app.services.task_runners import f2_personal

    monkeypatch.setattr(
        f2_personal, "load_collect_folder_map", lambda root: {"b夹": {}, "a夹": {}}
    )
    assert svc._known_folder_names() == ["a夹", "b夹"]

    def _boom(_root):
        raise OSError("清单损坏")

    monkeypatch.setattr(f2_personal, "load_collect_folder_map", _boom)
    assert svc._known_folder_names() == []


async def test_row_dict_counts_only_live_members(client, upload):
    folder, member = await _setup_folder_with_member(client, upload, "计数夹")
    trashed = _upload(client, upload)
    client.post(f"/api/inspirations/{trashed}/trash")
    await _add_member(folder, trashed)

    row = (await _upsert("计数夹", mode=MODE_TAG, new_tag_name="计数标签"))
    async with async_session() as db:
        mapping = await svc.get_mapping(db, "计数夹")
        data = await svc._row_dict(db, mapping)

    assert data == {
        "folder_name": "计数夹",
        "mode": MODE_TAG,
        "needs_confirm": False,
        "tag_id": row["tag_id"],
        "tag_name": "计数标签",
        "tag_category": "outfit",
        "local_collection_id": folder,
        "local_item_count": 1,  # 垃圾桶成员不计
    }

    # 未配置映射的本地夹：tag 字段全空
    empty_folder, _ = await _setup_folder_with_member(client, upload, "无标签夹")
    async with async_session() as db:
        raw = DouyinFolderTagMap(folder_name="无标签夹", mode=MODE_PENDING)
        db.add(raw)
        await db.commit()
        data = await svc._row_dict(db, raw)
    assert data["tag_name"] is None and data["tag_category"] is None
    assert data["local_collection_id"] == empty_folder


async def test_list_mappings_merges_mapped_local_and_known(client, upload, monkeypatch):
    await _setup_folder_with_member(client, upload, "本地夹")
    await _upsert("已映射夹", mode=MODE_TAG, new_tag_name="已映射标签")
    monkeypatch.setattr(svc, "_known_folder_names", lambda: ["预配置夹"])

    async with async_session() as db:
        rows = await svc.list_mappings(db)

    by_name = {row["folder_name"]: row for row in rows}
    assert set(by_name) >= {"本地夹", "已映射夹", "预配置夹"}
    assert by_name["已映射夹"]["mode"] == MODE_TAG
    assert by_name["已映射夹"]["tag_name"] == "已映射标签"
    # 未建映射的本地夹：pending 且带本地合集与成员数
    assert by_name["本地夹"]["mode"] == MODE_PENDING
    assert by_name["本地夹"]["local_collection_id"] is not None
    assert by_name["本地夹"]["local_item_count"] == 1
    # f2 已扫描但还没本地合集的夹：pending，无本地信息
    assert by_name["预配置夹"]["mode"] == MODE_PENDING
    assert by_name["预配置夹"]["local_collection_id"] is None
    assert by_name["预配置夹"]["local_item_count"] is None


# ══════════════════════════════════════════════════════════════
#  upsert_mapping：参数校验 / 三种模式 / 冲突与复用
# ══════════════════════════════════════════════════════════════


async def test_upsert_rejects_bad_mode():
    with pytest.raises(HTTPException) as exc:
        await _upsert("模式夹", mode="weird")
    assert exc.value.status_code == 400
    assert exc.value.detail == "不支持的映射模式：weird"


async def test_upsert_tag_by_id_missing_and_outfit_ok():
    async with async_session() as db:
        with pytest.raises(HTTPException) as missing:
            await svc.upsert_mapping(db, "悬空标签夹", mode=MODE_TAG, tag_id=999999)
        assert missing.value.status_code == 404
        assert missing.value.detail == "标签未找到"

    async with async_session() as db:
        tag = _tag("已存在大标签")
        db.add(tag)
        await db.commit()
        await db.refresh(tag)
        tag_id = tag.id
    data = await _upsert("大标签夹", mode=MODE_TAG, tag_id=tag_id)
    assert data["tag_id"] == tag_id
    assert data["tag_name"] == "已存在大标签"
    assert data["tag_category"] == "outfit"
    assert data["needs_confirm"] is False


async def test_upsert_tag_by_id_conflict_then_reuse_changes_category():
    async with async_session() as db:
        tag = _tag("复古", category="style")
        db.add(tag)
        await db.commit()
        await db.refresh(tag)
        tag_id = tag.id

    with pytest.raises(HTTPException) as conflict:
        await _upsert("复古夹", mode=MODE_TAG, tag_id=tag_id)
    assert conflict.value.status_code == 409
    assert "已被" in conflict.value.detail
    assert "风格" in conflict.value.detail

    reused = await _upsert(
        "复古夹", mode=MODE_TAG, tag_id=tag_id, reuse_existing_category=True
    )
    assert reused["tag_category"] == "outfit"
    assert (await _tag_by_name("复古")).category == "outfit"


async def test_upsert_default_tag_name_is_folder_name():
    data = await _upsert("通勤穿搭", mode=MODE_TAG)
    assert data["tag_name"] == "通勤穿搭"
    assert data["tag_category"] == "outfit"
    assert data["needs_confirm"] is False
    assert await _tag_by_name("通勤穿搭") is not None


async def test_upsert_new_tag_name_conflict_and_reuse():
    async with async_session() as db:
        db.add(_tag("极简风", category="style"))
        await db.commit()

    with pytest.raises(HTTPException) as conflict:
        await _upsert("极简", mode=MODE_TAG, new_tag_name="极简风")
    assert conflict.value.status_code == 409
    assert "风格" in conflict.value.detail

    reused = await _upsert(
        "极简", mode=MODE_TAG, new_tag_name="极简风", reuse_existing_category=True
    )
    assert reused["tag_category"] == "outfit"
    assert (await _tag_by_name("极简风")).category == "outfit"


async def test_upsert_new_tag_name_reuses_existing_outfit_tag():
    """按名称解析时同名标签已是 outfit → 直接复用，不建新标签、不冲突。"""
    async with async_session() as db:
        db.add(_tag("复用大标签"))
        await db.commit()
        tag, conflict = await svc._ensure_outfit_tag(
            db, "复用大标签", reuse_existing_category=False
        )
        assert conflict is None
        assert tag is not None and tag.category == "outfit"

    data = await _upsert("复用标签夹", mode=MODE_TAG, new_tag_name="复用大标签")
    assert data["tag_name"] == "复用大标签"
    assert data["tag_category"] == "outfit"

    async with async_session() as db:
        tags = (
            await db.execute(select(Tag).where(Tag.name == "复用大标签"))
        ).scalars().all()
    assert len(tags) == 1  # 没有重复建同名标签


async def test_upsert_empty_new_tag_name_rejected():
    with pytest.raises(HTTPException) as exc:
        await _upsert("空名夹", mode=MODE_TAG, new_tag_name="   ")
    assert exc.value.status_code == 400
    assert exc.value.detail == "标签名称不能为空"


async def test_upsert_skip_and_pending_reset_existing_row():
    first = await _upsert("重置夹", mode=MODE_TAG)
    first_tag_id = first["tag_id"]
    assert first_tag_id is not None

    skipped = await _upsert("重置夹", mode=MODE_SKIP)
    assert skipped["mode"] == MODE_SKIP
    assert skipped["tag_id"] is None
    assert skipped["needs_confirm"] is False

    pending = await _upsert("重置夹", mode=MODE_PENDING)
    assert pending["mode"] == MODE_PENDING
    assert pending["tag_id"] is None

    async with async_session() as db:
        rows = (
            await db.execute(
                select(DouyinFolderTagMap).where(
                    DouyinFolderTagMap.folder_name == "重置夹"
                )
            )
        ).scalars().all()
    assert len(rows) == 1  # 更新而非新增


async def test_upsert_same_folder_twice_updates_row(client, upload):
    first = await _upsert("换标签夹", mode=MODE_TAG, new_tag_name="旧标签")
    second = await _upsert("换标签夹", mode=MODE_TAG, new_tag_name="新标签")
    assert first["tag_id"] != second["tag_id"]

    async with async_session() as db:
        rows = (
            await db.execute(
                select(DouyinFolderTagMap).where(
                    DouyinFolderTagMap.folder_name == "换标签夹"
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].tag_id == second["tag_id"]


# ══════════════════════════════════════════════════════════════
#  删除映射 / 成员 ID / 重打 / 同步入口 / 影响面
# ══════════════════════════════════════════════════════════════


async def test_delete_mapping_missing_and_success():
    async with async_session() as db:
        with pytest.raises(HTTPException) as missing:
            await svc.delete_mapping(db, "不存在的映射")
        assert missing.value.status_code == 404
        assert missing.value.detail == "映射不存在"

    await _upsert("待删映射", mode=MODE_TAG)
    async with async_session() as db:
        result = await svc.delete_mapping(db, "待删映射")
    assert result == {"folder_name": "待删映射", "deleted": True}
    assert await _get_row("待删映射") is None


async def test_folder_member_ids_missing_and_live_only(client, upload):
    async with async_session() as db:
        assert await svc.folder_member_ids(db, "不存在的夹") == []

    folder, member = await _setup_folder_with_member(client, upload, "成员夹")
    trashed = _upload(client, upload)
    client.post(f"/api/inspirations/{trashed}/trash")
    await _add_member(folder, trashed)

    async with async_session() as db:
        ids = await svc.folder_member_ids(db, "  成员夹  ")
    assert ids == [member]


async def test_apply_folder_tags_without_mapping_or_skip_mode(client, upload):
    insp_id = _upload(client, upload)
    async with async_session() as db:
        assert await svc.apply_folder_tags(db, "未配置夹", [insp_id]) == {
            "applied": 0,
            "reason": "该收藏夹未配置大标签",
        }

    await _upsert("跳过夹", mode=MODE_SKIP)
    async with async_session() as db:
        assert await svc.apply_folder_tags(db, "跳过夹", [insp_id]) == {
            "applied": 0,
            "reason": "该收藏夹未配置大标签",
        }


async def test_apply_folder_tags_appends_links_and_idempotent(client, upload):
    insp_a = _upload(client, upload)
    insp_b = _upload(client, upload)
    mapped = await _upsert("重打夹", mode=MODE_TAG)

    async with async_session() as db:
        first = await svc.apply_folder_tags(db, "重打夹", [insp_a, insp_b, "", None])
    assert first["applied"] == 2  # 空 ID 跳过

    async with async_session() as db:
        links = (
            await db.execute(
                select(InspirationTag).where(
                    InspirationTag.tag_id == mapped["tag_id"],
                    InspirationTag.inspiration_id.in_([insp_a, insp_b]),
                )
            )
        ).scalars().all()
    assert {link.inspiration_id for link in links} == {insp_a, insp_b}
    assert {link.source for link in links} == {"collection"}

    async with async_session() as db:
        again = await svc.apply_folder_tags(db, "重打夹", [insp_a, insp_b])
    assert again["applied"] == 0  # 幂等：只追加不重复


async def test_apply_folder_tags_deleted_tag_degrades_to_pending(client, upload):
    insp_id = _upload(client, upload)
    mapped = await _upsert("标签被删夹", mode=MODE_TAG)
    async with async_session() as db:
        row = await svc.get_mapping(db, "标签被删夹")
        row.tag_id = None  # 模拟 FK ondelete=SET NULL，mode 仍为 tag
        await db.commit()

    async with async_session() as db:
        result = await svc.apply_folder_tags(db, "标签被删夹", [insp_id])
    assert result["applied"] == 0
    assert "已被删除" in result["reason"]

    row = await _get_row("标签被删夹")
    assert row.mode == MODE_PENDING
    assert row.tag_id is None
    assert row.needs_confirm is True
    assert mapped["tag_id"] is not None  # 原映射确实指向过标签


async def test_apply_on_sync_empty_ids_and_skip_mapping(client, upload):
    async with async_session() as db:
        assert await svc.apply_on_sync(db, "任意夹", []) == {"applied": 0}

    await _upsert("同步跳过夹", mode=MODE_SKIP)
    insp_id = _upload(client, upload)
    async with async_session() as db:
        assert await svc.apply_on_sync(db, "同步跳过夹", [insp_id]) == {
            "applied": 0,
            "skipped": True,
        }


async def test_apply_all_single_and_all_mapped(client, upload):
    await _setup_folder_with_member(client, upload, "全量夹1")
    await _setup_folder_with_member(client, upload, "全量夹2")
    await _upsert("全量夹1", mode=MODE_TAG)
    await _upsert("全量夹2", mode=MODE_TAG)
    # skip/pending 不应参与「全部重打」
    await _setup_folder_with_member(client, upload, "全量跳过夹")
    await _upsert("全量跳过夹", mode=MODE_SKIP)

    async with async_session() as db:
        all_result = await svc.apply_all(db)
    assert all_result["applied"] == 2
    assert {row["folder_name"] for row in all_result["folders"]} == {
        "全量夹1",
        "全量夹2",
    }

    # 单夹重打：已打过 → 幂等 0
    async with async_session() as db:
        single = await svc.apply_all(db, "全量夹1")
    assert single["applied"] == 0
    assert [row["folder_name"] for row in single["folders"]] == ["全量夹1"]


async def test_apply_all_without_local_collection_returns_zero():
    """已映射但本地还没建合集（首次同步前）：成员为空，重打 0 且不报错。"""
    await _upsert("仅映射夹", mode=MODE_TAG)
    async with async_session() as db:
        result = await svc.apply_all(db, "仅映射夹")
    assert result["applied"] == 0
    assert result["folders"] == [
        {"folder_name": "仅映射夹", "applied": 0, "skipped": 0}
    ]


async def test_tag_impact_missing_and_full(client, upload):
    async with async_session() as db:
        with pytest.raises(HTTPException) as missing:
            await svc.tag_impact(db, 999999)
        assert missing.value.status_code == 404
        assert missing.value.detail == "标签未找到"

    insp_a = _upload(client, upload)
    insp_b = _upload(client, upload)
    async with async_session() as db:
        tag = _tag("影响面标签")
        db.add(tag)
        await db.commit()
        await db.refresh(tag)
        tag_id = tag.id
        await batch_add_tags(
            db, [insp_a, insp_b], ["影响面标签"], category="outfit", source="manual"
        )
    await _upsert("影响夹A", mode=MODE_TAG, tag_id=tag_id)
    await _upsert("影响夹B", mode=MODE_TAG, tag_id=tag_id)

    async with async_session() as db:
        data = await svc.tag_impact(db, tag_id)

    assert data["tag_id"] == tag_id
    assert data["name"] == "影响面标签"
    assert data["category"] == "outfit"
    assert data["usage_count"] == 2
    assert set(data["mapped_folders"]) == {"影响夹A", "影响夹B"}
