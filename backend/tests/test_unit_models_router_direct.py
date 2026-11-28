"""`app/routers/models.py` 路由函数直调补测（绕过 TestClient portal 线程的覆盖率漏记）。

覆盖：模特 create/list（搜索/平台/分页/排序/人脸过滤）/ip-stats/top/suggestions/
get/update/delete/inspirations，以及照片组 CRUD、照片上传（假 UploadFile + 临时
storage，不触网）与删除的错误分支。领域异常（PersonNotFoundError 等）由全局
exception_handler 转 HTTP，这里按「路由函数直接调用」的真实边界断言领域异常。
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.database import async_session
from app.models.face import ModelFaceEmbedding
from app.models.person import ModelPhotoSet
from app.routers import models as models_router
from app.schemas.person import (
    ModelCreate,
    ModelPhotoSetCreate,
    ModelPhotoSetUpdate,
    ModelUpdate,
)
from app.services.person.base import (
    PersonConflictError,
    PersonHasInspirationsError,
    PersonNotFoundError,
)
from app.utils.time import utcnow


def _upload(client, upload) -> str:
    resp = upload()
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _fake_upload(data: bytes, filename: str = "photo.jpg") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": "image/jpeg"}),
    )


async def _call(fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    """直调路由函数并提交（路由经 get_db 依赖时会自动 commit）。"""
    async with async_session() as db:
        result = await fn(*args, db=db, **kwargs)
        await db.commit()
        return result


async def _create_model(name: str, **overrides) -> dict:
    return await _call(models_router.create_model, ModelCreate(name=name, **overrides))

async def _list_models(**overrides):
    """直调 list_models：Query 默认值在直调时不会解析，必须显式传齐。"""
    params = dict(
        page=1,
        size=20,
        search=None,
        platform=None,
        sort="newest",
        order="desc",
        sort2=None,
        order2="desc",
        face_registered_only=False,
    )
    params.update(overrides)
    return await _call(models_router.list_models, **params)


async def _model_ip_stats(limit: int = 30):
    return await _call(models_router.model_ip_stats, limit=limit)


async def _top_models(limit: int = 20):
    return await _call(models_router.top_models, limit=limit)


async def _model_inspirations(
    model_id: int, page: int = 1, size: int = 20, sort: str = "newest"
):
    return await _call(
        models_router.model_inspirations, model_id, page=page, size=size, sort=sort
    )


async def _list_photo_sets(model_id: int, page: int = 1, size: int = 50):
    return await _call(models_router.list_photo_sets, model_id, page=page, size=size)


async def _get_photo_set(model_id: int, set_id: int, page: int = 1, size: int = 100):
    return await _call(
        models_router.get_photo_set, model_id, set_id, page=page, size=size
    )


async def _add_photo(model_id: int, set_id: int, file, sort_order: int = 0):  # noqa: ANN001
    return await _call(
        models_router.add_photo_to_set, model_id, set_id, file, sort_order=sort_order
    )


async def _register_face(model_id: int, top_k: int = 5):
    return await _call(models_router.register_face, model_id, top_k=top_k)


def _link(client, inspiration_id: str, model_id: int) -> None:
    resp = client.post(
        f"/api/inspirations/{inspiration_id}/models",
        json={"person_ids": [model_id]},
    )
    assert resp.status_code == 200, resp.text


# ══════════════════════════════════════════════════════════════
#  模特 CRUD / 列表筛选排序
# ══════════════════════════════════════════════════════════════


async def test_create_and_list_models_filters_and_pagination():
    first = await _create_model("模特甲", platform="douyin", ip_location="浙江")
    second = await _create_model("模特乙", platform="xiaohongshu")
    third = await _create_model("模特丙")
    assert first["name"] == "模特甲"
    assert first["platform"] == "douyin"
    assert first["inspiration_count"] == 0

    data = await _list_models()
    assert data["total"] == 3
    assert data["page"] == 1 and data["size"] == 20
    assert {item["name"] for item in data["items"]} == {"模特甲", "模特乙", "模特丙"}

    searched = await _list_models(search="模特乙")
    assert [item["id"] for item in searched["items"]] == [second["id"]]

    by_platform = await _list_models(platform="xiaohongshu")
    assert [item["id"] for item in by_platform["items"]] == [second["id"]]

    page2 = await _list_models(page=2, size=1)
    assert page2["total"] == 3
    assert len(page2["items"]) == 1

    by_name = await _list_models(sort="name", order="asc")
    assert [item["name"] for item in by_name["items"]] == sorted(
        ["模特甲", "模特乙", "模特丙"]
    )
    counted = await _list_models(sort="count", order="desc")
    assert counted["total"] == 3
    assert third["id"] in {item["id"] for item in counted["items"]}


async def test_list_models_face_registered_only():
    registered = await _create_model("已注册模特")
    plain = await _create_model("未注册模特")
    async with async_session() as db:
        db.add(
            ModelFaceEmbedding(
                model_id=registered["id"],
                embedding=np.zeros(512, dtype=np.float32).tobytes(),
                updated_at=utcnow(),
            )
        )
        await db.commit()

    data = await _list_models(face_registered_only=True)
    ids = [item["id"] for item in data["items"]]
    assert ids == [registered["id"]]
    assert plain["id"] not in ids
    assert data["items"][0]["face_registered"] is True


async def test_ip_stats_empty_and_grouped():
    empty = await _model_ip_stats()
    assert empty == {"total": 0, "items": []}

    await _create_model("属地甲", ip_location="浙江")
    await _create_model("属地乙", ip_location="浙江")
    await _create_model("属地丙")  # 空属地归入「未知」
    data = await _model_ip_stats()
    assert data["total"] == 3
    assert data["items"][0] == {"ip_location": "浙江", "count": 2}
    assert {"ip_location": "未知", "count": 1} in data["items"]

    limited = await _model_ip_stats(limit=1)
    assert len(limited["items"]) == 1
    assert limited["total"] == 3


async def test_top_models_orders_by_inspiration_count(client, upload):
    hot = await _create_model("热门模特")
    cold = await _create_model("冷门模特")
    for _ in range(2):
        _link(client, _upload(client, upload), hot["id"])
    _link(client, _upload(client, upload), cold["id"])

    top = await _top_models(limit=2)
    assert [item["id"] for item in top] == [hot["id"], cold["id"]]
    assert top[0]["inspiration_count"] == 2
    limited = await _top_models(limit=1)
    assert len(limited) == 1


async def test_suggest_models_by_name():
    await _create_model("小葵模特")
    await _create_model("阿紫")
    matched = await _call(models_router.suggest_models, name="小葵")
    assert [item["name"] for item in matched] == ["小葵模特"]
    assert await _call(models_router.suggest_models, name="不存在的人") == []


async def test_get_model_detail_style_profile(client, upload):
    model = await _create_model("详情模特")
    insp_id = _upload(client, upload)
    _link(client, insp_id, model["id"])

    detail = await _call(models_router.get_model, model["id"])
    assert detail["id"] == model["id"]
    assert detail["inspiration_count"] == 1
    assert set(detail["style_profile"]) == {"top_tags", "by_category", "trend"}

    with pytest.raises(PersonNotFoundError):
        await _call(models_router.get_model, 999999)


async def test_update_model_partial_and_clear_bio():
    model = await _create_model("待改模特", bio="旧简介")
    updated = await _call(
        models_router.update_model, model["id"], ModelUpdate(name="新名字", bio=None)
    )
    assert updated["name"] == "新名字"
    assert updated["bio"] is None

    with pytest.raises(PersonNotFoundError):
        await _call(models_router.update_model, 999999, ModelUpdate(name="X"))


async def test_delete_model_success_and_blocked(client, upload):
    empty = await _create_model("待删模特")
    assert await _call(models_router.delete_model, empty["id"]) is None
    with pytest.raises(PersonNotFoundError):
        await _call(models_router.get_model, empty["id"])

    linked = await _create_model("有素材模特")
    _link(client, _upload(client, upload), linked["id"])
    with pytest.raises(PersonHasInspirationsError):
        await _call(models_router.delete_model, linked["id"])

    with pytest.raises(PersonNotFoundError):
        await _call(models_router.delete_model, 999999)


async def test_model_inspirations_success_sorts_and_404(client, upload):
    model = await _create_model("素材模特")
    first = _upload(client, upload)
    second = _upload(client, upload)
    _link(client, first, model["id"])
    _link(client, second, model["id"])
    # 未关联的素材不应出现
    unrelated = _upload(client, upload)
    from datetime import timedelta

    from app.models.inspiration import Inspiration

    async with async_session() as db:
        old = await db.get(Inspiration, first)
        old.created_at = utcnow() - timedelta(days=2)
        new = await db.get(Inspiration, second)
        new.created_at = utcnow()
        await db.commit()

    newest = await _model_inspirations(model["id"])
    ids = [item["inspiration_id"] for item in newest["items"]]
    assert ids == [second, first]
    assert unrelated not in ids
    assert newest["person"] == {
        "id": model["id"],
        "name": "素材模特",
        "platform": "other",
    }
    assert newest["total"] == 2

    oldest = await _model_inspirations(model["id"], sort="oldest")
    assert [item["inspiration_id"] for item in oldest["items"]] == [first, second]

    page2 = await _model_inspirations(model["id"], page=2, size=1)
    assert page2["total"] == 2 and len(page2["items"]) == 1

    with pytest.raises(HTTPException) as exc:
        await _model_inspirations(999999)
    assert exc.value.status_code == 404
    assert exc.value.detail == "模特未找到"


# ══════════════════════════════════════════════════════════════
#  照片组 CRUD / 照片上传与删除
# ══════════════════════════════════════════════════════════════


async def test_photo_set_crud_and_ownership():
    model = await _create_model("照片组模特")
    other = await _create_model("别的模特")

    created = await _call(
        models_router.create_photo_set, model["id"], ModelPhotoSetCreate(name=None)
    )
    assert created["name"] == "未命名照片组"
    assert created["model_id"] == model["id"]
    assert created["photo_count"] == 0 and created["cover_path"] is None

    named = await _call(
        models_router.create_photo_set, model["id"], ModelPhotoSetCreate(name="写真A")
    )
    listed = await _list_photo_sets(model["id"])
    assert listed["total"] == 2
    assert listed["page"] == 1 and listed["size"] == 50
    assert {item["name"] for item in listed["items"]} == {"未命名照片组", "写真A"}

    detail = await _get_photo_set(model["id"], named["id"])
    assert detail["id"] == named["id"]
    assert detail["photos"] == [] and detail["total"] == 0

    with pytest.raises(HTTPException) as mismatch:
        await _get_photo_set(other["id"], named["id"])
    assert mismatch.value.status_code == 404
    assert mismatch.value.detail == "照片组未找到"

    renamed = await _call(
        models_router.update_photo_set,
        model["id"],
        named["id"],
        ModelPhotoSetUpdate(name="写真B"),
    )
    assert renamed["name"] == "写真B"

    with pytest.raises(HTTPException):
        await _call(
            models_router.update_photo_set,
            other["id"],
            named["id"],
            ModelPhotoSetUpdate(name="越权改名"),
        )
    with pytest.raises(PersonConflictError):
        await _call(
            models_router.update_photo_set,
            model["id"],
            named["id"],
            ModelPhotoSetUpdate(name="   "),
        )

    with pytest.raises(HTTPException):
        await _call(models_router.delete_photo_set, other["id"], named["id"])
    assert await _call(models_router.delete_photo_set, model["id"], named["id"]) is None
    after = await _list_photo_sets(model["id"])
    assert after["total"] == 1


async def test_list_photo_sets_missing_model_and_photo_flow(client, make_image):
    with pytest.raises(PersonNotFoundError):
        await _list_photo_sets(999999)

    model = await _create_model("照片模特")
    other = await _create_model("越权模特")
    photo_set = await _call(
        models_router.create_photo_set, model["id"], ModelPhotoSetCreate(name="照片组")
    )

    data, _ctype = make_image(size=(64, 64))
    photo = await _add_photo(
        model["id"], photo_set["id"], _fake_upload(data), sort_order=3
    )
    assert photo["set_id"] == photo_set["id"]
    assert photo["file_path"]
    assert photo["thumbnail_path"]
    assert photo["sort_order"] == 3

    # 组内内容去重：同一张图再传 → 409
    with pytest.raises(HTTPException) as dup:
        await _add_photo(model["id"], photo_set["id"], _fake_upload(data))
    assert dup.value.status_code == 409
    assert "内容重复" in dup.value.detail

    # 坏图 → 400（save_upload 的格式校验）
    with pytest.raises(HTTPException) as bad:
        await _add_photo(
            model["id"], photo_set["id"], _fake_upload(b"definitely-not-an-image")
        )
    assert bad.value.status_code == 400

    with pytest.raises(HTTPException) as foreign:
        await _add_photo(
            other["id"], photo_set["id"], _fake_upload(make_image()[0])
        )
    assert foreign.value.status_code == 404

    # 照片组详情带封面与分页
    detail = await _get_photo_set(model["id"], photo_set["id"])
    assert detail["total"] == 1
    assert detail["photos"][0]["id"] == photo["id"]
    assert detail["cover_path"] == photo["thumbnail_path"]

    # 删除：路由层跨模特归属 / 组归属不符 / 不存在 → 404 或领域异常
    with pytest.raises(HTTPException) as foreign_delete:
        await _call(
            models_router.delete_photo, other["id"], photo_set["id"], photo["id"]
        )
    assert foreign_delete.value.status_code == 404
    assert foreign_delete.value.detail == "照片组未找到"

    with pytest.raises(PersonNotFoundError):
        await _call(
            models_router.delete_photo, model["id"], photo_set["id"], 999999
        )
    other_set = await _call(
        models_router.create_photo_set, model["id"], ModelPhotoSetCreate(name="另一组")
    )
    with pytest.raises(PersonNotFoundError):
        await _call(
            models_router.delete_photo, model["id"], other_set["id"], photo["id"]
        )
    assert await _call(
        models_router.delete_photo, model["id"], photo_set["id"], photo["id"]
    ) == {"removed": 1}
    remaining = await _get_photo_set(model["id"], photo_set["id"])
    assert remaining["total"] == 0


async def test_face_endpoints_direct():
    model = await _create_model("人脸状态模特")
    status = await _call(models_router.face_status, model["id"])
    assert status == {"registered": False, "model_id": model["id"]}

    # 没有照片时注册 → 400（领域 HTTPException）
    with pytest.raises(HTTPException) as exc:
        await _register_face(model["id"])
    assert exc.value.status_code == 400
    assert "暂无照片" in exc.value.detail

    with pytest.raises(HTTPException) as missing:
        await _register_face(999999)
    assert missing.value.status_code == 404


async def test_photo_set_orm_rows_persisted():
    """确认照片组直调后确实落库（create_photo_set 本身不 commit，由路由依赖提交）。"""
    model = await _create_model("落库检查模特")
    created = await _call(
        models_router.create_photo_set, model["id"], ModelPhotoSetCreate(name="落库组")
    )
    async with async_session() as db:
        row = await db.get(ModelPhotoSet, created["id"])
        assert row is not None
        assert row.name == "落库组"
        assert row.model_id == model["id"]
