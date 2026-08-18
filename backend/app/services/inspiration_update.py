"""灵感素材更新：单条字段更新、批量元数据更新、批量收藏。"""

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.inspiration import Inspiration, NOT_DELETED, utcnow
from app.models.tag import InspirationTag
from app.schemas.inspiration import InspirationUpdate


async def update_inspiration(
    db: AsyncSession,
    inspiration_id: str,
    data: InspirationUpdate,
) -> Inspiration:
    """更新灵感（收藏状态、作者等部分字段），不存在则抛出 404。"""
    result = await db.execute(
        select(Inspiration)
        .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
        .where(Inspiration.id == inspiration_id)
    )
    inspiration = result.unique().scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")

    if data.is_favorite is not None:
        inspiration.is_favorite = data.is_favorite
    if data.rating is not None:
        inspiration.rating = data.rating
    if data.source_author is not None:
        inspiration.source_author = data.source_author
    if data.quality_status is not None:
        # 人工复核翻案：修改审核状态，同时处理原因
        inspiration.quality_status = data.quality_status
        if data.quality_status in ("approved", "pending"):
            inspiration.quality_reason = None
        elif data.quality_reason is not None:
            inspiration.quality_reason = data.quality_reason

    if data.is_ai_generated is not None:
        # 人工复核翻案：标记或取消「疑似 AI」标记
        inspiration.is_ai_generated = data.is_ai_generated

    await db.flush()
    await db.refresh(inspiration)
    return inspiration


async def batch_favorite_inspirations(
    db: AsyncSession,
    inspiration_ids: list[str],
    is_favorite: bool,
) -> int:
    """批量设置素材收藏状态，返回实际更新的行数。

    仅作用于未删除素材；已删除/不存在的 ID 被静默忽略。
    """
    result = await db.execute(
        update(Inspiration)
        .where(Inspiration.id.in_(inspiration_ids), NOT_DELETED)
        .values(is_favorite=is_favorite, updated_at=utcnow())
    )
    await db.commit()
    return result.rowcount


async def batch_update_inspirations(
    db: AsyncSession,
    inspiration_ids: list[str],
    *,
    source_type: str | None = None,
    is_favorite: bool | None = None,
    rating: int | None = None,
    quality_status: str | None = None,
    is_ai_generated: bool | None = None,
) -> int:
    """批量编辑素材元数据，仅更新显式提供的字段，返回实际更新行数。

    审核状态翻案为 approved/pending 时清空拒绝原因（与单条更新语义一致）。
    """
    values: dict = {"updated_at": utcnow()}
    if source_type is not None:
        values["source_type"] = source_type
    if is_favorite is not None:
        values["is_favorite"] = is_favorite
    if rating is not None:
        values["rating"] = rating
    if is_ai_generated is not None:
        values["is_ai_generated"] = is_ai_generated
    if quality_status is not None:
        values["quality_status"] = quality_status
        if quality_status in ("approved", "pending"):
            values["quality_reason"] = None

    if len(values) == 1:  # 仅 updated_at，无任何业务字段
        return 0

    result = await db.execute(
        update(Inspiration)
        .where(Inspiration.id.in_(inspiration_ids), NOT_DELETED)
        .values(**values)
    )
    await db.commit()
    return result.rowcount
