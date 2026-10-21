"""标签效果分析测试（升降榜 / 组合排行 / 覆盖度 / 来源分布）。

这些数据是「标签体系该不该清理」的决策依据，两类错误最要命：
1. **把垃圾桶素材算进去** → 丢弃的废图让某个标签看起来暴涨；
2. **升降榜方向反了** → 该扩的标签被判定为衰退。

时间窗口用「素材 created_at 分桶」而不是关联时间，所以测试要能把素材造到
不同日期上，这也正是最容易写错的地方。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services.tag_effect import (
    get_tag_combinations,
    get_tag_coverage,
    get_tag_source_dist,
    get_trending_tags,
)

NOW = datetime.now(timezone.utc)


async def _tag(name: str, category: str = "item", source: str = "seed") -> int:
    async with async_session() as db:
        tag = Tag(name=name, category=category, source=source)
        db.add(tag)
        await db.commit()
        return tag.id


async def _link(insp_id: str, tag_ids: list[int], *, days_ago: float = 0, deleted: bool = False):
    """造素材（可指定创建于几天前 / 已进垃圾桶）并关联标签。"""
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg",
                media_type="image",
                quality_status="approved",
                created_at=NOW - timedelta(days=days_ago),
                deleted_at=NOW if deleted else None,
            )
        )
        await db.flush()
        for tag_id in tag_ids:
            db.add(
                InspirationTag(
                    inspiration_id=insp_id, tag_id=tag_id, confidence=0.9, source="ai_generated"
                )
            )
        await db.commit()


async def test_trending_detects_rising_and_falling(client):
    """最近窗口多 = 上升；前一窗口多 = 下降。"""
    rising, falling = await _tag("上升标签"), await _tag("下降标签")

    for i in range(3):
        await _link(f"r{i}", [rising], days_ago=1)      # 本窗口 3
    await _link("r-old", [rising], days_ago=40)          # 上一窗口 1
    await _link("f-new", [falling], days_ago=1)          # 本窗口 1
    for i in range(3):
        await _link(f"f{i}", [falling], days_ago=40)     # 上一窗口 3

    async with async_session() as db:
        data = await get_trending_tags(db, days=30, top=10)

    rising_names = [t["name"] for t in data["rising"]]
    falling_names = [t["name"] for t in data["falling"]]
    assert "上升标签" in rising_names
    assert "下降标签" in falling_names
    assert data["days"] == 30

    rising_row = next(t for t in data["rising"] if t["name"] == "上升标签")
    assert rising_row["current"] == 3 and rising_row["previous"] == 1
    assert rising_row["delta"] == 2


async def test_trending_excludes_trashed_assets(client):
    """垃圾桶素材不参与升降榜（否则丢弃的废图会伪造「标签暴涨」）。"""
    tag = await _tag("被丢弃的标签")
    await _link("d1", [tag], days_ago=1)
    await _link("d2", [tag], days_ago=1, deleted=True)
    await _link("d3", [tag], days_ago=1, deleted=True)

    async with async_session() as db:
        data = await get_trending_tags(db, days=30, top=10)

    row = next(t for t in data["rising"] if t["name"] == "被丢弃的标签")
    assert row["current"] == 1  # 只算未删除那条


async def test_trending_ignores_old_beyond_two_windows(client):
    """两窗口之外的历史不参与对比（否则老标签的既有量会通过 previous 影响排序）。"""
    tag = await _tag("远古标签")
    await _link("o1", [tag], days_ago=200)

    async with async_session() as db:
        data = await get_trending_tags(db, days=30, top=10)

    assert all(t["name"] != "远古标签" for t in data["rising"] + data["falling"])


async def test_trending_falls_back_to_id_when_tag_missing(client):
    """标签行被删但关联残留：用 #id 占位而不是 KeyError/丢行。"""
    tag = await _tag("即将消失的标签")
    await _link("m1", [tag], days_ago=1)
    async with async_session() as db:
        # 外键是 ON DELETE CASCADE，直接删标签会连关联一起删；这里模拟"关联残留"
        await db.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=OFF"))
        await db.execute(__import__("sqlalchemy").text("DELETE FROM tags WHERE id=:i"), {"i": tag})
        await db.commit()

        data = await get_trending_tags(db, days=30, top=10)

    assert any(t["name"] == f"#{tag}" for t in data["rising"])


async def test_combinations_count_cooccurrence(client):
    """组合排行：同素材出现的标签对计数，min_count 过滤低频。"""
    a, b, c = await _tag("A标签"), await _tag("B标签"), await _tag("C标签")
    await _link("c1", [a, b], days_ago=1)
    await _link("c2", [a, b], days_ago=1)
    await _link("c3", [a, c], days_ago=1)

    async with async_session() as db:
        data = await get_tag_combinations(db, limit=10, min_count=2)

    assert data["pairs"][0] == {"tags": ["A标签", "B标签"], "count": 2}
    assert all(p["count"] >= 2 for p in data["pairs"])
    assert data["total"] == 1  # A+C 只有 1 次，被 min_count 过滤


async def test_combinations_exclude_trashed(client):
    a, b = await _tag("垃圾桶A"), await _tag("垃圾桶B")
    await _link("t1", [a, b], days_ago=1)
    await _link("t2", [a, b], days_ago=1, deleted=True)
    await _link("t3", [a, b], days_ago=1, deleted=True)

    async with async_session() as db:
        data = await get_tag_combinations(db, limit=10, min_count=2)

    assert data["pairs"] == []  # 只剩 1 条有效


async def test_combinations_returns_empty_when_less_than_two_tags(client):
    await _tag("独苗标签")
    async with async_session() as db:
        data = await get_tag_combinations(db)
    assert data == {"pairs": [], "total": 0}


async def test_combinations_skip_pathological_asset(client, monkeypatch):
    """单素材标签数超阈值时跳过它（组合数是平方级，会拖垮接口）。"""
    from app.services import tag_effect

    monkeypatch.setattr(tag_effect, "_MAX_TAGS_PER_INSPIRATION", 2)
    ids = [await _tag(f"P{i}") for i in range(3)]
    await _link("heavy", ids, days_ago=1)

    async with async_session() as db:
        data = await get_tag_combinations(db, limit=10, min_count=1)

    assert data["pairs"] == []


async def test_coverage_reports_ratio_and_categories(client):
    item, style = await _tag("覆盖单品", "item"), await _tag("覆盖风格", "style")
    await _link("cov1", [item, style], days_ago=1)
    await _link("cov2", [], days_ago=1)  # 有素材但无标签

    async with async_session() as db:
        data = await get_tag_coverage(db)

    assert data["inspiration_total"] >= 2
    assert data["with_tags"] >= 1
    assert 0 < data["tagged_ratio"] <= 1
    assert data["avg_tags_per_inspiration"] >= 1
    # by_category 是 {类别: 覆盖率}
    assert {"item", "style"} <= set(data["by_category"])


async def test_coverage_empty_library(client):
    """空库不能除零（前端首次打开就该显示 0 而不是 NaN）。"""
    async with async_session() as db:
        await db.execute(__import__("sqlalchemy").text("DELETE FROM inspiration_tags"))
        await db.execute(__import__("sqlalchemy").text("DELETE FROM inspirations"))
        await db.commit()
        data = await get_tag_coverage(db)

    assert data["inspiration_total"] == 0
    assert data["tagged_ratio"] == 0
    assert data["avg_tags_per_inspiration"] == 0


async def test_source_distribution_groups_by_source(client):
    tag = await _tag("来源标签")
    await _link("s1", [tag], days_ago=1)
    await _link("s2", [tag], days_ago=1)

    async with async_session() as db:
        data = await get_tag_source_dist(db)

    assert set(data) == {"by_source", "top_low_quality"}
    assert "seed" in data["by_source"]
    row = data["by_source"]["seed"]
    assert set(row) == {"tag_count", "usage_total", "avg_usage"}


@pytest.mark.parametrize("days,top", [(7, 5), (30, 20)])
async def test_trending_respects_window_and_top(client, days, top):
    tag = await _tag(f"窗口标签{days}")
    await _link(f"w{days}", [tag], days_ago=1)

    async with async_session() as db:
        data = await get_trending_tags(db, days=days, top=top)

    assert data["days"] == days
    assert len(data["rising"]) <= top
    assert len(data["falling"]) <= top
