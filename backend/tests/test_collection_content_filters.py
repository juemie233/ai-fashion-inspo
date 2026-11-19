"""合集内容搜索/筛选/排序测试（2026-11-19 收藏增强 P0-C）。

覆盖：
- 手动合集：默认 position 顺序；q（作者/正文/标签名）、media_type、quality_status、
  rating_min、is_favorite 筛选在分页前生效；newest/oldest/rating/rating_asc 排序；
- 智能合集：query_json 求值 + 附加筛选，默认 newest；显式 ``sort=manual`` 返回 400；
- 垃圾桶素材不参与；参数校验 422 / 合集不存在 404。
"""

from datetime import datetime

from sqlalchemy import select

from app.database import async_session
from app.models.collection import CollectionItem
from app.models.inspiration import Inspiration, NOT_DELETED
from app.models.tag import InspirationTag, Tag


def _create_collection(client, name: str, query_json: dict | None = None) -> int:
    body: dict = {"name": name}
    if query_json is not None:
        body["query_json"] = query_json
    resp = client.post("/api/collections", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _add_material(
    insp_id: str,
    *,
    media_type: str = "image",
    quality_status: str = "approved",
    rating: int = 0,
    source_author: str | None = None,
    caption: str | None = None,
    is_favorite: bool = False,
    deleted: bool = False,
    created_at: datetime | None = None,
) -> None:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="xiaohongshu",
                file_path=f"images/{insp_id}.jpg",
                thumbnail_path=f"thumbnails/{insp_id}.jpg",
                media_type=media_type,
                quality_status=quality_status,
                rating=rating,
                source_author=source_author,
                caption=caption,
                is_favorite=is_favorite,
                created_at=created_at or datetime(2026, 1, 1, 0, 0, 0),
                deleted_at=datetime(2026, 1, 10, 0, 0, 0) if deleted else None,
            )
        )
        await db.commit()


async def _add_members(collection_id: int, inspiration_ids: list[str]) -> None:
    async with async_session() as db:
        for position, insp_id in enumerate(inspiration_ids):
            db.add(
                CollectionItem(
                    collection_id=collection_id,
                    inspiration_id=insp_id,
                    position=position,
                )
            )
        await db.commit()


async def _attach_tag(insp_id: str, tag_name: str) -> None:
    async with async_session() as db:
        tag = Tag(name=tag_name, category="free")
        db.add(tag)
        await db.flush()
        db.add(
            InspirationTag(
                inspiration_id=insp_id, tag_id=tag.id, source="manual"
            )
        )
        await db.commit()


def _content(client, collection_id: int, **params) -> dict:
    resp = client.get(
        f"/api/collections/{collection_id}/inspirations", params=params
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ids(payload: dict) -> list[str]:
    return [item["id"] for item in payload["items"]]


async def test_manual_collection_filters(client):
    cid = _create_collection(client, "手动筛选")
    await _add_material(
        "m1", rating=5, source_author="作者A", caption="长裙搭配", is_favorite=True
    )
    await _add_material(
        "m2",
        media_type="video",
        quality_status="pending",
        source_author="作者B",
        caption="通勤记录",
    )
    await _add_material(
        "m3", quality_status="rejected", rating=3, source_author="作者A"
    )
    await _add_material("m4", deleted=True)  # 垃圾桶素材不参与
    await _add_material(
        "m5", quality_status="pending", rating=4, source_author="作者D"
    )
    await _attach_tag("m5", "法式")
    await _add_members(cid, ["m1", "m2", "m3", "m4", "m5"])

    base = _content(client, cid)
    assert base["total"] == 4
    assert _ids(base) == ["m1", "m2", "m3", "m5"]  # 默认 position 顺序

    assert set(_ids(_content(client, cid, q="作者A"))) == {"m1", "m3"}
    assert _ids(_content(client, cid, q="长裙搭")) == ["m1"]  # 正文
    assert _ids(_content(client, cid, q="法式")) == ["m5"]  # 标签名
    assert _ids(_content(client, cid, media_type="image")) == ["m1", "m3", "m5"]
    assert _ids(_content(client, cid, quality_status="approved")) == ["m1"]
    assert _ids(_content(client, cid, rating_min=4)) == ["m1", "m5"]
    assert _ids(_content(client, cid, is_favorite="true")) == ["m1"]

    # 组合筛选 + 分页在筛选后
    combined = _content(
        client, cid, q="作者A", media_type="image", quality_status="approved"
    )
    assert _ids(combined) == ["m1"]
    page2 = _content(client, cid, page=2, page_size=1)
    assert _ids(page2) == ["m2"]
    assert page2["total"] == 4


async def test_manual_collection_sorts(client):
    cid = _create_collection(client, "手动排序")
    await _add_material(
        "s1", rating=3, created_at=datetime(2026, 1, 1, 0, 0, 0)
    )
    await _add_material(
        "s2", rating=5, created_at=datetime(2026, 1, 3, 0, 0, 0)
    )
    await _add_material(
        "s3", rating=0, created_at=datetime(2026, 1, 2, 0, 0, 0)
    )
    await _add_members(cid, ["s1", "s2", "s3"])

    assert _ids(_content(client, cid)) == ["s1", "s2", "s3"]  # position
    assert _ids(_content(client, cid, sort="newest")) == ["s2", "s3", "s1"]
    assert _ids(_content(client, cid, sort="oldest")) == ["s1", "s3", "s2"]
    assert _ids(_content(client, cid, sort="rating")) == ["s2", "s1", "s3"]
    # rating_asc 只排已评分（与素材库同一口径）
    asc = _content(client, cid, sort="rating_asc")
    assert _ids(asc) == ["s1", "s2"]
    assert asc["total"] == 2


async def test_smart_collection_filters_and_default_sort(client):
    cid = _create_collection(client, "智能夹", {"is_favorite": True})
    await _add_material(
        "f1",
        is_favorite=True,
        rating=5,
        created_at=datetime(2026, 2, 1, 0, 0, 0),
    )
    await _add_material(
        "f2",
        is_favorite=True,
        rating=0,
        created_at=datetime(2026, 2, 3, 0, 0, 0),
    )
    await _add_material("f3", is_favorite=False)  # 不满足 query_json

    default = _content(client, cid)
    assert default["total"] == 2
    assert _ids(default) == ["f2", "f1"]  # 智能合集默认 newest

    filtered = _content(client, cid, rating_min=4)
    assert _ids(filtered) == ["f1"]  # query_json + 附加筛选

    assert _ids(_content(client, cid, sort="oldest")) == ["f1", "f2"]

    # 智能合集显式 manual → 400
    resp = client.get(
        f"/api/collections/{cid}/inspirations", params={"sort": "manual"}
    )
    assert resp.status_code == 400
    assert "manual" in resp.json()["detail"]


async def test_collection_content_validation_and_404(client):
    cid = _create_collection(client, "校验夹")
    for params in (
        {"sort": "bogus"},
        {"media_type": "document"},
        {"quality_status": "bad"},
        {"rating_min": 0},
        {"is_favorite": "maybe"},
    ):
        resp = client.get(
            f"/api/collections/{cid}/inspirations", params=params
        )
        assert resp.status_code == 422, (params, resp.text)

    assert (
        client.get("/api/collections/999999/inspirations").status_code == 404
    )


async def test_manual_collection_deleted_member_excluded_from_filters(client):
    """垃圾桶成员即使命中筛选条件也不返回（与不合筛选一致：只查未删除）。"""
    cid = _create_collection(client, "含垃圾桶")
    await _add_material("d1", source_author="作者D")
    await _add_material("d2", source_author="作者D", deleted=True)
    await _add_members(cid, ["d1", "d2"])

    data = _content(client, cid, q="作者D")
    assert data["total"] == 1
    assert _ids(data) == ["d1"]
    async with async_session() as db:
        alive = (
            await db.execute(
                select(Inspiration.id).where(NOT_DELETED, Inspiration.id == "d2")
            )
        ).scalar_one_or_none()
    assert alive is None
