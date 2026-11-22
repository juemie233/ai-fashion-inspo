"""收藏中心（`app/routers/favorites.py`）边界补测。

既有用例（test_favorite_events_and_center / test_favorite_views_cleanup）覆盖了
主流程与口径；这里补：空库行为、智能视图排序与分页边界、overview 的 recent_limit
边界、history 的 limit/同时间戳排序、cleanup 的重复组上限与排序、路径安全解析、
`_load_out_items` 辅助函数，以及收藏中心依赖的批量收藏/取消与收藏历史端点的
参数校验与「跳过垃圾桶」语义。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.inspiration import FavoriteEvent, Inspiration
from app.routers import favorites as fav
from app.utils.time import utcnow


# ── 构造助手 ──


def _upload(client, upload) -> str:
    resp = upload()
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _set_favorite(client, inspiration_id: str, value: bool = True) -> None:
    resp = client.patch(
        f"/api/inspirations/{inspiration_id}", json={"is_favorite": value}
    )
    assert resp.status_code == 200, resp.text


def _create_collection(client, name: str, query_json: dict | None = None) -> int:
    body: dict = {"name": name}
    if query_json is not None:
        body["query_json"] = query_json
    resp = client.post("/api/collections", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed(
    insp_id: str,
    *,
    is_favorite: bool = True,
    rating: int = 0,
    favorited_at=...,  # type: ignore[assignment]
    content_hash: str | None = None,
    file_path: str | None = None,
    deleted: bool = False,
) -> None:
    """直接建库造收藏素材（file_path 默认指向不存在的文件）。"""
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="manual_upload",
                file_path=file_path or f"images/{insp_id}-missing.jpg",
                media_type="image",
                quality_status="approved",
                rating=rating,
                is_favorite=is_favorite,
                content_hash=content_hash,
                favorited_at=(
                    utcnow() if favorited_at is ... else favorited_at
                ),
                deleted_at=utcnow() if deleted else None,
            )
        )
        await db.commit()


async def _set_stored_favorited_at(inspiration_id: str, value) -> None:  # noqa: ANN001
    async with async_session() as db:
        row = await db.get(Inspiration, inspiration_id)
        assert row is not None
        row.favorited_at = value
        await db.commit()


async def _events_of(inspiration_id: str) -> list[FavoriteEvent]:
    async with async_session() as db:
        return list(
            (
                await db.execute(
                    select(FavoriteEvent)
                    .where(FavoriteEvent.inspiration_id == inspiration_id)
                    .order_by(FavoriteEvent.id.asc())
                )
            )
            .scalars()
            .all()
        )


# ══════════════════════════════════════════════════════════════
#  空库行为：五个端点都要能跑，计数全 0、列表为空
# ══════════════════════════════════════════════════════════════


def test_favorites_center_on_empty_db(client):
    views = client.get("/api/favorites/views").json()["views"]
    assert [view["key"] for view in views] == [
        "unrated",
        "unfiled",
        "high_rating",
        "stale",
    ]
    assert [view["count"] for view in views] == [0, 0, 0, 0]

    for key in ("unrated", "unfiled", "high_rating", "stale"):
        data = client.get(f"/api/favorites/views/{key}").json()
        assert data == {"key": key, "items": [], "total": 0, "page": 1, "size": 50}

    cleanup = client.get("/api/favorites/cleanup").json()
    assert cleanup == {
        "counts": {
            "unrated": 0,
            "unfiled": 0,
            "missing_files": 0,
            "duplicate_groups": 0,
            "duplicate_items": 0,
        },
        "missing_files": [],
        "duplicate_groups": [],
    }

    overview = client.get("/api/favorites/overview").json()
    assert overview == {
        "total_favorites": 0,
        "favorites_this_week": 0,
        "unrated_favorites": 0,
        "unfiled_favorites": 0,
        "recent": [],
    }

    history = client.get("/api/favorites/history").json()
    assert history == {"items": [], "total": 0}


# ══════════════════════════════════════════════════════════════
#  /views/{key}：排序（收藏时间倒序、无时间最后、id 兜底）与分页边界
# ══════════════════════════════════════════════════════════════


async def test_view_items_sort_nulls_last_and_id_tiebreak(client, upload):
    recent = _upload(client, upload)
    older = _upload(client, upload)
    null_a = _upload(client, upload)
    null_b = _upload(client, upload)
    for insp_id in (recent, older, null_a, null_b):
        _set_favorite(client, insp_id)

    now = utcnow()
    await _set_stored_favorited_at(recent, now)
    await _set_stored_favorited_at(older, now - timedelta(days=3))
    await _set_stored_favorited_at(null_a, None)
    await _set_stored_favorited_at(null_b, None)

    data = client.get("/api/favorites/views/unrated", params={"size": 10}).json()
    expected_nulls = sorted([null_a, null_b], reverse=True)  # id desc 兜底
    assert [item["id"] for item in data["items"]] == [recent, older, *expected_nulls]
    assert data["total"] == 4


async def test_view_items_pagination_boundaries(client, upload):
    ids = []
    for _ in range(3):
        insp_id = _upload(client, upload)
        _set_favorite(client, insp_id)
        ids.append(insp_id)

    page2 = client.get(
        "/api/favorites/views/unrated", params={"page": 2, "size": 2}
    ).json()
    assert page2["total"] == 3
    assert page2["page"] == 2
    assert page2["size"] == 2
    assert len(page2["items"]) == 1

    page3 = client.get(
        "/api/favorites/views/unrated", params={"page": 3, "size": 2}
    ).json()
    assert page3["items"] == []
    assert page3["total"] == 3

    # size=200 是上限（合法），201 / 0 与非整数分页参数 → 422
    assert (
        client.get("/api/favorites/views/unrated", params={"size": 200}).status_code
        == 200
    )
    assert (
        client.get("/api/favorites/views/unrated", params={"size": 201}).status_code
        == 422
    )
    assert (
        client.get("/api/favorites/views/unrated", params={"page": 0}).status_code
        == 422
    )
    assert (
        client.get("/api/favorites/views/unrated", params={"size": 0}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/favorites/views/unrated", params={"page": "not-int"}
        ).status_code
        == 422
    )


def test_view_extra_conditions_unknown_key_raises_404():
    """`_view_extra_conditions` 是视图口径的唯一入口：未知 key 直接 404。"""
    with pytest.raises(HTTPException) as exc:
        fav._view_extra_conditions("bogus")
    assert exc.value.status_code == 404
    assert exc.value.detail == "未知收藏视图：bogus"


def test_view_key_unknown_http_404(client):
    for key in ("bogus", "UNRATED", "unknown"):
        resp = client.get(f"/api/favorites/views/{key}")
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════
#  /overview：recent_limit 边界 + 无收藏时间排最后
# ══════════════════════════════════════════════════════════════


async def test_overview_recent_limit_and_null_ordering(client, upload):
    latest = _upload(client, upload)
    middle = _upload(client, upload)
    old = _upload(client, upload)
    no_time = _upload(client, upload)
    for insp_id in (latest, middle, old, no_time):
        _set_favorite(client, insp_id)

    now = utcnow()
    await _set_stored_favorited_at(latest, now)
    await _set_stored_favorited_at(middle, now - timedelta(days=1))
    await _set_stored_favorited_at(old, now - timedelta(days=30))
    await _set_stored_favorited_at(no_time, None)

    two = client.get(
        "/api/favorites/overview", params={"recent_limit": 2}
    ).json()
    assert [item["id"] for item in two["recent"]] == [latest, middle]

    full = client.get("/api/favorites/overview").json()
    assert [item["id"] for item in full["recent"]] == [
        latest,
        middle,
        old,
        no_time,
    ]
    assert full["total_favorites"] == 4
    # 无收藏时间的也计入总数，但不算「本周新增」
    assert full["favorites_this_week"] == 2

    assert (
        client.get("/api/favorites/overview", params={"recent_limit": 1}).status_code
        == 200
    )
    for bad in (0, 51):
        assert (
            client.get(
                "/api/favorites/overview", params={"recent_limit": bad}
            ).status_code
            == 422
        )


# ══════════════════════════════════════════════════════════════
#  /history：limit 边界、同时间戳按 id 倒序
# ══════════════════════════════════════════════════════════════


async def test_history_limit_truncates_and_default(client, upload):
    insp_id = _upload(client, upload)
    _set_favorite(client, insp_id)
    await asyncio.sleep(0.01)
    _set_favorite(client, insp_id, False)
    await asyncio.sleep(0.01)
    _set_favorite(client, insp_id)

    limited = client.get("/api/favorites/history", params={"limit": 2}).json()
    assert limited["total"] == 2
    assert [item["action"] for item in limited["items"]] == ["favorite", "unfavorite"]

    assert client.get("/api/favorites/history").json()["total"] == 3
    assert (
        client.get("/api/favorites/history", params={"limit": 200}).status_code
        == 200
    )
    for bad in (0, 201):
        assert (
            client.get("/api/favorites/history", params={"limit": bad}).status_code
            == 422
        )


async def test_history_same_timestamp_orders_by_id_desc(client, upload):
    insp_id = _upload(client, upload)
    _set_favorite(client, insp_id)
    async with async_session() as db:
        row = await db.get(Inspiration, insp_id)
        row.is_favorite = False
        row.favorited_at = None
        stamp = datetime(2026, 1, 1, 0, 0, 0)
        for action in ("favorite", "unfavorite"):
            db.add(
                FavoriteEvent(
                    inspiration_id=insp_id,
                    action=action,
                    source="legacy",
                    created_at=stamp,
                )
            )
        await db.commit()

    data = client.get("/api/favorites/history", params={"limit": 200}).json()
    tie_events = [item for item in data["items"] if item["created_at"].startswith("2026-01-01")]
    assert len(tie_events) == 2
    # 同一 created_at → 按 id 倒序（后写入的事件在前）
    assert tie_events[0]["id"] > tie_events[1]["id"]
    assert tie_events[0]["created_at"].endswith("Z")
    assert tie_events[0]["item"]["favorited_at"] is None


# ══════════════════════════════════════════════════════════════
#  /cleanup：重复组排序 + 20 组展示上限；缺失路径安全处理
# ══════════════════════════════════════════════════════════════


async def test_cleanup_duplicate_groups_ordering_and_cap(client):
    """重复组按「组内条数降序、hash 升序」排序，最多展示 20 组，总数不受上限影响。"""
    # 21 个 2 条组（hash h00~h20）+ 1 个 3 条组（z-extra）
    for index in range(21):
        hash_ = f"h{index:02d}"
        await _seed(f"dup-{hash_}-1", content_hash=hash_)
        await _seed(f"dup-{hash_}-2", content_hash=hash_)
    for suffix in ("1", "2", "3"):
        await _seed(f"dup-extra-{suffix}", content_hash="z-extra")

    data = client.get("/api/favorites/cleanup").json()
    counts = data["counts"]
    assert counts["duplicate_groups"] == 22  # 21 + 1
    assert counts["duplicate_items"] == 45  # 21*2 + 3
    groups = data["duplicate_groups"]
    assert len(groups) == 20  # CLEANUP_GROUP_LIMIT
    hashes = [group["content_hash"] for group in groups]
    assert hashes[0] == "z-extra"  # 条数最多（3）排最前
    assert hashes[1] == "h00"  # 同为 2 条 → hash 升序
    assert hashes[-1] == "h18"  # 共取 20 组：1 个 3 条组 + 前 19 个 2 条组
    assert all(len(group["items"]) >= 2 for group in groups)
    assert all(item["is_favorite"] for group in groups for item in group["items"])


async def test_cleanup_duplicate_group_items_sorted_by_favorite_time(client, upload):
    first = _upload(client, upload)
    second = _upload(client, upload)
    _set_favorite(client, first)
    _set_favorite(client, second)
    now = utcnow()
    await _set_stored_favorited_at(first, now - timedelta(days=2))
    await _set_stored_favorited_at(second, now)
    async with async_session() as db:
        for insp_id in (first, second):
            row = await db.get(Inspiration, insp_id)
            row.content_hash = "same-hash"
        # 未收藏 / 垃圾桶里的同 hash 素材不算重复组
        db.add(
            Inspiration(
                id="dup-nonfavorite",
                source_type="manual_upload",
                file_path="images/dup-nonfavorite.jpg",
                media_type="image",
                quality_status="approved",
                content_hash="same-hash",
                is_favorite=False,
            )
        )
        db.add(
            Inspiration(
                id="dup-trashed",
                source_type="manual_upload",
                file_path="images/dup-trashed.jpg",
                media_type="image",
                quality_status="approved",
                content_hash="same-hash",
                is_favorite=True,
                deleted_at=utcnow(),
            )
        )
        await db.commit()

    data = client.get("/api/favorites/cleanup").json()
    assert data["counts"]["duplicate_groups"] == 1
    assert data["counts"]["duplicate_items"] == 2
    (group,) = data["duplicate_groups"]
    assert group["content_hash"] == "same-hash"
    assert [item["id"] for item in group["items"]] == [second, first]


async def test_cleanup_missing_file_edge_paths(client, upload):
    good = _upload(client, upload)
    _set_favorite(client, good)
    # 补一个真实存在的收藏文件 → 不算缺失
    async with async_session() as db:
        row = await db.get(Inspiration, good)
        assert row is not None
        row.content_hash = "good-hash"
        await db.commit()

    await _seed("miss-empty", file_path="")
    await _seed("miss-null-bytes", file_path="images/\x00bad.jpg")
    await _seed("miss-escape", file_path="../../outside.jpg")
    await _seed("miss-abs", file_path="/etc/passwd")
    await _seed("miss-trashed", file_path="images/miss-trashed.jpg", deleted=True)
    await _seed(
        "miss-not-favorite", is_favorite=False, file_path="images/not-favorite.jpg"
    )

    data = client.get("/api/favorites/cleanup").json()
    missing = {item["id"] for item in data["missing_files"]}
    assert {"miss-empty", "miss-null-bytes", "miss-escape", "miss-abs"} <= missing
    assert "miss-trashed" not in missing  # 垃圾桶不参与
    assert "miss-not-favorite" not in missing  # 未收藏不参与
    assert good not in missing  # 文件真实存在
    assert data["counts"]["missing_files"] == len(missing)


async def _stored_file_path(inspiration_id: str) -> str:
    async with async_session() as db:
        return (
            await db.execute(
                select(Inspiration.file_path).where(Inspiration.id == inspiration_id)
            )
        ).scalar_one()


# ══════════════════════════════════════════════════════════════
#  路径安全解析 / 素材加载辅助函数
# ══════════════════════════════════════════════════════════════


async def test_safe_storage_path_and_file_exists(client, upload):
    good = _upload(client, upload)
    rel = await _stored_file_path(good)

    assert fav._safe_storage_path(None) is None
    assert fav._safe_storage_path("") is None
    for bad in ("../outside.jpg", "../../outside.jpg", "/etc/passwd", "images/\x00bad.jpg"):
        assert fav._safe_storage_path(bad) is None
    resolved = fav._safe_storage_path(rel)
    assert resolved is not None
    assert resolved.is_relative_to(settings.storage_root.resolve())

    assert fav._storage_file_exists(rel) is True
    assert fav._storage_file_exists(None) is False
    assert fav._storage_file_exists("images/definitely-missing.jpg") is False


async def test_load_out_items_filters_unknown_and_preserves_order(client, upload):
    first = _upload(client, upload)
    second = _upload(client, upload)

    async with async_session() as db:
        assert await fav._load_out_items(db, []) == []
        out = await fav._load_out_items(db, [second, "unknown-id", first])

    assert [item.id for item in out] == [second, first]
    assert all(item.is_favorite is False for item in out)


# ══════════════════════════════════════════════════════════════
#  批量收藏 / 取消（收藏中心依赖的写路径）
# ══════════════════════════════════════════════════════════════


async def test_batch_favorite_skips_unknown_and_trashed(client, upload):
    valid = _upload(client, upload)
    trashed = _upload(client, upload)
    client.post(f"/api/inspirations/{trashed}/trash")

    resp = client.post(
        "/api/inspirations/batch-favorite",
        json={"ids": [valid, trashed, "no-such-id"], "is_favorite": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 1}

    assert [event.action for event in await _events_of(valid)] == ["favorite"]
    # 垃圾桶素材：不写事件、状态不变
    async with async_session() as db:
        row = await db.get(Inspiration, trashed)
        assert row.is_favorite is False
    assert await _events_of(trashed) == []


async def test_batch_favorite_dedupes_ids(client, upload):
    insp_id = _upload(client, upload)
    resp = client.post(
        "/api/inspirations/batch-favorite",
        json={"ids": [insp_id, insp_id, insp_id], "is_favorite": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"updated": 1}
    assert len(await _events_of(insp_id)) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"ids": [], "is_favorite": True},  # 空列表（min_length=1）
        {"ids": ["x"]},  # 缺少 is_favorite
        {"ids": ["x"], "is_favorite": [1, 2]},  # 非布尔（Pydantic 不接受列表）
        {"ids": [f"id-{index}" for index in range(501)], "is_favorite": True},  # 超上限
    ],
)
def test_batch_favorite_rejects_invalid_payload(client, payload):
    resp = client.post("/api/inspirations/batch-favorite", json=payload)
    assert resp.status_code == 422


async def test_batch_unfavorite_trashed_keeps_state(client, upload):
    insp_id = _upload(client, upload)
    _set_favorite(client, insp_id)
    client.post(f"/api/inspirations/{insp_id}/trash")

    resp = client.post(
        "/api/inspirations/batch-favorite",
        json={"ids": [insp_id], "is_favorite": False},
    )
    assert resp.json() == {"updated": 0}
    async with async_session() as db:
        assert (await db.get(Inspiration, insp_id)).is_favorite is True


async def test_batch_update_reports_favorite_changed(client, upload):
    already = _upload(client, upload)
    changed = _upload(client, upload)
    _set_favorite(client, already)

    resp = client.post(
        "/api/inspirations/batch-update",
        json={"ids": [already, changed], "is_favorite": True, "rating": 5},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 2  # 带其它字段时 updated 是命中行数
    assert body["favorite_changed"] == 1  # 只有 changed 真实翻转
    assert [event.action for event in await _events_of(already)] == ["favorite"]
    assert [event.action for event in await _events_of(changed)] == ["favorite"]

    only_rating = client.post(
        "/api/inspirations/batch-update",
        json={"ids": [already, changed], "rating": 4},
    ).json()
    assert only_rating["favorite_changed"] == 0
    assert only_rating["updated"] == 2


# ══════════════════════════════════════════════════════════════
#  端点函数直调：一次走完五个只读聚合端点的完整函数体
#  （TestClient portal 线程会漏记 async 函数体行，直调可验证查询/序列化全路径）
# ══════════════════════════════════════════════════════════════


async def test_endpoint_functions_direct_with_seeded_data(client, upload):
    rated = _upload(client, upload)
    unfiled = _upload(client, upload)
    duplicate = _upload(client, upload)
    _set_favorite(client, rated)
    _set_favorite(client, unfiled)
    _set_favorite(client, duplicate)
    client.patch(f"/api/inspirations/{rated}", json={"rating": 5}).raise_for_status()

    # 手动合集收一条 → rated 从「未整理」里排除；重复组造两条同 hash 收藏
    manual = _create_collection(client, "直调手动夹")
    client.post(
        f"/api/collections/{manual}/inspirations",
        json={"inspiration_ids": [rated]},
    )
    async with async_session() as db:
        for insp_id in (rated, duplicate):
            row = await db.get(Inspiration, insp_id)
            row.content_hash = "direct-dup-hash"
        await db.commit()

    async with async_session() as db:
        views = await fav.favorites_views(db)
        item_page = await fav.favorites_view_items("high_rating", 1, 50, db)
        cleanup = await fav.favorites_cleanup(db)
        overview = await fav.favorites_overview(12, db)
        history = await fav.favorites_history(30, db)

    counts = {view["key"]: view["count"] for view in views["views"]}
    assert counts == {
        "unrated": 2,  # unfiled + duplicate
        "unfiled": 2,  # rated 已在手动合集
        "high_rating": 1,
        "stale": 0,
    }
    assert [item.id for item in item_page["items"]] == [rated]
    assert item_page["total"] == 1

    assert cleanup["counts"]["duplicate_groups"] == 1
    assert cleanup["counts"]["duplicate_items"] == 2
    assert cleanup["duplicate_groups"][0]["content_hash"] == "direct-dup-hash"

    assert overview["total_favorites"] == 3
    assert overview["unrated_favorites"] == 2
    assert overview["unfiled_favorites"] == 2
    assert len(overview["recent"]) == 3

    assert history["total"] == 3  # 三次收藏事件
    assert all(item["action"] == "favorite" for item in history["items"])
    assert history["items"][0]["created_at"].endswith("Z")
    assert history["items"][0]["item"]["is_favorite"] is True


async def test_single_favorite_history_limit_bounds(client, upload):
    insp_id = _upload(client, upload)
    _set_favorite(client, insp_id)
    _set_favorite(client, insp_id, False)
    _set_favorite(client, insp_id)

    limited = client.get(
        f"/api/inspirations/{insp_id}/favorite-history", params={"limit": 2}
    ).json()
    assert limited["total"] == 2
    assert client.get(
        f"/api/inspirations/{insp_id}/favorite-history", params={"limit": 200}
    ).status_code == 200
    for bad in (0, 201):
        assert (
            client.get(
                f"/api/inspirations/{insp_id}/favorite-history",
                params={"limit": bad},
            ).status_code
            == 422
        )
