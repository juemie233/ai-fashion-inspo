"""标签健康度「问题明细」分页（tag_health.get_health_issue_detail）的单测。

明细接口是用户点开问题卡片后看到的那一屏。它有两个**必须实时复校**的语义，
否则用户会看到「点进去发现没问题」或者干脆白屏：

1. 扫描结果只是历史**快照**。期间标签可能被改名（已经合法了）、被删除，
   也可能规则阈值调整过。明细必须按当前状态重新校验/过滤——
   - 已合法的标签不能继续挂在「不规范命名」列表里；
   - 已删除的标签不能出现在配对里（否则响应带 null，前端渲染直接报错）。
2. 未知问题类型要报错，而不是静默返回空列表（否则前端永远显示「0 条」，
   排查时以为是没扫到）。

这些分支以前只有 API 测试覆盖，而 TestClient 请求不在覆盖率追踪范围内，
所以这里直接调用服务函数。
"""

import pytest

from app.database import async_session
from app.models.tag import Tag
from app.services.tag_health import _fetch_tag_items, get_health_issue_detail


async def _tag(name: str, category: str = "单品") -> int:
    async with async_session() as db:
        t = Tag(name=name, category=category, source="manual")
        db.add(t)
        await db.commit()
        return t.id


async def test_unknown_issue_type_raises():
    """未知问题类型必须报错——静默返回空列表会让「0 条」看起来像正常结果。"""
    async with async_session() as db:
        with pytest.raises(ValueError, match="未知问题类型"):
            await get_health_issue_detail(db, {"issues": {"orphan": {"tag_ids": []}}}, "不存在")


async def test_fetch_tag_items_empty_ids_short_circuits():
    async with async_session() as db:
        assert await _fetch_tag_items(db, []) == {}


async def test_orphan_detail_skips_deleted_tags():
    """分页取明细时，快照里已被删除的标签要跳过（不返回 None 槽位）。"""
    alive = await _tag("针织开衫")
    result = {"issues": {"orphan": {"tag_ids": [alive, 999999]}}}

    async with async_session() as db:
        detail = await get_health_issue_detail(db, result, "orphan")

    assert detail["total"] == 2  # 总数按快照算
    assert [i["id"] for i in detail["items"]] == [alive]  # 但明细只给存在的
    assert all(i is not None for i in detail["items"])


async def test_orphan_detail_paginates():
    ids = [await _tag(f"孤儿标签{i}") for i in range(3)]
    result = {"issues": {"orphan": {"tag_ids": ids}}}

    async with async_session() as db:
        page2 = await get_health_issue_detail(db, result, "orphan", page=2, size=2)

    assert page2["total"] == 3
    assert page2["page"] == 2
    assert [i["id"] for i in page2["items"]] == [ids[2]]


async def test_duplicate_detail_drops_pairs_with_deleted_tag():
    """配对里任一标签已删除 → 整对过滤掉（否则前端拿到 null 崩溃）。"""
    a = await _tag("阔腿裤")
    b = await _tag("阔腿长裤")
    result = {
        "issues": {
            "duplicate": {
                "pairs": [
                    {"tag_a_id": a, "tag_b_id": b, "similarity": 0.92},
                    {"tag_a_id": a, "tag_b_id": 999999, "similarity": 0.88},
                ]
            }
        }
    }

    async with async_session() as db:
        detail = await get_health_issue_detail(db, result, "duplicate")

    assert detail["total"] == 2  # 总数仍按快照
    assert len(detail["items"]) == 1  # 但只返回两边都在的那对
    item = detail["items"][0]
    assert item["tag_a"]["name"] == "阔腿裤"
    assert item["tag_b"]["name"] == "阔腿长裤"
    assert item["similarity"] == 0.92


async def test_low_quality_detail_revalidates_name_in_realtime():
    """扫描后被改名的标签必须从「不规范」列表里消失（实时复校，不用快照结论）。"""
    legal = await _tag("针织开衫")
    too_long = await _tag("这是一个特别特别特别长的标签名字超过上限")
    result = {"issues": {"low_quality_name": {"tag_ids": [legal, too_long]}}}

    async with async_session() as db:
        detail = await get_health_issue_detail(db, result, "low_quality_name")

    assert detail["total"] == 1  # 已合法的被过滤
    assert detail["items"][0]["id"] == too_long
    assert detail["items"][0]["reason"]  # 原因取实时校验结果，不是含糊兜底


async def test_noncompliant_detail_revalidates_and_labels_reason():
    """违禁词明细：实时分类并带上中文原因标签。"""
    normal = await _tag("针织开衫")
    # 裸品类名（无颜色/长度修饰）属不合规：规则见 tag_compliance.is_bare_hosiery_word
    banned = await _tag("过膝袜")
    result = {"issues": {"noncompliant": {"tag_ids": [normal, banned]}}}

    async with async_session() as db:
        detail = await get_health_issue_detail(db, result, "noncompliant")

    assert detail["total"] == 1  # 「针织开衫」合规，被实时复校过滤
    assert detail["items"][0]["id"] == banned
    assert detail["items"][0]["reason"]  # 分类码
    assert detail["items"][0]["reason_label"]  # 中文展示文案


async def test_low_quality_detail_skips_deleted_tag_ids():
    """快照里的标签已删除：跳过而不报错（扫描结果可能比库更旧）。"""
    too_long = await _tag("另一个特别特别特别长的标签名字超过上限了")
    result = {"issues": {"low_quality_name": {"tag_ids": [999999, too_long]}}}

    async with async_session() as db:
        detail = await get_health_issue_detail(db, result, "low_quality_name")

    assert [i["id"] for i in detail["items"]] == [too_long]
