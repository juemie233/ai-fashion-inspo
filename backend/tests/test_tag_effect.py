"""标签使用效果分析测试：热度升降榜 / 组合排行 / 覆盖度 / 来源分布。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import Tag


def _create_tag(client, name: str, category: str = "free") -> dict:
    r = client.post("/api/tags", json={"name": name, "category": category})
    assert r.status_code == 201, r.text
    return r.json()


def _link(client, insp_id: str, names: list[str]) -> None:
    r = client.post(f"/api/inspirations/{insp_id}/tags", json={"names": names})
    assert r.status_code == 200, r.text


async def _backdate_inspiration(insp_id: str, days: int) -> None:
    """把素材创建时间回拨（模拟历史素材，供升降榜分窗口）。"""
    async with async_session() as db:
        await db.execute(
            update(Inspiration)
            .where(Inspiration.id == insp_id)
            .values(created_at=datetime.now(timezone.utc) - timedelta(days=days))
        )
        await db.commit()


async def test_trending_rising_and_falling(client, upload):
    """近窗口与旧窗口的标签分别进上升/下降榜。"""
    _create_tag(client, "升甲")
    _create_tag(client, "降乙")
    # 升甲：最近 1 次关联
    insp = upload().json()["id"]
    _link(client, insp, ["升甲"])
    # 降乙：45 天前 2 次关联（落在前一窗口内、当前窗口外）
    for _ in range(2):
        old_insp = upload().json()["id"]
        _link(client, old_insp, ["降乙"])
        await _backdate_inspiration(old_insp, 45)

    r = client.get("/api/tags/effect/trending", params={"days": 30, "top": 10})
    assert r.status_code == 200
    data = r.json()
    rising_names = [i["name"] for i in data["rising"]]
    falling_names = [i["name"] for i in data["falling"]]
    assert "升甲" in rising_names
    assert "降乙" in falling_names
    rising = next(i for i in data["rising"] if i["name"] == "升甲")
    assert rising["current"] == 1 and rising["previous"] == 0
    falling = next(i for i in data["falling"] if i["name"] == "降乙")
    assert falling["current"] == 0 and falling["previous"] == 2


async def test_combinations_ranked(client, upload):
    """组合排行按共现次数降序，min_count 过滤弱组合。"""
    _create_tag(client, "组合甲")
    _create_tag(client, "组合乙")
    _create_tag(client, "组合丙")
    for _ in range(2):
        insp = upload().json()["id"]
        _link(client, insp, ["组合甲", "组合乙"])
    insp = upload().json()["id"]
    _link(client, insp, ["组合乙", "组合丙"])

    r = client.get("/api/tags/effect/combinations", params={"limit": 10, "min_count": 2})
    data = r.json()
    assert data["total"] == 1  # 只有 (组合甲, 组合乙) 共现 2 次达标
    pair = data["pairs"][0]
    assert pair["count"] == 2
    assert set(pair["tags"]) == {"组合甲", "组合乙"}


async def test_coverage_stats(client, upload):
    """覆盖度：带标签比例与平均标签数。"""
    _create_tag(client, "覆盖甲")
    _create_tag(client, "覆盖乙")
    insp1 = upload().json()["id"]
    _link(client, insp1, ["覆盖甲", "覆盖乙"])
    upload()  # 第二个素材不打标签

    r = client.get("/api/tags/effect/coverage")
    data = r.json()
    assert data["inspiration_total"] == 2
    assert data["with_tags"] == 1
    assert data["tagged_ratio"] == 0.5
    assert data["avg_tags_per_inspiration"] == 1.0


async def test_source_dist(client, upload):
    """来源分布 + 低效 AI 标签。"""
    m = _create_tag(client, "手动甲")
    ai = _create_tag(client, "AI噪乙")
    async with async_session() as db:
        await db.execute(update(Tag).where(Tag.id == ai["id"]).values(source="ai_generated"))
        await db.commit()
    insp = upload().json()["id"]
    _link(client, insp, ["手动甲", "AI噪乙"])

    r = client.get("/api/tags/effect/source_dist")
    data = r.json()
    assert set(data["by_source"].keys()) == {"manual", "ai_generated"}
    assert data["by_source"]["manual"]["tag_count"] == 1
    assert data["by_source"]["manual"]["usage_total"] == 1
    assert ai["id"] in [t["id"] for t in data["top_low_quality"]]
    assert m["id"] not in [t["id"] for t in data["top_low_quality"]]
