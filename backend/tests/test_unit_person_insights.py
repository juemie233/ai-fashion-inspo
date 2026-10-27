"""人物洞察（person/insights）的测试：活跃度曲线、质量构成、风格相近。

这些是人物详情页顶部的聚合数字，错了不会报错、只会**默默给出错误的判断依据**：

- **补零**：前端画柱状图需要连续月份。缺月不补会出现「跳月」假象——看起来像
  「这个号断更了」，其实只是那个月没有素材；
- **只看未删除素材**：与素材列表/风格画像同口径。把垃圾桶素材算进去，用户删了素材
  数字还在涨；
- **风格相近只看同类型人物**（博对博、模特对模特）：跨类型比较「博主 vs 模特」的标签
  天然差异极大，会产生毫无意义的推荐；
- **共同标签数下限 + 相似度下限**：低于阈值的「相似」基本都是巧合，宁可不推荐。
"""

import math
from datetime import datetime, timedelta

import pytest
from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.person import Blogger, InspirationBlogger
from app.models.tag import InspirationTag, Tag
from app.services.person.insights import MIN_SHARED_TAGS, person_insights, similar_persons
from app.utils.time import utcnow


async def _blogger(name: str) -> int:
    async with async_session() as db:
        b = Blogger(name=name, platform="douyin", source="manual")
        db.add(b)
        await db.commit()
        return b.id


async def _insp(
    insp_id: str,
    *,
    quality: str | None = "approved",
    media_type: str = "image",
    created_at: datetime | None = None,
    deleted: bool = False,
) -> str:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg",
                media_type=media_type,
                quality_status=quality,
                created_at=created_at or utcnow(),
                deleted_at=utcnow() if deleted else None,
            )
        )
        await db.commit()
    return insp_id


async def _link(insp_id: str, blogger_id: int) -> None:
    async with async_session() as db:
        db.add(InspirationBlogger(inspiration_id=insp_id, blogger_id=blogger_id, confidence=1.0))
        await db.commit()


async def _tag_link(insp_id: str, name: str) -> int:
    from sqlalchemy import select

    async with async_session() as db:
        tag = (await db.execute(select(Tag).where(Tag.name == name))).scalar_one_or_none()
        if tag is None:
            tag = Tag(name=name, category="单品", source="manual")
            db.add(tag)
            await db.flush()
        db.add(InspirationTag(inspiration_id=insp_id, tag_id=tag.id, confidence=0.9))
        await db.commit()
        return tag.id


async def _insights(blogger_id: int, months: int = 12) -> dict:
    async with async_session() as db:
        return await person_insights(
            db, InspirationBlogger, InspirationBlogger.blogger_id, blogger_id, months=months
        )


# ── 活跃度 / 质量构成 ──


async def test_insights_empty_person_returns_zeroed_shape():
    bid = await _blogger("空博主")
    data = await _insights(bid)

    assert data["total"] == 0
    assert data["monthly"] and all(m["count"] == 0 for m in data["monthly"])
    assert data["quality"] == {"approved": 0, "pending": 0, "rejected": 0, "unreviewed": 0}
    assert data["first_added"] is None and data["last_added"] is None


async def test_insights_monthly_has_no_gaps():
    """补零：返回的月份必须连续（缺月会让柱状图看起来像断更）。"""
    bid = await _blogger("补零博主")
    await _insp("in1")
    await _link("in1", bid)

    data = await _insights(bid, months=6)

    months = [m["month"] for m in data["monthly"]]
    assert len(months) == 6
    # 字符串 YYYY-MM 连续：相邻月份要么同年月差 1，要么跨年 12→1
    for prev, cur in zip(months, months[1:]):
        py, pm = map(int, prev.split("-"))
        cy, cm = map(int, cur.split("-"))
        assert (cy * 12 + cm) - (py * 12 + pm) == 1


async def test_insights_monthly_includes_current_month():
    """窗口必须**含当月**：当月素材要能出现在曲线上（否则「还在更新吗」永远看不出来）。"""
    bid = await _blogger("当月博主")
    await _insp("cur1")
    await _link("cur1", bid)

    data = await _insights(bid, months=3)

    cur_month = utcnow().strftime("%Y-%m")
    assert [m["month"] for m in data["monthly"]][-1] == cur_month
    assert data["monthly"][-1]["count"] == 1


async def test_insights_counts_quality_and_media():
    bid = await _blogger("构成博主")
    await _insp("q1", quality="approved", media_type="image")
    await _insp("q2", quality="pending", media_type="video")
    await _insp("q3", quality=None, media_type="image")  # 列默认值会补成 pending
    for iid in ("q1", "q2", "q3"):
        await _link(iid, bid)
    # quality_status 是 NOT NULL（列默认值 pending），所以「未审核」桶只在历史脏数据
    # 上出现；这里只锁住正常三态的归类。

    data = await _insights(bid)

    assert data["total"] == 3
    assert data["quality"]["approved"] == 1
    assert data["quality"]["pending"] == 2  # q2 + q3（None 被列默认值补成 pending）
    assert data["quality"]["unreviewed"] == 0
    assert data["media"] == {"image": 2, "video": 1}


async def test_insights_excludes_trashed_material():
    """垃圾桶素材不计入洞察（与素材列表/风格画像同口径）。"""
    bid = await _blogger("有垃圾素材博主")
    await _insp("t1")
    await _insp("t2", deleted=True)
    await _link("t1", bid)
    await _link("t2", bid)

    data = await _insights(bid)

    assert data["total"] == 1
    assert data["quality"]["approved"] == 1


async def test_insights_first_and_last_added_are_extremes():
    bid = await _blogger("时间跨度博主")
    now = utcnow()
    old = now - timedelta(days=200)
    await _insp("f1", created_at=old)
    await _insp("f2", created_at=now)
    await _link("f1", bid)
    await _link("f2", bid)

    data = await _insights(bid)

    assert data["first_added"].startswith(str(old.year))
    assert data["last_added"].startswith(str(now.year))


async def test_insights_ignores_material_outside_window():
    """窗口外的素材计入总数与首末时间，但不进月度曲线（曲线只画近 N 个月）。"""
    bid = await _blogger("老素材博主")
    now = utcnow()
    await _insp("w1", created_at=now - timedelta(days=400))
    await _insp("w2", created_at=now)
    await _link("w1", bid)
    await _link("w2", bid)

    data = await _insights(bid, months=3)

    assert data["total"] == 2
    assert sum(m["count"] for m in data["monthly"]) == 1  # 只有近期的进曲线


# ── 风格相近 ──


async def _similar(blogger_id: int, limit: int = 5) -> list[dict]:
    async with async_session() as db:
        return await similar_persons(
            db, Blogger, InspirationBlogger, InspirationBlogger.blogger_id, blogger_id, limit=limit
        )


async def test_similar_persons_empty_when_no_tags():
    bid = await _blogger("无标签博主")
    assert await _similar(bid) == []


async def test_similar_persons_finds_shared_tag_peer():
    me = await _blogger("我")
    peer = await _blogger("同好")
    tags = ["针织开衫", "阔腿裤", "极简", "通勤"]
    for i, name in enumerate(tags):
        await _insp(f"s{i}")
        await _link(f"s{i}", me)
        await _tag_link(f"s{i}", name)
    # 同伴：共享 3 个标签（达到下限）+ 1 个自己的
    for i, name in enumerate(tags[:3] + ["甜美"]):
        await _insp(f"p{i}")
        await _link(f"p{i}", peer)
        await _tag_link(f"p{i}", name)

    out = await _similar(me)

    assert [r["id"] for r in out] == [peer]
    assert out[0]["shared_tags"] == MIN_SHARED_TAGS
    expected = MIN_SHARED_TAGS / math.sqrt(len(tags) * 4)
    assert out[0]["similarity"] == round(expected, 3)
    assert out[0]["inspiration_count"] == 4


async def test_similar_persons_skips_below_shared_threshold():
    """共同标签不足下限：不推荐（这类「相似」基本是巧合）。"""
    me = await _blogger("我2")
    peer = await _blogger("偶合")
    for i, name in enumerate(["针织开衫", "阔腿裤", "极简"]):
        await _insp(f"m{i}")
        await _link(f"m{i}", me)
        await _tag_link(f"m{i}", name)
    for i, name in enumerate(["针织开衫", "阔腿裤"]):  # 只共享 2 个
        await _insp(f"n{i}")
        await _link(f"n{i}", peer)
        await _tag_link(f"n{i}", name)

    assert await _similar(me) == []


async def test_similar_persons_ignores_trashed_material_of_peer():
    """互相「相似」的判定只看未删除素材：垃圾桶里的标签不能伪造相似度。"""
    me = await _blogger("我3")
    peer = await _blogger("垃圾相似")
    for i, name in enumerate(["针织开衫", "阔腿裤", "极简"]):
        await _insp(f"a{i}")
        await _link(f"a{i}", me)
        await _tag_link(f"a{i}", name)
    for i, name in enumerate(["针织开衫", "阔腿裤", "极简"]):
        await _insp(f"b{i}", deleted=True)
        await _link(f"b{i}", peer)
        await _tag_link(f"b{i}", name)

    assert await _similar(me) == []


async def test_similar_persons_respects_limit_and_order():
    me = await _blogger("我4")
    tags = ["针织开衫", "阔腿裤", "极简"]
    for i, name in enumerate(tags):
        await _insp(f"x{i}")
        await _link(f"x{i}", me)
        await _tag_link(f"x{i}", name)

    # 两个候选：一个只有 3 个共同标签（相似度更高），一个 3 个共同 + 一堆自己的（更低）
    close = await _blogger("很像")
    for i, name in enumerate(tags):
        await _insp(f"c{i}")
        await _link(f"c{i}", close)
        await _tag_link(f"c{i}", name)
    far = await _blogger("不太像")
    for i, name in enumerate(tags + ["甜美", "复古", "运动", "学院", "机能", "度假"]):
        await _insp(f"d{i}")
        await _link(f"d{i}", far)
        await _tag_link(f"d{i}", name)

    out = await _similar(me, limit=1)

    assert len(out) == 1
    assert out[0]["id"] == close  # 相似度高的排前面，limit 生效
