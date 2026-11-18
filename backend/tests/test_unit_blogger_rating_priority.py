"""人物优先评分接口测试（`GET /api/bloggers/rating-priority` + random 的 unrated 筛选）。

用户已确认口径（2026-11-18）：
- 只统计**图片**素材（media_type=image、未删除）；所有还有未评分图片的博主都纳入；
- 排序：已评占比升序 → 已评数升序 → 图片素材数升序 → id 升序；
- 已评 = rating>0；未评 = rating 为空/0；rating_average 只算已评图片；
- `random?unrated=true` 只抽未评分素材，与其它筛选（如 media_type）可组合。
"""

import pytest

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.person import Blogger, InspirationBlogger
from app.utils.time import utcnow

API = "/api/bloggers/rating-priority"

FIELDS = {
    "blogger_id",
    "name",
    "platform",
    "avatar_path",
    "image_count",
    "rated_count",
    "unrated_count",
    "rating_average",
    "coverage",
}


async def _add_blogger(
    name: str, *, platform: str = "xiaohongshu", avatar: str | None = None
) -> int:
    """插入一位博主，返回 id。"""
    async with async_session() as db:
        blogger = Blogger(name=name, platform=platform, avatar_path=avatar)
        db.add(blogger)
        await db.commit()
        await db.refresh(blogger)
        return blogger.id


async def _add_material(
    insp_id: str,
    blogger_id: int,
    *,
    media_type: str = "image",
    rating: int = 0,
    deleted: bool = False,
) -> None:
    """插入一条素材并关联到博主（deleted=True 时进垃圾桶，不参与统计）。"""
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="xiaohongshu",
                file_path=f"images/{insp_id}.jpg",
                media_type=media_type,
                quality_status="approved",
                rating=rating,
                deleted_at=utcnow() if deleted else None,
            )
        )
        db.add(InspirationBlogger(inspiration_id=insp_id, blogger_id=blogger_id))
        await db.commit()


async def _seed_blogger(
    name: str,
    image_ratings: list[int],
    *,
    videos: int = 0,
    deleted_images: int = 0,
    platform: str = "xiaohongshu",
    avatar: str | None = None,
) -> int:
    """建博主并造素材：``image_ratings`` 是每张图片的评分（0 = 未评分）。"""
    blogger_id = await _add_blogger(name, platform=platform, avatar=avatar)
    seq = 0
    for rating in image_ratings:
        seq += 1
        await _add_material(f"insp-{blogger_id}-{seq}", blogger_id, rating=rating)
    for _ in range(videos):
        seq += 1
        await _add_material(
            f"insp-{blogger_id}-{seq}", blogger_id, media_type="video"
        )
    for _ in range(deleted_images):
        seq += 1
        await _add_material(
            f"insp-{blogger_id}-{seq}", blogger_id, rating=0, deleted=True
        )
    return blogger_id


# ── rating-priority ──


async def test_order_and_fields(client):
    """排序四段键全覆盖：占比 → 已评数 → 图片数 → id；字段与口径正确。"""
    b_a = await _seed_blogger("A", [0, 0], avatar="avatars/a.jpg")  # 0/2
    b_b = await _seed_blogger(
        "B", [0, 0, 0, 0], videos=1, deleted_images=1
    )  # 0/4（视频与垃圾桶不计）
    b_c = await _seed_blogger("C", [5, 0, 0, 0])  # 1/4
    b_d = await _seed_blogger("D", [3, 0, 0, 0])  # 1/4，与 C 同键 → id 升序
    b_e = await _seed_blogger("E", [4, 0])  # 1/2
    b_f = await _seed_blogger("F", [4, 2, 0, 0])  # 2/4

    resp = client.get(API)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 6
    items = data["items"]
    assert [item["blogger_id"] for item in items] == [
        b_a,
        b_b,
        b_c,
        b_d,
        b_e,
        b_f,
    ]
    assert all(set(item) == FIELDS for item in items)

    by_id = {item["blogger_id"]: item for item in items}
    assert by_id[b_a] == {
        "blogger_id": b_a,
        "name": "A",
        "platform": "xiaohongshu",
        "avatar_path": "avatars/a.jpg",
        "image_count": 2,
        "rated_count": 0,
        "unrated_count": 2,
        "rating_average": None,
        "coverage": 0.0,
    }
    assert by_id[b_b]["image_count"] == 4  # 视频/垃圾桶图片不算
    assert by_id[b_b]["unrated_count"] == 4
    assert by_id[b_c]["image_count"] == 4
    assert by_id[b_c]["rated_count"] == 1
    assert by_id[b_c]["unrated_count"] == 3
    assert by_id[b_c]["rating_average"] == 5.0
    assert by_id[b_c]["coverage"] == 0.25
    assert by_id[b_d]["rating_average"] == 3.0
    assert by_id[b_e]["coverage"] == 0.5
    assert by_id[b_f]["rating_average"] == 3.0  # (4+2)/2，未评分不计入
    assert by_id[b_f]["coverage"] == 0.5


async def test_only_bloggers_with_unrated_images(client):
    """全已评分 / 只有视频 / 只有垃圾桶图片的博主都不进队列。"""
    unrated = await _seed_blogger("有待评", [5, 0])
    await _seed_blogger("全已评分", [5, 4])
    await _seed_blogger("只有视频", [], videos=3)
    await _seed_blogger("只有垃圾桶图", [], deleted_images=2)

    data = client.get(API).json()
    assert data["total"] == 1
    assert [item["blogger_id"] for item in data["items"]] == [unrated]


async def test_limit_and_total(client):
    """``total`` 是符合条件总数，不受 limit 影响；limit 取前 N 位。"""
    ids = [await _seed_blogger(f"博{i}", [0]) for i in range(5)]

    data = client.get(API, params={"limit": 2}).json()
    assert data["total"] == 5
    assert [item["blogger_id"] for item in data["items"]] == ids[:2]

    full = client.get(API, params={"limit": 2000}).json()
    assert len(full["items"]) == 5


@pytest.mark.parametrize("limit", [0, 2001])
def test_limit_validation(client, limit):
    assert client.get(API, params={"limit": limit}).status_code == 422


# ── /{blogger_id}/random 的 unrated 筛选 ──


async def test_random_unrated_filter(client):
    """unrated=true 只抽未评分素材，可与 media_type 组合（评分页传 image）。"""
    blogger_id = await _seed_blogger(
        "随机", [5, 5, 0, 0], videos=1
    )  # 图片 2 已评 + 2 未评；视频 1 条未评分

    images = client.get(
        f"/api/bloggers/{blogger_id}/random",
        params={"unrated": "true", "media_type": "image", "size": 10},
    ).json()
    assert images["total"] == 2
    assert len(images["items"]) == 2
    assert {item["media_type"] for item in images["items"]} == {"image"}
    assert all(not item["rating"] for item in images["items"])

    videos = client.get(
        f"/api/bloggers/{blogger_id}/random",
        params={"unrated": "true", "media_type": "video", "size": 10},
    ).json()
    assert videos["total"] == 1
    assert videos["items"][0]["media_type"] == "video"

    all_images = client.get(
        f"/api/bloggers/{blogger_id}/random",
        params={"media_type": "image", "size": 10},
    ).json()
    assert all_images["total"] == 4  # 不传 unrated 时包含已评分


def test_random_not_found_404(client):
    """人物不存在 → 404（带不带 unrated 都一样）。"""
    assert client.get("/api/bloggers/999999/random").status_code == 404
    assert (
        client.get(
            "/api/bloggers/999999/random",
            params={"unrated": "true", "media_type": "image"},
        ).status_code
        == 404
    )
