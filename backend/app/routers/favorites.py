"""收藏中心数据接口（2026-11-19 收藏增强 P0）。

只读聚合，供收藏中心页展示：

- ``GET /api/favorites/overview``：收藏总数、近 7 天新增、未评分、未整理，
  以及最近收藏素材（按收藏时间倒序）；
- ``GET /api/favorites/history``：最近收藏/取消事件流水（含精简素材信息）。

口径：
- 所有统计只算**未删除**素材（垃圾桶不参与）；
- 「未整理」= 不在任何**手动合集**成员的收藏素材——智能合集（``query_json``
  非空）是动态求值，不能算「已整理」；
- 历史事件按事件时间倒序；素材软删除后其事件不返回（物理删除由外键级联清掉）。
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.collection import Collection, CollectionItem
from app.models.inspiration import NOT_DELETED, FavoriteEvent, Inspiration
from app.schemas.inspiration import inspiration_to_out
from app.services.inspiration_query import inspiration_load_options
from app.utils.time import format_utc, utcnow

router = APIRouter(prefix="/api/favorites", tags=["favorites"])

# 「本周收藏」窗口：近 7 天（滚动窗口，不是自然周）
RECENT_WINDOW_DAYS = 7


def _favorite_conditions() -> tuple:
    """收藏素材的基础过滤条件：已收藏 + 未删除。"""
    return (Inspiration.is_favorite.is_(True), NOT_DELETED)


async def _count_favorites(db: AsyncSession, *extra) -> int:
    """收藏素材计数（在基础条件上叠加额外条件）。"""
    result = await db.execute(
        select(func.count(Inspiration.id)).where(*_favorite_conditions(), *extra)
    )
    return int(result.scalar() or 0)


@router.get("/overview")
async def favorites_overview(
    recent_limit: int = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """收藏中心概览：总数 / 近 7 天新增 / 未评分 / 未整理 / 最近收藏。

    - ``unrated_favorites``：评分空或 0 的收藏素材数；
    - ``unfiled_favorites``：不在任何**手动合集**里的收藏素材数（智能合集不算整理）；
    - ``recent``：按 ``favorited_at`` 倒序的最近收藏（``InspirationOut``，最多
      ``recent_limit`` 条；历史无收藏时间的排最后）。
    """
    total = await _count_favorites(db)
    week_ago = utcnow() - timedelta(days=RECENT_WINDOW_DAYS)
    this_week = await _count_favorites(
        db,
        Inspiration.favorited_at.isnot(None),
        Inspiration.favorited_at >= week_ago,
    )
    unrated = await _count_favorites(
        db, or_(Inspiration.rating.is_(None), Inspiration.rating == 0)
    )

    # 手动合集成员子查询（query_json IS NULL 才是手动合集；智能合集的成员是动态求值）
    manual_member = (
        select(CollectionItem.inspiration_id)
        .join(Collection, Collection.id == CollectionItem.collection_id)
        .where(Collection.query_json.is_(None))
    )
    unfiled = await _count_favorites(db, Inspiration.id.not_in(manual_member))

    rows = (
        await db.execute(
            select(Inspiration)
            .options(*inspiration_load_options())
            .where(*_favorite_conditions())
            .order_by(
                case((Inspiration.favorited_at.is_(None), 1), else_=0).asc(),
                Inspiration.favorited_at.desc(),
                Inspiration.id.desc(),
            )
            .limit(recent_limit)
        )
    ).unique().scalars().all()

    return {
        "total_favorites": total,
        "favorites_this_week": this_week,
        "unrated_favorites": unrated,
        "unfiled_favorites": unfiled,
        "recent": [inspiration_to_out(item) for item in rows],
    }


@router.get("/history")
async def favorites_history(
    limit: int = Query(30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """最近收藏/取消事件流水（按事件时间倒序，最多 ``limit`` 条）。

    每条含事件 ``action``（favorite/unfavorite）、``source``（web/batch/api/legacy）、
    ``created_at``，以及精简素材 ``item``（列表页展示所需字段）。
    """
    rows = (
        await db.execute(
            select(FavoriteEvent, Inspiration)
            .join(Inspiration, Inspiration.id == FavoriteEvent.inspiration_id)
            .where(NOT_DELETED)
            .order_by(FavoriteEvent.created_at.desc(), FavoriteEvent.id.desc())
            .limit(limit)
        )
    ).all()
    items = [
        {
            "id": event.id,
            "inspiration_id": event.inspiration_id,
            "action": event.action,
            "source": event.source,
            "created_at": format_utc(event.created_at),
            "item": {
                "id": inspiration.id,
                "file_path": inspiration.file_path,
                "thumbnail_path": inspiration.thumbnail_path,
                "media_type": inspiration.media_type,
                "source_author": inspiration.source_author,
                "is_favorite": inspiration.is_favorite,
                "favorited_at": format_utc(inspiration.favorited_at),
            },
        }
        for event, inspiration in rows
    ]
    return {"items": items, "total": len(items)}
