"""素材更新的服务层用例（补齐 `inspiration_update.py` 49% 的盲区）。

关注点：**人工复核翻案**的口径——改 `quality_status` 要记 `quality_reviewed_by="human"`，
翻案为 approved/pending 必须清空拒绝原因，翻案为 rejected 时原因以显式传入为准、
未传则清空（否则上一轮的原因会张冠李戴）；批量路径与单条语义必须一致。
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import Inspiration
from app.schemas.inspiration import InspirationUpdate
from app.services import inspiration_trash, inspiration_update


async def _reload(insp_id: str) -> Inspiration:
    async with async_session() as db:
        row = await db.get(Inspiration, insp_id)
        assert row is not None
        return row


async def test_update_missing_inspiration_404(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await inspiration_update.update_inspiration(
                db, "not-exist", InspirationUpdate(is_favorite=True)
            )
    assert exc.value.status_code == 404


async def test_update_plain_fields(client, upload):
    """收藏/评分/作者/疑似 AI：按显式提供字段更新。"""
    insp_id = upload().json()["id"]
    async with async_session() as db:
        await inspiration_update.update_inspiration(
            db,
            insp_id,
            InspirationUpdate(
                is_favorite=True,
                rating=4,
                source_author="某博主",
                is_ai_generated=True,
            ),
        )
        await db.commit()

    row = await _reload(insp_id)
    assert row.is_favorite is True
    assert row.rating == 4
    assert row.source_author == "某博主"
    assert row.is_ai_generated is True
    # 没改质量状态时不应写人工来源
    assert row.quality_reviewed_by != "human"


async def test_update_quality_status_records_human_and_clears_reason(client, upload):
    """翻案：记 human；approved/pending 清空原因；rejected 用显式原因。"""
    insp_id = upload().json()["id"]

    async with async_session() as db:
        await inspiration_update.update_inspiration(
            db,
            insp_id,
            InspirationUpdate(
                quality_status="rejected",
                quality_reason="重复",
            ),
        )
        await db.commit()
    row = await _reload(insp_id)
    assert row.quality_status == "rejected"
    assert row.quality_reason == "重复"
    assert row.quality_reviewed_by == "human"

    # 翻案为 approved：旧拒绝原因必须清掉（否则负样本口径与原因统计都会被污染）
    async with async_session() as db:
        await inspiration_update.update_inspiration(
            db, insp_id, InspirationUpdate(quality_status="approved")
        )
        await db.commit()
    row = await _reload(insp_id)
    assert row.quality_status == "approved"
    assert row.quality_reason is None

    # 再次 rejected 但未传原因：旧原因不能残留
    async with async_session() as db:
        await inspiration_update.update_inspiration(
            db, insp_id, InspirationUpdate(quality_status="rejected")
        )
        await db.commit()
    assert (await _reload(insp_id)).quality_reason is None


async def test_batch_favorite_skips_trashed(client, upload):
    """批量收藏只作用于未删除素材，返回实际更新行数。"""
    keep_id = upload().json()["id"]
    trashed_id = upload().json()["id"]
    async with async_session() as db:
        await inspiration_trash.trash_inspiration(db, trashed_id, "重复")

    async with async_session() as db:
        count = await inspiration_update.batch_favorite_inspirations(
            db, [keep_id, trashed_id, "not-exist"], True
        )
    assert count == 1
    assert (await _reload(keep_id)).is_favorite is True
    assert (await _reload(trashed_id)).is_favorite is False


async def test_batch_update_no_business_fields_is_noop(client, upload):
    """只带了 updated_at（没有任何业务字段）时直接返回 0，不写库。"""
    insp_id = upload().json()["id"]
    async with async_session() as db:
        count = await inspiration_update.batch_update_inspirations(db, [insp_id])
    assert count == 0


async def test_batch_update_fields_and_quality_reason_reset(client, upload):
    """批量改元数据：字段生效；改质量状态时 reason 清空、来源记 human。"""
    insp_a = upload().json()["id"]
    insp_b = upload().json()["id"]

    async with async_session() as db:
        await inspiration_update.update_inspiration(
            db, insp_a, InspirationUpdate(quality_status="rejected", quality_reason="质量差")
        )
        await db.commit()

    async with async_session() as db:
        count = await inspiration_update.batch_update_inspirations(
            db,
            [insp_a, insp_b],
            source_type="xiaohongshu",
            is_favorite=True,
            rating=2,
            quality_status="approved",
            is_ai_generated=False,
        )
    assert count == 2

    async with async_session() as db:
        rows = (
            await db.execute(
                select(Inspiration).where(Inspiration.id.in_([insp_a, insp_b]))
            )
        ).scalars().all()
    for row in rows:
        assert row.source_type == "xiaohongshu"
        assert row.is_favorite is True
        assert row.rating == 2
        assert row.quality_status == "approved"
        assert row.quality_reason is None  # 旧拒绝原因被清掉
        assert row.quality_reviewed_by == "human"
        assert row.is_ai_generated is False
