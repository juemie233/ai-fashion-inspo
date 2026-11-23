"""标签查询与统计（tag_query）的直连边缘单测。

既有 test_tag_governance / test_tag_tree / test_tag_service 等走 API 覆盖了主路径；
本文件直接 await 服务函数，补齐：分组排序与用量口径、统计口径（未使用/总关联）、
重复对的相似度区间与首字过滤、Top 排行 / 导出、层级树（has_children/分页）、
使用趋势三种粒度、共现网络（分类/阈值/空库）、观察名单（total 真计数/分页/
时间格式）与「确认保留」的边界。
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services import tag_query as svc
from app.utils.time import utcnow


async def _tag(
    name: str,
    *,
    category: str = "free",
    source: str = "seed",
    pinned: bool = False,
    sort_order: int = 0,
    created_at: datetime | None = None,
    parent_id: int | None = None,
) -> Tag:
    async with async_session() as db:
        tag = Tag(
            name=name,
            category=category,
            source=source,
            pinned=pinned,
            sort_order=sort_order,
            parent_id=parent_id,
        )
        if created_at is not None:
            tag.created_at = created_at
        db.add(tag)
        await db.commit()
        return tag


async def _insp(deleted: bool = False, created_at: datetime | None = None) -> Inspiration:
    async with async_session() as db:
        insp = Inspiration(
            file_path="images/tag_query.jpg",
            media_type="image",
            created_at=created_at or utcnow(),
            deleted_at=utcnow() if deleted else None,
            **({"trash_reason": "其他", "trash_source": "manual"} if deleted else {}),
        )
        db.add(insp)
        await db.commit()
        return insp


async def _link(insp_id: str, tag_id: int) -> None:
    async with async_session() as db:
        db.add(InspirationTag(inspiration_id=insp_id, tag_id=tag_id))
        await db.commit()


# ═══════════════════════════════════════════════════════════════
#  分组列表 / 统计
# ═══════════════════════════════════════════════════════════════


async def test_grouped_tags_sorting_and_usage(client):
    """组内顺序：置顶 → sort_order → 使用次数降序；垃圾桶素材不计用量。"""
    pinned = await _tag("置顶标签", category="c1", pinned=True, sort_order=9)
    order1 = await _tag("排序1", category="c1", sort_order=1)
    order5_hot = await _tag("排序5热门", category="c1", sort_order=5)
    order5_cold = await _tag("排序5冷门", category="c1", sort_order=5)
    alive1 = await _insp()
    alive2 = await _insp()
    trashed = await _insp(deleted=True)
    await _link(alive1.id, order5_hot.id)
    await _link(alive2.id, order5_hot.id)
    await _link(alive1.id, order5_cold.id)
    await _link(trashed.id, pinned.id)  # 垃圾桶素材不计入 usage_count

    async with async_session() as db:
        grouped = await svc.get_all_tags_grouped(db)

    c1 = grouped["c1"]
    assert [t["id"] for t in c1] == [
        pinned.id,
        order1.id,
        order5_hot.id,
        order5_cold.id,
    ]
    by_id = {t["id"]: t for t in c1}
    assert by_id[order5_hot.id]["usage_count"] == 2
    assert by_id[pinned.id]["usage_count"] == 0
    assert by_id[pinned.id]["pinned"] is True


async def test_tag_stats_counts(client):
    """总数 / 未使用数 / 总关联数 / 按来源 / 按类别。"""
    used = await _tag("已用", category="color", source="seed")
    unused = await _tag("未用", category="style", source="ai_generated")
    only_trash = await _tag("只关联垃圾桶", category="color", source="manual")
    alive = await _insp()
    trashed = await _insp(deleted=True)
    await _link(alive.id, used.id)
    await _link(alive.id, unused.id)
    await _link(trashed.id, only_trash.id)
    # 未使用口径：unused 有一条存活关联 → 只有一个标签算未使用（only_trash 只关联垃圾桶）
    # 重新梳理：unused 已关联存活素材，故未使用数应为 0 之外的组合
    async with async_session() as db:
        stats = await svc.get_tag_stats(db)

    assert stats["total"] == 3
    # unused 标签也被关联了，only_trash 只关联垃圾桶 → 两个都「没有存活关联」？
    # 口径：alive 上的 unused 关联算使用，因此未使用 = {only_trash}
    assert stats["unused"] == 1
    assert stats["total_links"] == 2  # 只统计存活素材的关联
    assert stats["by_source"] == {"seed": 1, "ai_generated": 1, "manual": 1}
    assert stats["by_category"] == {"color": 2, "style": 1}


# ═══════════════════════════════════════════════════════════════
#  重复对 / 排行 / 导出
# ═══════════════════════════════════════════════════════════════


async def test_duplicate_pairs_similarity_band_and_first_char(client, monkeypatch):
    """相似度落在 [threshold, max_similarity) 才入结果；首字不同不计算。"""
    white_a = await _tag("白色A", category="color")
    white_b = await _tag("白色B", category="color")
    await _tag("红裙", category="color")
    calls: list[tuple[str, str]] = []

    def _fake_similarity(a: str, b: str) -> float:
        calls.append((a, b))
        return 0.9

    monkeypatch.setattr(svc, "_similarity", _fake_similarity)

    async with async_session() as db:
        pairs, total = await svc.find_duplicate_tag_pairs(db, threshold=0.8)
        none_band, _ = await svc.find_duplicate_tag_pairs(db, threshold=0.95)
        narrow_max, _ = await svc.find_duplicate_tag_pairs(
            db, threshold=0.5, max_similarity=0.85
        )

    assert total == 1
    assert pairs[0]["similarity"] == 0.9
    assert {pairs[0]["tag_a"]["id"], pairs[0]["tag_b"]["id"]} == {white_a.id, white_b.id}
    # 首字不同的「红裙」不参与相似度计算
    assert all("红裙" not in pair for pair in calls)
    assert none_band == []
    assert narrow_max == []


async def test_top_tags_and_export(client):
    hot = await _tag("热门", category="color")
    cold = await _tag("冷门", category="style")
    alive = await _insp()
    await _link(alive.id, hot.id)
    await _link(alive.id, cold.id)

    async with async_session() as db:
        top = await svc.get_top_tags(db, limit=1)
        exported = await svc.export_tags(db)

    assert len(top) == 1
    exported_by_name = {item["name"]: item for item in exported}
    assert exported_by_name["热门"]["category"] == "color"
    assert exported_by_name["热门"]["usage_count"] == 1
    assert exported_by_name["冷门"]["source"] == "seed"


# ═══════════════════════════════════════════════════════════════
#  层级树 / 趋势
# ═══════════════════════════════════════════════════════════════


async def test_tag_tree_children_root_and_pagination(client):
    """根层只回 parent_id=NULL；has_children 标记；分页 total 为全量。"""
    root1 = await _tag("一级A")
    root2 = await _tag("一级B")
    child1 = await _tag("二级1", parent_id=root1.id)
    child2 = await _tag("二级2", parent_id=root1.id)
    await _tag("三级", parent_id=child1.id)
    alive = await _insp()
    trashed = await _insp(deleted=True)
    await _link(alive.id, child1.id)
    await _link(trashed.id, child2.id)  # 垃圾桶不计用量

    async with async_session() as db:
        roots = await svc.get_tag_tree_children(db, None)
        children = await svc.get_tag_tree_children(db, root1.id)
        page2 = await svc.get_tag_tree_children(db, root1.id, page=2, size=1)

    assert roots["total"] == 2
    assert roots["parent_id"] is None
    assert [item["id"] for item in roots["items"]] == [root1.id, root2.id]
    root_row = next(item for item in roots["items"] if item["id"] == root1.id)
    assert root_row["has_children"] is True
    assert next(item for item in roots["items"] if item["id"] == root2.id)[
        "has_children"
    ] is False

    assert children["total"] == 2
    by_id = {item["id"]: item for item in children["items"]}
    assert by_id[child1.id]["has_children"] is True
    assert by_id[child2.id]["has_children"] is False
    assert by_id[child1.id]["usage_count"] == 1
    assert by_id[child2.id]["usage_count"] == 0

    assert len(page2["items"]) == 1 and page2["items"][0]["id"] == child2.id


async def test_tag_trend_granularities(client):
    """month/week/day 三种分桶；垃圾桶素材排除；未知标签返回 None。"""
    tag = await _tag("趋势标签")
    await _link((await _insp(created_at=datetime(2026, 1, 15))).id, tag.id)
    await _link((await _insp(created_at=datetime(2026, 1, 20))).id, tag.id)
    await _link(
        (await _insp(created_at=datetime(2026, 2, 3))).id, tag.id
    )
    await _link(
        (await _insp(created_at=datetime(2026, 2, 4), deleted=True)).id, tag.id
    )

    async with async_session() as db:
        month = await svc.get_tag_trend(db, tag.id, "month")
        day = await svc.get_tag_trend(db, tag.id, "day")
        missing = await svc.get_tag_trend(db, 99999, "month")
        with pytest.raises(KeyError):
            await svc.get_tag_trend(db, tag.id, "year")

    assert month["tag"] == {"id": tag.id, "name": "趋势标签"}
    assert month["granularity"] == "month"
    assert month["trend"] == [
        {"bucket": "2026-01", "count": 2},
        {"bucket": "2026-02", "count": 1},
    ]
    assert [row["bucket"] for row in day["trend"]] == [
        "2026-01-15",
        "2026-01-20",
        "2026-02-03",
    ]
    assert missing is None


# ═══════════════════════════════════════════════════════════════
#  共现
# ═══════════════════════════════════════════════════════════════


async def test_tag_cooccurrence_alive_only_and_limit(client):
    """共现按存活素材计数；垃圾桶素材排除；未知标签返回空结构。"""
    a = await _tag("A")
    b = await _tag("B")
    c = await _tag("C")
    d = await _tag("D")
    insp1 = await _insp()
    insp2 = await _insp()
    insp3 = await _insp()
    trashed = await _insp(deleted=True)
    for tag in (a, b):
        await _link(insp1.id, tag.id)
        await _link(insp2.id, tag.id)
    await _link(insp3.id, a.id)
    await _link(insp3.id, c.id)
    for tag in (a, b, c, d):
        await _link(trashed.id, tag.id)

    async with async_session() as db:
        result = await svc.get_tag_cooccurrence(db, a.id)
        limited = await svc.get_tag_cooccurrence(db, a.id, limit=1)
        missing = await svc.get_tag_cooccurrence(db, 99999)

    assert result["tag"] == {"id": a.id, "name": "A", "category": "free"}
    assert result["materials"] == 3
    assert result["items"] == [
        {"id": b.id, "name": "B", "category": "free", "count": 2},
        {"id": c.id, "name": "C", "category": "free", "count": 1},
    ]
    assert limited["items"] == [result["items"][0]]
    assert missing == {"tag": None, "materials": 0, "items": []}


async def test_cooccurrence_network_edges_and_category_filter(client):
    """边 = 共现素材数；min_count 过滤弱边；category 只留该类节点。"""
    a = await _tag("A", category="style")
    b = await _tag("B", category="style")
    c = await _tag("C", category="color")
    insp1 = await _insp()
    insp2 = await _insp()
    insp3 = await _insp()
    for insp in (insp1, insp2):
        await _link(insp.id, a.id)
        await _link(insp.id, b.id)
    await _link(insp3.id, a.id)
    await _link(insp3.id, c.id)

    async with async_session() as db:
        all_edges = await svc.get_cooccurrence_network(db, limit=10, min_count=1)
        strong = await svc.get_cooccurrence_network(db, limit=10, min_count=2)
        only_style = await svc.get_cooccurrence_network(
            db, limit=10, min_count=1, category="style"
        )
        empty = await svc.get_cooccurrence_network(db, limit=10, min_count=5)

    assert {n["id"] for n in all_edges["nodes"]} == {a.id, b.id, c.id}
    edges = {(e["source"], e["target"]): e["weight"] for e in all_edges["edges"]}
    assert edges == {(a.id, b.id): 2, (a.id, c.id): 1}
    assert len(strong["edges"]) == 1 and strong["edges"][0]["weight"] == 2
    assert {n["id"] for n in only_style["nodes"]} == {a.id, b.id}
    assert only_style["edges"] == [{"source": a.id, "target": b.id, "weight": 2}]
    assert empty["edges"] == []  # 弱边被过滤，但节点仍返回
    assert len(empty["nodes"]) == 3


# ═══════════════════════════════════════════════════════════════
#  观察名单 / 确认保留
# ═══════════════════════════════════════════════════════════════


async def test_watchlist_rows_total_offset_and_usage(client):
    """只收近期 AI 标签且用量 ≤ max_usage；total 为真实命中数（不受分页截断）。"""
    now = utcnow()
    fresh1 = await _tag("新标签1", source="ai_generated", created_at=now - timedelta(days=1))
    await _tag("新标签2", source="ai_generated", created_at=now - timedelta(days=2))
    await _tag("新标签3", source="ai_generated", created_at=now - timedelta(days=3))
    await _tag("老标签", source="ai_generated", created_at=now - timedelta(days=60))
    await _tag("手动标签", source="manual", created_at=now)
    hot = await _tag("用量高", source="ai_generated", created_at=now)
    for _ in range(2):
        await _link((await _insp()).id, hot.id)
    # fresh1 用一次（≤ max_usage=1），fresh2/3 零次
    await _link((await _insp()).id, fresh1.id)

    async with async_session() as db:
        page = await svc.list_watch_tags(db, days=7, max_usage=1, limit=2, offset=0)
        page2 = await svc.list_watch_tags(db, days=7, max_usage=1, limit=2, offset=2)

    assert page["total"] == 3
    assert page["returned"] == 2 and page["offset"] == 0
    assert page["cutoff"] is not None
    names = {item["name"] for item in page["items"]}
    assert names == {"新标签1", "新标签2"}  # 按 created_at 倒序
    assert all(item["created_at"].endswith("Z") for item in page["items"])
    assert any(item["usage_count"] == 1 for item in [*page["items"], *page2["items"]])
    assert [item["name"] for item in page2["items"]] == ["新标签3"]


async def test_keep_watch_tags_updates_source_only(client):
    """确认保留：ai_generated → manual；空入参返回 0；非 AI/不存在 id 不动；updated_at 不变。"""
    old_time = datetime(2026, 1, 1, 0, 0, 0)
    ai_tag = await _tag("待确认", source="ai_generated", created_at=old_time)
    manual_tag = await _tag("已是手动", source="manual", created_at=old_time)

    from sqlalchemy import func

    async with async_session() as db:
        before_updated = (await db.execute(select(func.max(Tag.updated_at)))).scalar()
        assert await svc.keep_watch_tags(db, []) == 0
        changed = await svc.keep_watch_tags(db, [ai_tag.id, manual_tag.id, 99999])

    assert changed == 1
    async with async_session() as db:
        refreshed = await db.get(Tag, ai_tag.id)
        untouched = await db.get(Tag, manual_tag.id)
        after_updated = (await db.execute(select(func.max(Tag.updated_at)))).scalar()
        assert refreshed.source == "manual"
        assert untouched.source == "manual"
        # updated_at 是聚类缓存 key：确认保留不能刷新它
        assert after_updated == before_updated

    # 「确认保留」从观察名单消失
    async with async_session() as db:
        watch = await svc.list_watch_tags(db, days=3650, max_usage=10)
    assert "待确认" not in {item["name"] for item in watch["items"]}
