"""负样本口径收窄（审查老 #9）。

垃圾桶素材默认全部作为负样本，但「隐私」不是「穿得不好」——学进去会让初筛器
把露肤/私密场景这类正常穿搭判成垃圾。现在排除隐私，并在统计里按删除原因分层展示。
"""

import asyncio

from sqlalchemy import text

from app.database import async_session
from app.services.quality_learner import NEGATIVE_EXCLUDED_TRASH_REASONS, collect_samples


def _trash(client, upload, reason: str) -> str:
    """把一条素材移入垃圾桶（直接写三字段，保证软删除不变量同真同假）。"""
    insp = upload().json()

    async def _do():
        async with async_session() as db:
            await db.execute(
                text(
                    "UPDATE inspirations SET deleted_at = CURRENT_TIMESTAMP, "
                    "trash_reason = :r, trash_source = 'manual' WHERE id = :i"
                ),
                {"r": reason, "i": insp["id"]},
            )
            await db.commit()

    asyncio.run(_do())
    return insp["id"]


def test_privacy_is_excluded_constant():
    assert "隐私" in NEGATIVE_EXCLUDED_TRASH_REASONS


def test_privacy_not_counted_as_negative(client, upload):
    """隐私素材不计入负样本，其它原因仍计入，并出现在分层统计里。"""
    _trash(client, upload, "隐私")
    _trash(client, upload, "质量差")

    async def _collect():
        async with async_session() as db:
            return await collect_samples(db)

    _x, _y, stats = asyncio.run(_collect())
    assert "隐私" not in stats["negative_by_reason"], stats["negative_by_reason"]
    assert stats["negative_by_reason"].get("质量差", 0) >= 1, stats["negative_by_reason"]
    assert stats["excluded_reasons"] == ["隐私"]
