"""收藏增强 P0：收藏时间 / 收藏事件流水 / 收藏中心数据接口测试。

覆盖（2026-11-19）：
- 单条/批量收藏只在该状态真实变化时写 ``favorited_at`` 与 ``favorite_events``（幂等）；
- 事件来源：单条 PATCH=web、批量= batch、迁移回填= legacy；
- ``InspirationOut`` 带出 ``favorited_at``（UTC 后缀）；
- 列表 ``sort=favorite_time``（最近收藏倒序、NULL 最后）；
- ``GET /api/inspirations/{id}/favorite-history``（未知/无历史返回空）；
- ``GET /api/favorites/overview`` 统计与未整理口径（智能合集不算整理）；
- ``GET /api/favorites/history`` 最近动态（排除垃圾桶）。
"""

import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.database import async_session
from app.models.collection import CollectionItem
from app.models.inspiration import FavoriteEvent, Inspiration
from app.utils.time import utcnow


def _upload(client, upload) -> str:
    resp = upload()
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _set_favorite(client, inspiration_id: str, value: bool) -> dict:
    resp = client.patch(
        f"/api/inspirations/{inspiration_id}", json={"is_favorite": value}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _events(client, inspiration_id: str) -> list[dict]:
    resp = client.get(f"/api/inspirations/{inspiration_id}/favorite-history")
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


async def _fetch(inspiration_id: str):
    async with async_session() as db:
        return await db.get(Inspiration, inspiration_id)


def _create_collection(client, name: str, query_json: dict | None = None) -> int:
    body: dict = {"name": name}
    if query_json is not None:
        body["query_json"] = query_json
    resp = client.post("/api/collections", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ── 收藏时间 + 事件（单条 / 批量） ──


async def test_single_favorite_tracks_time_and_event_idempotent(client, upload):
    insp_id = _upload(client, upload)

    # 初始：未收藏、无收藏时间、无事件
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["is_favorite"] is False
    assert detail["favorited_at"] is None
    assert _events(client, insp_id) == []

    first = _set_favorite(client, insp_id, True)
    assert first["is_favorite"] is True
    assert first["favorited_at"] is not None
    assert first["favorited_at"].endswith("Z")
    favorited_at = first["favorited_at"]
    events = _events(client, insp_id)
    assert len(events) == 1
    assert events[0]["action"] == "favorite"
    assert events[0]["source"] == "web"
    assert events[0]["created_at"].endswith("Z")

    # 重复收藏：幂等（不写事件、不改收藏时间）
    again = _set_favorite(client, insp_id, True)
    assert again["favorited_at"] == favorited_at
    assert len(_events(client, insp_id)) == 1

    # 取消收藏：清空收藏时间、写 unfavorite 事件
    removed = _set_favorite(client, insp_id, False)
    assert removed["favorited_at"] is None
    events = _events(client, insp_id)
    assert [e["action"] for e in events] == ["unfavorite", "favorite"]
    assert events[0]["source"] == "web"

    # 重复取消：幂等
    _set_favorite(client, insp_id, False)
    assert [e["action"] for e in _events(client, insp_id)] == [
        "unfavorite",
        "favorite",
    ]


async def test_batch_favorite_only_real_changes(client, upload):
    a = _upload(client, upload)
    b = _upload(client, upload)

    first = client.post(
        "/api/inspirations/batch-favorite",
        json={"ids": [a, b], "is_favorite": True},
    )
    assert first.status_code == 200, first.text
    assert first.json()["updated"] == 2
    for insp_id in (a, b):
        events = _events(client, insp_id)
        assert len(events) == 1
        assert events[0]["source"] == "batch"
        assert events[0]["action"] == "favorite"

    # 重复批量收藏：没有真实变化 → updated=0、不新增事件
    second = client.post(
        "/api/inspirations/batch-favorite",
        json={"ids": [a, b], "is_favorite": True},
    ).json()
    assert second["updated"] == 0
    assert len(_events(client, a)) == 1

    # 批量取消其中一个：只有它变化
    third = client.post(
        "/api/inspirations/batch-favorite",
        json={"ids": [a, b], "is_favorite": False},
    ).json()
    assert third["updated"] == 2
    assert [e["action"] for e in _events(client, a)] == ["unfavorite", "favorite"]
    row = await _fetch(a)
    assert row is not None and row.favorited_at is None


async def test_batch_update_favorite_writes_batch_event(client, upload):
    insp_id = _upload(client, upload)

    resp = client.post(
        "/api/inspirations/batch-update",
        json={"ids": [insp_id], "is_favorite": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1
    events = _events(client, insp_id)
    assert len(events) == 1
    assert events[0]["source"] == "batch"

    # 幂等：再次批量编辑同一状态 → updated=0、无新事件
    again = client.post(
        "/api/inspirations/batch-update",
        json={"ids": [insp_id], "is_favorite": True},
    ).json()
    assert again["updated"] == 0
    assert len(_events(client, insp_id)) == 1

    # 同时改其它字段时：收藏真实变化仍写 batch 事件
    removed = client.post(
        "/api/inspirations/batch-update",
        json={"ids": [insp_id], "is_favorite": False, "source_type": "manual_upload"},
    ).json()
    assert removed["updated"] == 1
    assert [e["action"] for e in _events(client, insp_id)] == [
        "unfavorite",
        "favorite",
    ]


async def test_trashed_cannot_be_favorited(client, upload):
    insp_id = _upload(client, upload)
    assert client.post(f"/api/inspirations/{insp_id}/trash").status_code == 200
    resp = client.patch(
        f"/api/inspirations/{insp_id}", json={"is_favorite": True}
    )
    assert resp.status_code == 400


# ── InspirationOut / 列表排序 ──


async def test_list_and_detail_carry_favorited_at(client, upload):
    insp_id = _upload(client, upload)
    favorited = _set_favorite(client, insp_id, True)

    listed = client.get("/api/inspirations", params={"size": 50}).json()
    item = next(i for i in listed["items"] if i["id"] == insp_id)
    assert item["favorited_at"] == favorited["favorited_at"]
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["favorited_at"] == favorited["favorited_at"]


async def test_list_sort_favorite_time(client, upload):
    first = _upload(client, upload)
    second = _upload(client, upload)
    never = _upload(client, upload)

    _set_favorite(client, first, True)
    await asyncio.sleep(0.02)
    _set_favorite(client, second, True)

    data = client.get(
        "/api/inspirations", params={"sort": "favorite_time", "size": 50}
    ).json()
    ids = [item["id"] for item in data["items"]]
    assert ids.index(second) < ids.index(first)  # 最近收藏在前
    assert ids.index(first) < ids.index(never)  # 未收藏（NULL）最后


# ── 收藏中心：overview / history ──


async def test_favorites_overview(client, upload):
    old = _upload(client, upload)  # 收藏时间改到 8 天前（不算本周）
    unrated = _upload(client, upload)
    rated = _upload(client, upload)
    unfiled = _upload(client, upload)
    deleted = _upload(client, upload)

    for insp_id in (old, unrated, rated, unfiled, deleted):
        _set_favorite(client, insp_id, True)
    client.patch(f"/api/inspirations/{rated}", json={"rating": 5}).raise_for_status()

    # old 的收藏时间改为 8 天前
    async with async_session() as db:
        row = await db.get(Inspiration, old)
        assert row is not None
        row.favorited_at = utcnow().replace(microsecond=0) - timedelta(days=8)
        await db.commit()

    # 手动合集收一个；智能合集塞一条成员行（用于验证「智能合集不算整理」）
    manual_id = _create_collection(client, "手动夹")
    client.post(
        f"/api/collections/{manual_id}/inspirations",
        json={"inspiration_ids": [unrated]},
    )
    smart_id = _create_collection(client, "智能夹", {"is_favorite": True})
    async with async_session() as db:
        db.add(CollectionItem(collection_id=smart_id, inspiration_id=rated, position=0))
        await db.commit()

    # 垃圾桶素材不参与统计
    client.post(f"/api/inspirations/{deleted}/trash")

    data = client.get(
        "/api/favorites/overview", params={"recent_limit": 3}
    ).json()
    assert data["total_favorites"] == 4  # 5 - 垃圾桶 1
    assert data["favorites_this_week"] == 3  # 不含 8 天前的 old
    # 未评分：old / unrated / unfiled（rated 已评 5）
    assert data["unrated_favorites"] == 3
    # 未整理：4 条收藏里只有 unrated 进了手动合集 → 3 条未整理；
    # 智能合集里的 rated 不算整理（若误算，这里会是 2）
    assert data["unfiled_favorites"] == 3
    assert len(data["recent"]) == 3
    recent_ids = [item["id"] for item in data["recent"]]
    assert deleted not in recent_ids
    # 最近收藏按收藏时间倒序：old 最老 → 不在 top3
    assert old not in recent_ids
    assert all("favorited_at" in item for item in data["recent"])


async def test_favorites_history(client, upload):
    a = _upload(client, upload)
    b = _upload(client, upload)
    _set_favorite(client, a, True)
    await asyncio.sleep(0.01)
    _set_favorite(client, b, True)
    await asyncio.sleep(0.01)
    _set_favorite(client, a, False)

    data = client.get("/api/favorites/history", params={"limit": 10}).json()
    actions = [(item["inspiration_id"], item["action"]) for item in data["items"]]
    assert actions[0] == (a, "unfavorite")
    assert actions[1] == (b, "favorite")
    assert actions[2] == (a, "favorite")
    first = data["items"][0]
    assert first["source"] == "web"
    assert first["created_at"].endswith("Z")
    item = first["item"]
    assert set(item) == {
        "id",
        "file_path",
        "thumbnail_path",
        "media_type",
        "source_author",
        "is_favorite",
        "favorited_at",
    }
    assert item["id"] == a and item["is_favorite"] is False

    # 垃圾桶素材的事件不返回
    client.post(f"/api/inspirations/{b}/trash")
    after = client.get("/api/favorites/history", params={"limit": 10}).json()
    assert all(item["inspiration_id"] != b for item in after["items"])


def test_favorite_history_unknown_or_empty(client, upload):
    unknown = client.get("/api/inspirations/no-such-id/favorite-history")
    assert unknown.status_code == 200
    assert unknown.json() == {"items": [], "total": 0}

    insp_id = _upload(client, upload)
    never = client.get(f"/api/inspirations/{insp_id}/favorite-history").json()
    assert never == {"items": [], "total": 0}


async def test_favorite_events_table_records_source(client, upload):
    """直接查事件表：单条 web / 批量 batch 的来源写对。"""
    a = _upload(client, upload)
    b = _upload(client, upload)
    _set_favorite(client, a, True)
    client.post(
        "/api/inspirations/batch-favorite",
        json={"ids": [b], "is_favorite": True},
    )
    async with async_session() as db:
        rows = (
            await db.execute(
                select(FavoriteEvent).order_by(FavoriteEvent.id.asc())
            )
        ).scalars().all()
    assert [(r.inspiration_id, r.action, r.source) for r in rows] == [
        (a, "favorite", "web"),
        (b, "favorite", "batch"),
    ]
