"""AI 打标纠错服务补充测试（直接调用 service，不走 API）。

对应 ``app/services/tag_correction_service.py`` 中既有 test_tag_corrections.py
（API 层）之外的边界：非法原因/空标签名的 400、素材不存在 404、漏标幂等、
多标时标签不存在、已有标签类别沿用、备注空白归一、最新分析日志冗余
（排除 quality_check 日志）、列表筛选与未知原因标签、统计按版本聚合。
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.models.tag import InspirationTag, Tag
from app.models.tag_correction import TagCorrection
from app.services import tag_correction_service as service


async def _add_insp() -> str:
    row = Inspiration(file_path="images/tag.jpg", media_type="image")
    async with async_session() as db:
        db.add(row)
        await db.commit()
        return row.id


async def _add_tag(name: str, category: str = "item_type") -> int:
    async with async_session() as db:
        tag = Tag(name=name, category=category, source="manual")
        db.add(tag)
        await db.commit()
        return tag.id


async def _link(insp_id: str, tag_id: int, source: str = "manual") -> None:
    async with async_session() as db:
        db.add(InspirationTag(inspiration_id=insp_id, tag_id=tag_id, source=source))
        await db.commit()


async def _tag_ids(insp_id: str) -> set[int]:
    async with async_session() as db:
        rows = (
            await db.execute(
                select(InspirationTag.tag_id).where(
                    InspirationTag.inspiration_id == insp_id
                )
            )
        ).scalars().all()
    return set(rows)


@pytest.fixture
def vector_calls(monkeypatch):
    """替身文本向量重建：记录被重建的素材 ID，避免真实加载模型。"""
    from app.services import vector_service

    calls: list[str] = []

    async def _fake_rebuild(_db, inspiration_id: str) -> bool:
        calls.append(inspiration_id)
        return True

    monkeypatch.setattr(vector_service, "rebuild_text_vector", _fake_rebuild)
    return calls


async def test_invalid_reason_400():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await service.record_tag_correction(db, "any", "标签", "not-a-reason")
    assert exc.value.status_code == 400
    assert "未知反馈原因" in exc.value.detail


async def test_blank_tag_name_400():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await service.record_tag_correction(db, "any", "   ", "multi")
    assert exc.value.status_code == 400
    assert "标签名不能为空" in exc.value.detail


async def test_missing_inspiration_404():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await service.record_tag_correction(db, "no-such-id", "标签", "multi")
    assert exc.value.status_code == 404
    assert exc.value.detail == "素材未找到"


async def test_missing_reason_creates_tag_and_link(vector_calls):
    """漏标：按给定类别建标签并补关联，触发文本向量重建。"""
    insp_id = await _add_insp()

    async with async_session() as db:
        body = await service.record_tag_correction(
            db, insp_id, " 黑色连裤袜 ", "missing", note="补上", category="item_type"
        )

    assert body["recorded"] == 1
    assert body["reason"] == "missing" and body["action"] == "added"
    assert body["applied"] is True
    assert body["tag_name"] == "黑色连裤袜"
    assert body["category"] == "item_type"
    assert body["tag_id"] is not None
    assert vector_calls == [insp_id]
    assert body["tag_id"] in await _tag_ids(insp_id)


async def test_missing_reason_is_idempotent_for_existing_link(vector_calls):
    """已有关联的漏标反馈：只记录、不重复插入、不重建向量。"""
    insp_id = await _add_insp()
    tag_id = await _add_tag("黑色连裤袜")
    await _link(insp_id, tag_id)

    async with async_session() as db:
        body = await service.record_tag_correction(db, insp_id, "黑色连裤袜", "missing")

    assert body["applied"] is False
    assert vector_calls == []
    assert await _tag_ids(insp_id) == {tag_id}


async def test_multi_reason_removes_existing_link(vector_calls):
    """多标：删除该素材上该标签的关联，类别沿用库内标签。"""
    insp_id = await _add_insp()
    tag_id = await _add_tag("黑色过膝袜", category="item_type")
    await _link(insp_id, tag_id)
    await _link(insp_id, await _add_tag("白色"))

    async with async_session() as db:
        body = await service.record_tag_correction(db, insp_id, "黑色过膝袜", "multi")

    assert body["action"] == "removed" and body["applied"] is True
    assert body["tag_id"] == tag_id
    assert body["category"] == "item_type"
    assert tag_id not in await _tag_ids(insp_id)
    assert vector_calls == [insp_id]


async def test_multi_reason_unknown_tag_only_records(vector_calls):
    """多标但库里没有该标签：不改数据、tag_id 为 None、类别用入参。"""
    insp_id = await _add_insp()

    async with async_session() as db:
        body = await service.record_tag_correction(
            db, insp_id, "不存在的标签", "multi", category="style"
        )

    assert body["applied"] is False
    assert body["tag_id"] is None
    assert body["category"] == "style"
    assert vector_calls == []


async def test_noted_reasons_use_library_category():
    """类别错/名称不规范：仅记录；已有标签时类别以库内为准。"""
    insp_id = await _add_insp()
    await _add_tag("错类标签", category="color")

    async with async_session() as db:
        wrong = await service.record_tag_correction(db, insp_id, "错类标签", "wrong_category")
        bad = await service.record_tag_correction(db, insp_id, "不存在的名", "bad_name")

    assert wrong["action"] == "noted" and wrong["applied"] is False
    assert wrong["category"] == "color"
    assert bad["action"] == "noted" and bad["tag_id"] is None


async def test_blank_note_stored_as_none(vector_calls):
    """备注只有空白：落库为 None（避免出现「空字符串备注」）。"""
    insp_id = await _add_insp()
    async with async_session() as db:
        await service.record_tag_correction(db, insp_id, "标签A", "missing", note="   ")
        corrections = (await db.execute(select(TagCorrection))).scalars().all()

    assert len(corrections) == 1
    assert corrections[0].note is None


async def test_latest_analysis_log_skips_quality_check(vector_calls):
    """冗余最近一次「标签分析」日志：更新的 quality_check 日志不能顶替。"""
    insp_id = await _add_insp()
    async with async_session() as db:
        analysis = AIAnalysisLog(
            inspiration_id=insp_id,
            model_name="qwen3-vl:8b-instruct",
            log_type="analysis",
            raw_response='{"items": []}',
            prompt_version="v-old",
        )
        db.add(analysis)
        await db.commit()
        analysis_id = analysis.id

        db.add(
            AIAnalysisLog(
                inspiration_id=insp_id,
                model_name="qwen3-vl:8b-instruct",
                log_type="quality_check",
                raw_response="{}",
                prompt_version="v-new",
            )
        )
        await db.commit()

    async with async_session() as db:
        await service.record_tag_correction(db, insp_id, "标签B", "missing")
        row = (await db.execute(select(TagCorrection))).scalars().one()

    assert row.log_id == analysis_id
    assert row.prompt_version == "v-old"
    assert row.model_name == "qwen3-vl:8b-instruct"


async def test_list_corrections_filter_pagination_and_unknown_label(vector_calls):
    """列表：按原因筛选、分页 total 为筛选后总数、未知原因用原值兜底。"""
    insp_id = await _add_insp()
    async with async_session() as db:
        await service.record_tag_correction(db, insp_id, "标签C", "missing")
        await service.record_tag_correction(db, insp_id, "标签D", "bad_name")
        db.add(
            TagCorrection(
                inspiration_id=insp_id,
                tag_name="历史数据",
                category="free",
                reason="legacy_reason",
                action="noted",
            )
        )
        await db.commit()

        filtered = await service.list_tag_corrections(db, reason="missing")
        page1 = await service.list_tag_corrections(db, page=1, size=2)
        page2 = await service.list_tag_corrections(db, page=2, size=2)

    assert filtered["total"] == 1
    assert filtered["items"][0]["reason_label"] == "AI 漏标"
    assert page1["total"] == 3
    assert len(page1["items"]) == 2 and len(page2["items"]) == 1
    legacy = [item for item in page1["items"] if item["reason"] == "legacy_reason"]
    assert legacy and legacy[0]["reason_label"] == "legacy_reason"


async def test_stats_by_reason_action_and_prompt_version(vector_calls):
    """统计：按原因/动作/提示词版本聚合，removed/added 分别计数。"""
    insp_id = await _add_insp()
    async with async_session() as db:
        # 一条带分析日志（v1），一条没有日志
        db.add(
            AIAnalysisLog(
                inspiration_id=insp_id,
                model_name="m1",
                log_type="analysis",
                prompt_version="v1",
            )
        )
        await db.commit()
        await service.record_tag_correction(db, insp_id, "标签E", "missing")
        await service.record_tag_correction(db, insp_id, "标签F", "bad_name")

        stats = await service.tag_correction_stats(db, days=30)

    assert stats["total"] == 2
    reasons = {item["reason"]: item for item in stats["by_reason"]}
    assert reasons["missing"]["label"] == "AI 漏标"
    assert reasons["bad_name"]["count"] == 1
    actions = {item["action"]: item["count"] for item in stats["by_action"]}
    assert actions == {"added": 1, "noted": 1}
    # 两条纠错都冗余同一个素材最近一次分析日志（版本聚合口径：同一素材共享版本）
    version_rows = {row["prompt_version"]: row for row in stats["by_prompt_version"]}
    assert version_rows["v1"]["total"] == 2
    assert version_rows["v1"]["added"] == 1
    assert version_rows["v1"]["removed"] == 0


async def test_stats_empty():
    async with async_session() as db:
        stats = await service.tag_correction_stats(db)
    assert stats["total"] == 0
    assert stats["by_reason"] == [] and stats["by_action"] == []
    assert stats["by_prompt_version"] == []
