"""收藏智能视图 + 整理工作台测试（2026-11-19 收藏增强 P1）。

覆盖：
- `GET /api/favorites/views` 四口径计数（未评分/未整理/高分/久未回顾）；
- `GET /api/favorites/views/{key}` 列表、排序、分页、未知 key 404；
- `GET /api/favorites/cleanup` 计数、缺失文件（含越界/绝对路径安全处理）、
  content_hash 重复组、垃圾桶排除、缺失列表 20 条上限；
- 所有统计只含未删除收藏。
"""

from datetime import timedelta

from sqlalchemy import select

from app.database import async_session
from app.models.collection import CollectionItem
from app.models.inspiration import Inspiration
from app.utils.time import utcnow


def _upload(client, upload) -> str:
    resp = upload()
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _set_favorite(client, inspiration_id: str, value: bool = True) -> None:
    resp = client.patch(
        f"/api/inspirations/{inspiration_id}", json={"is_favorite": value}
    )
    assert resp.status_code == 200, resp.text


def _set_rating(client, inspiration_id: str, rating: int) -> None:
    resp = client.patch(
        f"/api/inspirations/{inspiration_id}", json={"rating": rating}
    )
    assert resp.status_code == 200, resp.text


def _create_collection(client, name: str) -> int:
    resp = client.post("/api/collections", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _add_manual_member(collection_id: int, inspiration_id: str) -> None:
    async with async_session() as db:
        db.add(
            CollectionItem(
                collection_id=collection_id,
                inspiration_id=inspiration_id,
                position=0,
            )
        )
        await db.commit()


async def _set_favorited_at(inspiration_id: str, value) -> None:
    async with async_session() as db:
        row = await db.get(Inspiration, inspiration_id)
        assert row is not None
        row.favorited_at = value
        await db.commit()


async def _fetch_file_path(inspiration_id: str) -> str:
    async with async_session() as db:
        return (
            await db.execute(
                select(Inspiration.file_path).where(Inspiration.id == inspiration_id)
            )
        ).scalar_one()


async def _seed(
    insp_id: str,
    *,
    is_favorite: bool = True,
    rating: int = 0,
    favorited_at=...,
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


async def test_favorites_views_counts_and_lists(client, upload):
    unrated = _upload(client, upload)
    high = _upload(client, upload)
    stale = _upload(client, upload)
    null_time = _upload(client, upload)
    not_favorite = _upload(client, upload)
    trashed = _upload(client, upload)

    for insp_id in (unrated, high, stale, null_time, trashed):
        _set_favorite(client, insp_id)
    _set_rating(client, high, 5)
    _set_rating(client, not_favorite, 5)  # 未收藏，不应出现在任何视图
    await _set_favorited_at(stale, utcnow() - timedelta(days=100))
    await _set_favorited_at(null_time, None)

    # high 放进手动合集 → 不算「未整理」
    manual = _create_collection(client, "手动夹")
    await _add_manual_member(manual, high)
    client.post(f"/api/inspirations/{trashed}/trash")

    views = client.get("/api/favorites/views").json()["views"]
    counts = {view["key"]: view["count"] for view in views}
    assert [view["key"] for view in views] == [
        "unrated",
        "unfiled",
        "high_rating",
        "stale",
    ]
    assert all(view["label"] and view["description"] for view in views)
    assert counts == {
        "unrated": 3,  # unrated / stale / null_time
        "unfiled": 3,  # 除 high（在手动合集）外
        "high_rating": 1,
        "stale": 2,  # stale + null_time
    }

    def _ids(key: str) -> list[str]:
        data = client.get(f"/api/favorites/views/{key}").json()
        return [item["id"] for item in data["items"]]

    assert set(_ids("unrated")) == {unrated, stale, null_time}
    assert _ids("high_rating") == [high]
    assert set(_ids("stale")) == {stale, null_time}
    assert set(_ids("unfiled")) == {unrated, stale, null_time}
    # 未收藏的 5 分素材不进 high_rating / 任何视图
    assert not_favorite not in _ids("high_rating")


async def test_favorites_view_pagination_and_unknown_key(client, upload):
    for _ in range(3):
        insp_id = _upload(client, upload)
        _set_favorite(client, insp_id)

    data = client.get(
        "/api/favorites/views/unrated", params={"page": 2, "size": 2}
    ).json()
    assert data["key"] == "unrated"
    assert data["total"] == 3
    assert data["page"] == 2
    assert data["size"] == 2
    assert len(data["items"]) == 1

    assert client.get("/api/favorites/views/bogus").status_code == 404
    assert (
        client.get(
            "/api/favorites/views/unrated", params={"size": 500}
        ).status_code
        == 422
    )


async def test_cleanup_missing_files_and_duplicates(client, upload):
    good = _upload(client, upload)
    _set_favorite(client, good)
    existing_path = await _fetch_file_path(good)

    # 缺失文件：不存在路径 / 越界路径 / 绝对路径都按缺失处理（安全解析）
    await _seed("missing-1", file_path="images/definitely-missing.jpg")
    await _seed("escape", file_path="../outside-storage.jpg")
    await _seed("absolute", file_path="/etc/passwd")

    # 重复组 A：两个收藏 + 一个未收藏（同 hash，未收藏不算）
    await _seed(
        "dup-a1",
        content_hash="dup-hash-a",
        file_path=existing_path,
    )
    await _seed(
        "dup-a2",
        content_hash="dup-hash-a",
        file_path=existing_path,
    )
    await _seed(
        "dup-a3",
        is_favorite=False,
        content_hash="dup-hash-a",
        file_path=existing_path,
    )
    # 重复组 B
    await _seed("dup-b1", content_hash="dup-hash-b", file_path=existing_path)
    await _seed("dup-b2", content_hash="dup-hash-b", file_path=existing_path)

    data = client.get("/api/favorites/cleanup").json()
    counts = data["counts"]
    assert counts["missing_files"] == 3
    assert counts["duplicate_groups"] == 2
    assert counts["duplicate_items"] == 4  # 两个组各 2 个收藏（未收藏不计）

    missing_ids = [item["id"] for item in data["missing_files"]]
    assert set(missing_ids) == {"missing-1", "escape", "absolute"}
    assert good not in missing_ids

    groups = {group["content_hash"]: group["items"] for group in data["duplicate_groups"]}
    assert set(groups) == {"dup-hash-a", "dup-hash-b"}
    assert {item["id"] for item in groups["dup-hash-a"]} == {"dup-a1", "dup-a2"}
    assert {item["id"] for item in groups["dup-hash-b"]} == {"dup-b1", "dup-b2"}
    # 组内按收藏时间倒序、id 兜底，且所有 item 都是收藏
    assert all(item["is_favorite"] for item in groups["dup-hash-a"])

    # 计数与智能视图一致（复用同一口径）
    views = {v["key"]: v["count"] for v in client.get("/api/favorites/views").json()["views"]}
    assert counts["unrated"] == views["unrated"]
    assert counts["unfiled"] == views["unfiled"]


async def test_cleanup_missing_files_capped_at_20(client):
    for index in range(21):
        await _seed(f"miss-{index}", file_path=f"images/not-exist-{index}.jpg")
    data = client.get("/api/favorites/cleanup").json()
    assert data["counts"]["missing_files"] == 21
    assert len(data["missing_files"]) == 20


async def test_cleanup_excludes_trashed_favorites(client, upload):
    kept = _upload(client, upload)
    _set_favorite(client, kept)
    removed = _upload(client, upload)
    _set_favorite(client, removed)
    client.post(f"/api/inspirations/{removed}/trash")

    data = client.get("/api/favorites/cleanup").json()
    # 只剩 kept 一条收藏，且它的文件存在 → 没有缺失、没有重复
    assert data["counts"]["unrated"] == 1
    assert data["counts"]["missing_files"] == 0
    assert data["counts"]["duplicate_groups"] == 0
    assert data["missing_files"] == []
