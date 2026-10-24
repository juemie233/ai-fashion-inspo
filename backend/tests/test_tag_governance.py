"""标签治理接口测试：AI 新标签观察名单（含保留动作）+ 单标签共现。

观察名单的意义：标签库 22,813 个里 99.9% 是 AI 打标自动建的、70% 只被用过 1~2 次，
膨胀源在「AI 一打一个新标签」。名单只捞「最近 N 天新建 + 用量仍很低」的 AI 标签，
人工保留（source→manual，从名单消失）或删除。
"""

from datetime import timedelta

from sqlalchemy import select

from app.database import async_session
from app.models.tag import InspirationTag, Tag
from app.utils.time import utcnow


async def _make_tag(name: str, *, source: str = "ai_generated", days_old: int = 0) -> int:
    """直接建一个标签（可指定来源与「多少天前创建」）。"""
    async with async_session() as db:
        tag = Tag(
            name=name,
            category="style",
            source=source,
            created_at=utcnow() - timedelta(days=days_old),
        )
        db.add(tag)
        await db.commit()
        return tag.id


async def _link(tag_id: int, inspiration_id: str) -> None:
    """把标签挂到素材上（AI 关联）。"""
    async with async_session() as db:
        db.add(
            InspirationTag(
                inspiration_id=inspiration_id, tag_id=tag_id, confidence=0.9, source="ai_generated"
            )
        )
        await db.commit()


def _upload(client, upload) -> str:
    """上传一张素材，返回 id。"""
    resp = upload()
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_watchlist_only_recent_ai_low_usage(client, upload):
    """只收「近 N 天 + AI 生成 + 用量 ≤ max_usage」；老标签 / 手动标签 / 高频标签都不进。"""
    import asyncio

    fresh = asyncio.run(_make_tag("新造风格A", days_old=1))
    old = asyncio.run(_make_tag("老造风格B", days_old=60))
    manual = asyncio.run(_make_tag("手动词C", source="manual", days_old=1))
    used = asyncio.run(_make_tag("被用过D", days_old=1))
    for _ in range(2):  # 用量 2 > max_usage=1
        asyncio.run(_link(used, _upload(client, upload)))

    resp = client.get("/api/tags/watchlist", params={"days": 7, "max_usage": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {item["name"] for item in body["items"]}
    assert "新造风格A" in names
    assert "老造风格B" not in names
    assert "手动词C" not in names
    assert "被用过D" not in names
    fresh_item = next(item for item in body["items"] if item["id"] == fresh)
    assert fresh_item["usage_count"] == 0
    assert body["days"] == 7 and body["max_usage"] == 1


def test_watchlist_keep_removes_from_list(client):
    """确认保留：source 改 manual，之后不再出现在观察名单。"""
    import asyncio

    tag_id = asyncio.run(_make_tag("待确认风格E", days_old=1))
    resp = client.post("/api/tags/watchlist/keep", json={"tag_ids": [tag_id]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["kept"] == 1

    async def _source() -> str:
        async with async_session() as db:
            tag = (await db.execute(select(Tag).where(Tag.id == tag_id))).scalar_one()
            return tag.source

    assert asyncio.run(_source()) == "manual"
    names = {item["name"] for item in client.get("/api/tags/watchlist", params={"days": 7}).json()["items"]}
    assert "待确认风格E" not in names

    # 再保留一次：已经是 manual，不再计数
    assert client.post("/api/tags/watchlist/keep", json={"tag_ids": [tag_id]}).json()["kept"] == 0
    # 参数缺失 → 400
    assert client.post("/api/tags/watchlist/keep", json={}).status_code == 400


def test_tag_cooccurrence_counts_alive_materials_only(client, upload):
    """共现只数「未删除素材」：垃圾桶素材里的同现不该把无关标签顶上来。"""
    import asyncio

    a = asyncio.run(_make_tag("外套A", source="manual"))
    b = asyncio.run(_make_tag("长裤B", source="manual"))
    c = asyncio.run(_make_tag("无关C", source="manual"))
    keep = _upload(client, upload)
    trash = _upload(client, upload)
    asyncio.run(_link(a, keep))
    asyncio.run(_link(b, keep))  # 与 A 在「保留」素材上同现
    asyncio.run(_link(a, trash))
    asyncio.run(_link(c, trash))  # 只与 A 在「垃圾桶」素材上同现
    resp = client.post(f"/api/inspirations/{trash}/trash")
    assert resp.status_code == 200, resp.text

    body = client.get(f"/api/tags/{a}/cooccurrence").json()
    assert body["materials"] == 1
    pairs = {item["id"]: item["count"] for item in body["items"]}
    assert pairs.get(b) == 1
    assert c not in pairs  # 只在垃圾桶素材里同现 → 不计


def test_tag_cooccurrence_unknown_tag(client):
    """标签不存在：返回空列表而不是 500（详情卡按空态渲染）。"""
    body = client.get("/api/tags/99999999/cooccurrence").json()
    assert body["tag"] is None
    assert body["items"] == []


def test_alias_suggestions_uses_band_not_topn(client):
    """别名建议必须按「区间」取，不能只按下限取再截断。

    回归（实测踩过）：之前复用 /tags/duplicates——那个接口先按相似度**倒序截断 50 条**，
    返回的全是 ≥0.75 的重复对；前端再过滤 <0.75 就空了。现在区间过滤发生在截断之前。
    """
    from app.services.tag_crud import _similarity
    from app.services.tag_query import find_duplicate_tag_pairs

    import asyncio

    base = "别名区间回归测试"
    high_a = asyncio.run(_make_tag(f"{base}甲", source="manual"))
    high_b = asyncio.run(_make_tag(f"{base}甲乙", source="manual"))
    sim = _similarity(f"{base}甲", f"{base}甲乙")
    assert sim >= 0.75, sim  # 构造出的这一对属于「重复」，不该出现在别名建议里

    async def _pairs(min_s: float, max_s: float) -> list[dict]:
        async with async_session() as db:
            pairs, _total = await find_duplicate_tag_pairs(db, min_s, max_s)
            return [p for p in pairs if {p["tag_a"]["id"], p["tag_b"]["id"]} == {high_a, high_b}]

    assert asyncio.run(_pairs(0.6, 0.75)) == []  # 高相似对不进别名区间
    assert asyncio.run(_pairs(0.6, 1.0)) != []  # 只要下限时能看到

    resp = client.get("/api/tags/alias-suggestions", params={"min_similarity": 0.6, "max_similarity": 0.75})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"min_similarity", "max_similarity", "total", "cached", "pairs"} <= set(body)
    for pair in body["pairs"]:
        assert 0.6 <= pair["similarity"] < 0.75
    # 第二次命中缓存（结果按「标签最后修改时间 + 下限」缓存）
    assert client.get("/api/tags/alias-suggestions").json()["cached"] is True

    # 区间非法 → 422
    bad = client.get("/api/tags/alias-suggestions", params={"min_similarity": 0.8, "max_similarity": 0.7})
    assert bad.status_code == 422


def test_watchlist_total_is_real_count_not_page_size(client):
    """total 必须是真实命中数，不能拿「本次返回条数」冒充。

    回归（实测踩过）：之前 total = len(rows)，被 limit 截断后恒等于 200，
    7/30/90 天都显示「200 个待确认」，而真实数分别是 2,849 / 7,308 / 13,021。
    """
    import asyncio

    for i in range(3):
        asyncio.run(_make_tag(f"总数回归{i}", days_old=1))

    body = client.get("/api/tags/watchlist", params={"days": 7, "max_usage": 1, "limit": 1}).json()
    assert body["returned"] == 1
    assert body["total"] >= 3, body
    assert len(body["items"]) == 1


def test_watchlist_keep_does_not_bump_updated_at(client):
    """「确认保留」只改 source，不该动 updated_at。

    为什么重要：updated_at 是聚类/别名扫描（30 秒量级）的缓存 key。观察名单里连着点
    几次保留，就把别名建议的缓存整批打掉——每次都要重算 29 秒。
    """
    import asyncio

    from sqlalchemy import func

    tag_id = asyncio.run(_make_tag("保留不动时间", days_old=1))

    async def _max_updated_at() -> str:
        async with async_session() as db:
            return str((await db.execute(select(func.max(Tag.updated_at)))).scalar() or "")

    before = asyncio.run(_max_updated_at())
    assert client.post("/api/tags/watchlist/keep", json={"tag_ids": [tag_id]}).json()["kept"] == 1
    after = asyncio.run(_max_updated_at())
    assert before == after


def test_merge_batch_merges_many_into_one(client):
    """批量合并：一次请求把多个源标签并进目标；单个失败不影响其余。"""
    import asyncio

    target = asyncio.run(_make_tag("批量目标", source="manual"))
    a = asyncio.run(_make_tag("批量源A", source="manual"))
    b = asyncio.run(_make_tag("批量源B", source="manual"))

    resp = client.post(
        "/api/tags/merge-batch", json={"source_ids": [a, b, 99_999_999], "target_id": target}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["merged"] == 2
    assert [item["id"] for item in body["failed"]] == [99_999_999]

    async def _exists(tag_id: int) -> bool:
        async with async_session() as db:
            return (await db.execute(select(Tag).where(Tag.id == tag_id))).scalar_one_or_none() is not None

    assert asyncio.run(_exists(target))
    assert not asyncio.run(_exists(a))
    assert not asyncio.run(_exists(b))

    # 参数校验：target 不能同时是 source；缺参 400
    bad = client.post("/api/tags/merge-batch", json={"source_ids": [target], "target_id": target})
    assert bad.status_code == 400
    assert client.post("/api/tags/merge-batch", json={"target_id": target}).status_code == 400


def test_watchlist_offset_pagination(client):
    """观察名单支持翻页：offset 让第 2 页与第 1 页不重叠，total 不随分页变化。"""
    import asyncio

    for i in range(3):
        asyncio.run(_make_tag(f"翻页回归{i}", days_old=1))

    first = client.get(
        "/api/tags/watchlist", params={"days": 7, "max_usage": 1, "limit": 1, "offset": 0}
    ).json()
    second = client.get(
        "/api/tags/watchlist", params={"days": 7, "max_usage": 1, "limit": 1, "offset": 1}
    ).json()
    assert first["total"] == second["total"] >= 3
    assert first["offset"] == 0 and second["offset"] == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
