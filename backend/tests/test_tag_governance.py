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
