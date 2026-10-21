"""搜索建议与标签共现的测试（两个只读查询）。

为什么要测：这两条是「搜索框自动补全」和「标签详情页常一起出现」的数据源，
写错了不会报错、只会**静默给出错误的排序或空列表**——用户会以为库里没数据。
"""

from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services.search_suggest_service import search_suggestions, tag_cooccurrence


async def _seed(rows: list[tuple[str, list[str]]]) -> None:
    """按 [[素材 id, [标签名...]]] 造素材与标签（同名标签复用）。"""
    async with async_session() as db:
        for insp_id, tag_names in rows:
            db.add(
                Inspiration(
                    id=insp_id,
                    source_type="douyin",
                    file_path=f"images/x/{insp_id}.jpg",
                    media_type="image",
                    quality_status="approved",
                )
            )
            for name in tag_names:
                tag = (
                    await db.execute(select(Tag).where(Tag.name == name))
                ).scalar_one_or_none()
                if tag is None:
                    tag = Tag(name=name, category="item", source="ai_generated")
                    db.add(tag)
                    await db.flush()
                db.add(
                    InspirationTag(
                        inspiration_id=insp_id, tag_id=tag.id, confidence=0.9, source="ai_generated"
                    )
                )
        await db.commit()


async def test_suggestions_rank_by_usage(client):
    """按使用次数降序：常用的先补全（否则用户要多打几个字才命中）。"""
    await _seed(
        [
            ("s1", ["针织开衫", "阔腿裤"]),
            ("s2", ["针织开衫"]),
            ("s3", ["针织衫"]),
        ]
    )
    async with async_session() as db:
        rows = await search_suggestions(db, "针织")

    assert rows[0] == {"name": "针织开衫", "usage_count": 2}
    assert {r["name"] for r in rows} == {"针织开衫", "针织衫"}


async def test_suggestions_respect_limit_and_empty_query(client):
    await _seed([("l1", ["a1"]), ("l2", ["a2"]), ("l3", ["a3"])])
    async with async_session() as db:
        assert len(await search_suggestions(db, "a", limit=2)) == 2
        assert await search_suggestions(db, "不存在的标签") == []


async def test_suggestions_match_anywhere_in_name(client):
    """`contains` 语义：中缀也应命中（用户常只记得「开衫」两个字）。"""
    await _seed([("m1", ["米色针织开衫"])])
    async with async_session() as db:
        rows = await search_suggestions(db, "开衫")

    assert [r["name"] for r in rows] == ["米色针织开衫"]


async def test_cooccurrence_returns_shared_tags(client):
    """共现：与目标标签共享素材最多的排前，且不包含自己。"""
    await _seed(
        [
            ("c1", ["针织开衫", "阔腿裤", "米色"]),
            ("c2", ["针织开衫", "阔腿裤"]),
            ("c3", ["针织开衫", "白色"]),
        ]
    )
    async with async_session() as db:
        result = await tag_cooccurrence(db, "针织开衫")

    assert result["tag"] == "针织开衫"
    names = [r["name"] for r in result["related"]]
    assert names[0] == "阔腿裤"  # 共享 2 个素材
    assert set(names) == {"阔腿裤", "米色", "白色"}
    assert "针织开衫" not in names
    assert result["related"][0]["shared_count"] == 2
    assert result["related"][0]["category"] == "item"


async def test_cooccurrence_missing_tag_returns_empty(client):
    """标签不存在时返回空关联而不是 500（前端标签详情页可能传已删标签）。"""
    async with async_session() as db:
        result = await tag_cooccurrence(db, "已删除的标签")

    assert result == {"tag": "已删除的标签", "related": []}


async def test_cooccurrence_respects_limit(client):
    await _seed([("d1", ["主标签", "x1", "x2", "x3"])])
    async with async_session() as db:
        result = await tag_cooccurrence(db, "主标签", limit=2)

    assert len(result["related"]) == 2
