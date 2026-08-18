"""标签-素材关联管理：解除关联与按标签查询素材列表。

依赖 tag_crud 的向量重建工具（_rebuild_vectors_for_tag_change），
不反向依赖任何其它标签模块。
"""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.tag import Tag, InspirationTag
from app.services.tag_crud import _rebuild_vectors_for_tag_change


async def batch_remove_tag_inspirations(
    db: AsyncSession, tag_id: int, inspiration_ids: list[str]
) -> int:
    """批量解除标签与多个素材的关联，返回解除数量。"""
    result = await db.execute(
        delete(InspirationTag).where(
            InspirationTag.tag_id == tag_id,
            InspirationTag.inspiration_id.in_(inspiration_ids),
        )
    )
    await db.commit()
    # 解除关联后素材标签集合变了，重建其文本向量（异步入队）。
    # enqueue 不内部提交，此处显式提交登记行（解除操作已在上方 commit）
    await _rebuild_vectors_for_tag_change(db, list(inspiration_ids))
    await db.commit()
    return result.rowcount


async def list_tag_inspirations(
    db: AsyncSession, tag_id: int, page: int, size: int, sort: str
) -> dict | None:
    """获取使用指定标签的素材列表（含分页与统计）。标签不存在返回 None。"""
    tag = await db.get(Tag, tag_id)
    if not tag:
        return None

    # 统计总数（排除垃圾桶素材，与素材列表口径一致）
    count_result = await db.execute(
        select(func.count())
        .select_from(InspirationTag)
        .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
        .where(
            InspirationTag.tag_id == tag_id,
            Inspiration.deleted_at.is_(None),
        )
    )
    total = count_result.scalar() or 0

    # 分页获取素材 — 只查需要的列，避免 Inspiration 的 selectin 预加载
    stmt = (
        select(
            Inspiration.id,
            Inspiration.file_path,
            Inspiration.thumbnail_path,
            Inspiration.media_type,
            Inspiration.created_at,
            InspirationTag.confidence,
        )
        .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
        .where(
            InspirationTag.tag_id == tag_id,
            Inspiration.deleted_at.is_(None),
        )
        .order_by(
            InspirationTag.confidence.desc() if sort == "confidence"
            else Inspiration.created_at.asc() if sort == "oldest"
            else Inspiration.created_at.desc()
        )
        .offset((page - 1) * size)
        .limit(size)
    )
    link_result = await db.execute(stmt)
    rows = link_result.all()

    items = [
        {
            "inspiration_id": row[0],
            "file_path": row[1],
            "thumbnail_path": row[2],
            "media_type": row[3],
            "confidence": round(row[5], 2) if row[5] else 0,
            "created_at": str(row[4]) if row[4] else None,
        }
        for row in rows
    ]

    return {
        "tag": {"id": tag.id, "name": tag.name, "category": tag.category},
        "items": items,
        "total": total,
        "page": page,
        "size": size,
    }
