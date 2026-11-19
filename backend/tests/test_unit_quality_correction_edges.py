"""质量审核误判反馈服务补充测试：参数校验、状态同步、列表与统计分组。

对应 ``app/services/quality_correction.py`` 中既有 test_quality_correction.py
（仅走 API 正常路径）之外的直接调用分支：非法目标/原因码、素材不存在、
纠正为 rejected 不清原因文本、纠正为 approved 清空原因、分页倒序、
统计里 None 原因码归 unspecified、方向键格式。
"""

import pytest
from sqlalchemy import delete

from app.database import async_session
from app.models.inspiration import Inspiration, QualityCorrection
from app.services.quality_correction import (
    correction_stats,
    list_corrections,
    record_correction,
)


@pytest.fixture(autouse=True)
async def _clean_corrections():
    """quality_corrections 不在 conftest 的全局清表清单里，本文件自清防跨用例累计。"""
    async with async_session() as db:
        await db.execute(delete(QualityCorrection))
        await db.commit()
    yield
    async with async_session() as db:
        await db.execute(delete(QualityCorrection))
        await db.commit()


async def _add_insp(**overrides) -> str:
    row = Inspiration(file_path="images/q.jpg", media_type="image", **overrides)
    async with async_session() as db:
        db.add(row)
        await db.commit()
        return row.id


async def test_invalid_status_message():
    async with async_session() as db:
        with pytest.raises(ValueError) as exc:
            await record_correction(db, "any-id", "maybe")
    assert "approved 或 rejected" in str(exc.value)


async def test_invalid_reason_code_rejected_before_lookup():
    """原因码白名单先校验：即使素材不存在也先报参数错误。"""
    async with async_session() as db:
        with pytest.raises(ValueError) as exc:
            await record_correction(db, "missing-id", "approved", reason_code="我瞎编的")
    assert "reason_code 不合法" in str(exc.value)


async def test_unknown_inspiration_raises_lookup_error():
    async with async_session() as db:
        with pytest.raises(LookupError) as exc:
            await record_correction(db, "no-such-id", "approved")
    assert "素材未找到" in str(exc.value)


async def test_correct_to_approved_clears_reason_fields():
    """纠正为通过：状态改 approved + human，原因码/原因文本一并清空。"""
    insp_id = await _add_insp(
        quality_status="rejected",
        quality_reason="不是穿搭",
        quality_reason_code="not_outfit",
    )

    async with async_session() as db:
        body = await record_correction(db, insp_id, "approved", reason_code="not_outfit", note="其实是")
        row = await db.get(Inspiration, insp_id)
        correction = await db.get(QualityCorrection, body["id"])

    assert body["original_status"] == "rejected"
    assert body["corrected_status"] == "approved"
    assert body["reason_code"] == "not_outfit" and body["note"] == "其实是"
    assert row.quality_status == "approved"
    assert row.quality_reviewed_by == "human"
    assert row.quality_reason is None and row.quality_reason_code is None
    assert correction.original_status == "rejected"


async def test_correct_to_rejected_keeps_reason_text():
    """纠正为不通过：不改原因文本（AI 给的拒绝原因仍然成立），只改状态与来源。"""
    insp_id = await _add_insp(quality_status="approved", quality_reason="看着还行")

    async with async_session() as db:
        body = await record_correction(db, insp_id, "rejected", reason_code="low_quality")
        row = await db.get(Inspiration, insp_id)

    assert body["original_status"] == "approved"
    assert row.quality_status == "rejected"
    assert row.quality_reason == "看着还行"
    assert row.quality_reviewed_by == "human"


async def test_list_corrections_pagination_desc_order():
    """列表按 id 倒序分页，返回时间带 Z 后缀。"""
    insp_id = await _add_insp()
    async with async_session() as db:
        for i in range(3):
            await record_correction(db, insp_id, "approved" if i % 2 else "rejected")

        page1, total = await list_corrections(db, page=1, size=2)
        page2, _ = await list_corrections(db, page=2, size=2)

    assert total == 3
    assert len(page1) == 2 and len(page2) == 1
    assert page1[0]["id"] > page1[1]["id"] > page2[0]["id"]
    assert page1[0]["created_at"].endswith("Z")
    assert page2[0]["inspiration_id"] == insp_id


async def test_correction_stats_groups_none_reason_and_direction():
    """统计：None 原因码归 unspecified，方向键为 原判定->纠正。"""
    insp_id = await _add_insp()
    async with async_session() as db:
        await record_correction(db, insp_id, "approved")  # 不带原因码 → None
        await record_correction(db, insp_id, "rejected", reason_code="partial")
        stats = await correction_stats(db)

    assert stats["total"] == 2
    assert stats["by_reason_code"]["unspecified"] == 1
    assert stats["by_reason_code"]["partial"] == 1
    assert stats["by_direction"]["pending->approved"] == 1
    assert stats["by_direction"]["approved->rejected"] == 1
