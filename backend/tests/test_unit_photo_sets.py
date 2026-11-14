"""模特照片组服务（`services/person/photo_sets.py`）的 CRUD/去重/清理测试。

照片组只由模特服务组合使用，原覆盖率 39.6%，缺的是「要么静默丢数据、要么误删文件」的
分支：

- 非模特类型调用必须 404（不支持照片组），而不是建出孤儿行；
- 列表的**每组照片数与封面**（缩略图优先）来自两条聚合查询，写错只会显示「0 张/无封面」；
- 上传**组内内容去重**：重复照片要删掉刚落盘的文件并 409，不能留两份占空间；
- 删除：先提交数据库再清理物理文件；单张删除可校验归属（跨组误删按 404 不泄露关系）；
- 空名称更新要 409 而不是把组名改成空串。
"""

import uuid

import pytest
from fastapi import HTTPException
from app.database import async_session
from app.models.person import Model, ModelPhoto, ModelPhotoSet
from app.services.person import photo_sets as ps_module
from app.services.person.base import PersonConflictError, PersonNotFoundError
from app.services.person.photo_sets import PhotoSetsMixin


class _Harness(PhotoSetsMixin):
    """最小组合体：代替真实 ModelService（不依赖其它 mixin）。"""

    has_photo_sets = True
    photo_set_model = ModelPhotoSet
    photo_model = ModelPhoto

    def __init__(self, persons: set[int] | None = None):
        self.persons = persons if persons is not None else set()

    async def get(self, _db, person_id: int):
        if person_id not in self.persons:
            raise PersonNotFoundError("模特未找到")
        return object()


def _svc(person_ids=()):
    return _Harness(set(person_ids))


async def _model(person_id: int) -> None:
    """照片组挂 models.id 外键（测试库开了 FK 约束），先建主体。"""
    async with async_session() as db:
        if await db.get(Model, person_id) is None:
            db.add(Model(id=person_id, name=f"模特{person_id}"))
            await db.commit()


async def _set(person_id: int, name: str = "写真") -> ModelPhotoSet:
    await _model(person_id)
    async with async_session() as db:
        row = ModelPhotoSet(model_id=person_id, name=name)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def _photo(set_id: int, *, sort_order: int = 0, hash: str | None = None) -> int:
    async with async_session() as db:
        row = ModelPhoto(
            set_id=set_id,
            file_path=f"person_photos/{uuid.uuid4().hex}.jpg",
            thumbnail_path=f"person_thumbnails/{uuid.uuid4().hex}.jpg",
            content_hash=hash,
            sort_order=sort_order,
        )
        db.add(row)
        await db.commit()
        return row.id


class TestSerialization:
    def test_dict_mapping(self):
        svc = _svc()
        set_row = ModelPhotoSet(id=3, model_id=1, name="写真", created_at=None, updated_at=None)
        assert svc._to_photo_set_dict(set_row, photo_count=2, cover_path="c.jpg") == {
            "id": 3,
            "model_id": 1,
            "name": "写真",
            "photo_count": 2,
            "cover_path": "c.jpg",
            "created_at": None,
            "updated_at": None,
        }

        photo = ModelPhoto(
            id=9,
            set_id=3,
            file_path="p.jpg",
            thumbnail_path="t.jpg",
            sort_order=1,
            created_at=None,
        )
        assert svc._to_photo_dict(photo) == {
            "id": 9,
            "set_id": 3,
            "file_path": "p.jpg",
            "thumbnail_path": "t.jpg",
            "sort_order": 1,
            "created_at": None,
        }


class TestCreateAndList:
    async def test_create_requires_photo_sets_support(self, client):
        svc = _svc({1})
        svc.has_photo_sets = False
        async with async_session() as db:
            with pytest.raises(PersonNotFoundError):
                await svc.create_photo_set(db, 1)

    async def test_create_missing_person_and_default_name(self, client):
        svc = _svc(set())
        async with async_session() as db:
            with pytest.raises(PersonNotFoundError):
                await svc.create_photo_set(db, 1)

        await _model(1)
        svc = _svc({1})
        async with async_session() as db:
            created = await svc.create_photo_set(db, 1, "   ")
        assert created["name"] == "未命名照片组"
        assert created["model_id"] == 1

        async with async_session() as db:
            trimmed = await svc.create_photo_set(db, 1, "  夏日写真  ")
        assert trimmed["name"] == "夏日写真"

    async def test_list_counts_and_cover(self, client):
        svc = _svc({1})
        first = await _set(1, "第一组")
        second = await _set(1, "第二组")
        empty = await _set(1, "空组")

        await _photo(first.id, sort_order=1)
        async with async_session() as db:
            photo = ModelPhoto(
                set_id=first.id,
                file_path="person_photos/x.jpg",
                thumbnail_path=None,  # 无缩略图 → 用原图
                sort_order=2,
            )
            db.add(photo)
            await db.commit()

        async with async_session() as db:
            items, total = await svc.list_photo_sets(db, 1, page=1, size=10)

        by_id = {item["id"]: item for item in items}
        assert total == 3
        assert by_id[first.id]["photo_count"] == 2
        assert by_id[first.id]["cover_path"]  # 第一条照片（按 sort_order）有缩略图
        assert by_id[second.id]["photo_count"] == 0
        assert by_id[second.id]["cover_path"] is None
        assert by_id[empty.id]["photo_count"] == 0
        # 新建时间倒序：最后创建的排前面
        assert items[0]["id"] == empty.id

        async with async_session() as db:
            page2, _total = await svc.list_photo_sets(db, 1, page=2, size=2)
        assert len(page2) == 1

    async def test_list_missing_person_or_unsupported(self, client):
        async with async_session() as db:
            with pytest.raises(PersonNotFoundError):
                await _svc(set()).list_photo_sets(db, 1, 1, 10)

            svc = _svc({1})
            svc.has_photo_sets = False
            with pytest.raises(PersonNotFoundError):
                await svc.list_photo_sets(db, 1, 1, 10)


class TestGetUpdateDelete:
    async def test_get_and_cover(self, client):
        set_row = await _set(1)
        photo_id = await _photo(set_row.id)
        svc = _svc({1})

        async with async_session() as db:
            loaded = await svc.get_photo_set(db, set_row.id)
            assert loaded.id == set_row.id

            with pytest.raises(PersonNotFoundError):
                await svc.get_photo_set(db, 999999)

            cover = await svc.get_photo_set_cover(db, set_row.id)
        assert cover and cover.startswith("person_thumbnails/")

        async with async_session() as db:
            await db.delete(await db.get(ModelPhoto, photo_id))
            await db.commit()
            assert await svc.get_photo_set_cover(db, set_row.id) is None

    async def test_update_name_validation(self, client):
        set_row = await _set(1)
        svc = _svc({1})

        async with async_session() as db:
            with pytest.raises(PersonNotFoundError):
                await svc.update_photo_set(db, 999999, "x")

            with pytest.raises(PersonConflictError):
                await svc.update_photo_set(db, set_row.id, "   ")

            updated = await svc.update_photo_set(db, set_row.id, " 新名字 ")
            await db.commit()  # 服务层只 flush，提交由 API 层负责
        assert updated["name"] == "新名字"

        async with async_session() as db:
            assert (await db.get(ModelPhotoSet, set_row.id)).name == "新名字"

    async def test_delete_set_cleans_files_after_commit(self, client, monkeypatch):
        set_row = await _set(1)
        await _photo(set_row.id)
        await _photo(set_row.id)
        cleaned: list[str] = []
        monkeypatch.setattr(ps_module, "delete_files_counting", lambda *paths: cleaned.extend(paths))

        svc = _svc({1})
        async with async_session() as db:
            await svc.delete_photo_set(db, set_row.id)

        assert len(cleaned) == 4  # 2 张照片 × (原图 + 缩略图)
        async with async_session() as db:
            assert await db.get(ModelPhotoSet, set_row.id) is None

        with pytest.raises(PersonNotFoundError):
            async with async_session() as db:
                await svc.delete_photo_set(db, 999999)

    async def test_delete_photo_with_ownership_check(self, client, monkeypatch):
        set_row = await _set(1)
        other_set = await _set(1, "另一组")
        photo_id = await _photo(set_row.id)
        cleaned: list = []
        monkeypatch.setattr(ps_module, "delete_files", lambda *paths: cleaned.extend(paths))

        svc = _svc({1})
        async with async_session() as db:
            with pytest.raises(PersonNotFoundError):
                await svc.delete_photo(db, photo_id, set_id=other_set.id)  # 跨组误删按 404

            await svc.delete_photo(db, photo_id, set_id=set_row.id)

        assert len(cleaned) == 2
        async with async_session() as db:
            assert await db.get(ModelPhoto, photo_id) is None

        with pytest.raises(PersonNotFoundError):
            async with async_session() as db:
                await svc.delete_photo(db, 999999)


class TestAddPhoto:
    async def _patch_save(self, monkeypatch):
        saved: dict = {}

        async def _save(file, **_kwargs):
            saved["called"] = True
            return ("person_photos/new.jpg", "person_thumbnails/new.jpg")

        monkeypatch.setattr(ps_module, "save_upload", _save)
        return saved

    async def test_upload_creates_photo_with_hash(self, client, monkeypatch):
        set_row = await _set(1)
        await self._patch_save(monkeypatch)
        monkeypatch.setattr(ps_module, "file_sha256", lambda _path: "hash-1")
        svc = _svc({1})

        async with async_session() as db:
            with pytest.raises(PersonNotFoundError):
                await svc.add_photo_to_set(db, 999999, object())  # 组不存在

            created = await svc.add_photo_to_set(db, set_row.id, object(), sort_order=5)
            await db.commit()

        assert created["file_path"] == "person_photos/new.jpg"
        assert created["sort_order"] == 5
        async with async_session() as db:
            row = await db.get(ModelPhoto, created["id"])
        assert row.content_hash == "hash-1"

    async def test_upload_duplicate_content_rejected_and_files_removed(self, client, monkeypatch):
        set_row = await _set(1)
        await _photo(set_row.id, hash="same-hash")
        await self._patch_save(monkeypatch)
        monkeypatch.setattr(ps_module, "file_sha256", lambda _path: "same-hash")
        removed: list = []
        monkeypatch.setattr(ps_module, "delete_files", lambda *paths: removed.extend(paths))
        svc = _svc({1})

        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await svc.add_photo_to_set(db, set_row.id, object())

        assert exc_info.value.status_code == 409
        assert "内容重复" in exc_info.value.detail
        assert removed == ["person_photos/new.jpg", "person_thumbnails/new.jpg"]

    async def test_upload_without_hash_skips_dedup(self, client, monkeypatch):
        set_row = await _set(1)
        await self._patch_save(monkeypatch)
        monkeypatch.setattr(ps_module, "file_sha256", lambda _path: "")
        svc = _svc({1})

        async with async_session() as db:
            created = await svc.add_photo_to_set(db, set_row.id, object())
            await db.commit()

        assert created["id"]
        async with async_session() as db:
            row = await db.get(ModelPhoto, created["id"])
        assert row.content_hash == ""


class TestListSetPhotos:
    async def test_pagination_and_missing_set(self, client):
        set_row = await _set(1)
        ids = [await _photo(set_row.id, sort_order=i) for i in range(3)]
        svc = _svc({1})

        async with async_session() as db:
            items, total = await svc.list_set_photos(db, set_row.id, page=1, size=2)
        assert total == 3
        assert len(items) == 2
        assert {item["id"] for item in items} <= set(ids)

        async with async_session() as db:
            page2, _ = await svc.list_set_photos(db, set_row.id, page=2, size=2)
        assert len(page2) == 1

        async with async_session() as db:
            with pytest.raises(PersonNotFoundError):
                await svc.list_set_photos(db, 999999, 1, 10)
