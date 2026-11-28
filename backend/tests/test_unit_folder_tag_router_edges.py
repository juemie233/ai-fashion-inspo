"""`app/routers/folder_tags.py` 管理接口的直调补测。

接口行为主路径已由 `tests/test_folder_tag_mapping.py` 覆盖；这里直调 async 路由函数
（传 AsyncSession，绕过 TestClient portal 线程的覆盖率漏记），补空结果、参数校验、
409 冲突、404 等错误分支与响应模型字段（FolderTagMappingOut / ApplyResult / ImpactOut）。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from app.config import settings
from app.database import async_session
from app.models.collection import Collection, CollectionItem
from app.models.tag import Tag
from app.routers import folder_tags as router_module
from app.schemas.folder_tag import FolderTagUpsertRequest
from app.services import folder_tag_service as svc


@pytest.fixture(autouse=True)
def _clean_folder_tag_maps():
    """该表尚未纳入 conftest 清表清单：本文件自行清理，避免跨用例串扰。"""
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


async def _setup_mapped_folder(client, upload, folder_name: str) -> tuple[int, str]:
    async with async_session() as db:
        root = Collection(name=f"{folder_name}-根", auto_source="douyin", parent_id=None)
        db.add(root)
        await db.commit()
        await db.refresh(root)
        folder = Collection(
            name=folder_name, auto_source="douyin", parent_id=root.id
        )
        db.add(folder)
        await db.commit()
        await db.refresh(folder)
        folder_id = folder.id
    insp_id = _upload(client, upload)
    async with async_session() as db:
        db.add(
            CollectionItem(collection_id=folder_id, inspiration_id=insp_id, position=0)
        )
        await db.commit()
    return folder_id, insp_id


async def test_list_router_empty_and_rows(client, monkeypatch):
    monkeypatch.setattr(svc, "_known_folder_names", lambda: [])
    async with async_session() as db:
        assert await router_module.list_folder_tag_mappings(db=db) == []

    async with async_session() as db:
        await svc.upsert_mapping(db, "映射夹", mode="tag", new_tag_name="映射标签")
    monkeypatch.setattr(svc, "_known_folder_names", lambda: ["预配置夹"])
    async with async_session() as db:
        rows = await router_module.list_folder_tag_mappings(db=db)

    by_name = {row.folder_name: row for row in rows}
    assert by_name["映射夹"].mode == "tag"
    assert by_name["映射夹"].tag_name == "映射标签"
    assert by_name["映射夹"].tag_category == "outfit"
    assert by_name["预配置夹"].mode == "pending"
    assert by_name["预配置夹"].tag_id is None


async def test_upsert_router_returns_model_and_raises_conflict():
    async with async_session() as db:
        created = await router_module.upsert_folder_tag_mapping(
            FolderTagUpsertRequest(folder_name="路由夹", mode="tag"), db=db
        )
    assert created.folder_name == "路由夹"
    assert created.mode == "tag"
    assert created.tag_name == "路由夹"
    assert created.needs_confirm is False

    async with async_session() as db:
        db.add(Tag(name="冲突标签", category="style", source="manual"))
        await db.commit()
    with pytest.raises(HTTPException) as conflict:
        async with async_session() as db:
            await router_module.upsert_folder_tag_mapping(
                FolderTagUpsertRequest(
                    folder_name="路由冲突夹", mode="tag", new_tag_name="冲突标签"
                ),
                db=db,
            )
    assert conflict.value.status_code == 409


async def test_upsert_router_skip_mode_and_delete_flow():
    async with async_session() as db:
        skipped = await router_module.upsert_folder_tag_mapping(
            FolderTagUpsertRequest(folder_name="路由跳过", mode="skip"), db=db
        )
    assert skipped.mode == "skip"

    async with async_session() as db:
        deleted = await router_module.delete_folder_tag_mapping(
            "路由跳过", db=db
        )
    assert deleted == {"folder_name": "路由跳过", "deleted": True}

    with pytest.raises(HTTPException) as missing:
        async with async_session() as db:
            await router_module.delete_folder_tag_mapping("路由跳过", db=db)
    assert missing.value.status_code == 404
    assert missing.value.detail == "映射不存在"


async def test_apply_all_router_empty_and_with_member(client, upload):
    async with async_session() as db:
        empty = await router_module.apply_all_folder_tags(db=db)
    assert empty.applied == 0
    assert empty.folders == []

    await _setup_mapped_folder(client, upload, "路由重打夹")
    async with async_session() as db:
        await svc.upsert_mapping(db, "路由重打夹", mode="tag")
    async with async_session() as db:
        result = await router_module.apply_all_folder_tags(db=db)
    assert result.applied == 1
    assert [row["folder_name"] for row in result.folders] == ["路由重打夹"]


async def test_apply_single_router_without_local_folder():
    async with async_session() as db:
        await svc.upsert_mapping(db, "仅映射路由夹", mode="tag")
    async with async_session() as db:
        result = await router_module.apply_folder_tags("仅映射路由夹", db=db)
    assert result.applied == 0
    assert result.folders == [
        {"folder_name": "仅映射路由夹", "applied": 0, "skipped": 0}
    ]


async def test_tag_impact_router_404_and_fields(client, upload):
    with pytest.raises(HTTPException) as missing:
        async with async_session() as db:
            await router_module.folder_tag_impact(999999, db=db)
    assert missing.value.status_code == 404

    insp_id = _upload(client, upload)
    async with async_session() as db:
        tag = Tag(name="路由影响标签", category="outfit", source="manual")
        db.add(tag)
        await db.commit()
        await db.refresh(tag)
        tag_id = tag.id
    from app.services.inspiration_tags import batch_add_tags

    async with async_session() as db:
        await batch_add_tags(
            db, [insp_id], ["路由影响标签"], category="outfit", source="manual"
        )
        await svc.upsert_mapping(db, "路由影响夹", mode="tag", tag_id=tag_id)
    async with async_session() as db:
        impact = await router_module.folder_tag_impact(tag_id, db=db)
    assert impact.tag_id == tag_id
    assert impact.name == "路由影响标签"
    assert impact.category == "outfit"
    assert impact.usage_count == 1
    assert impact.mapped_folders == ["路由影响夹"]
