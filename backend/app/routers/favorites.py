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

import logging
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.collection import Collection, CollectionItem
from app.models.inspiration import NOT_DELETED, FavoriteEvent, Inspiration
from app.schemas.inspiration import inspiration_to_out
from app.services.inspiration_query import inspiration_load_options
from app.utils.time import format_utc, utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/favorites", tags=["favorites"])

# 「本周收藏」窗口：近 7 天（滚动窗口，不是自然周）
RECENT_WINDOW_DAYS = 7
# 「久未回顾」窗口：收藏超过 90 天（或历史收藏无 favorited_at）视为 stale
STALE_WINDOW_DAYS = 90
# 收藏智能视图（P1，2026-11-19）：key → (中文名, 说明)
FAVORITE_VIEWS: dict[str, tuple[str, str]] = {
    "unrated": ("未评分", "收藏了但还没打分的素材（rating 空或 0）"),
    "unfiled": ("未整理", "还没放进任何手动合集的收藏（智能合集不算整理）"),
    "high_rating": ("高分收藏", "评分 4 分及以上的收藏"),
    "stale": ("久未回顾", f"收藏超过 {STALE_WINDOW_DAYS} 天（历史无收藏时间的也计入）"),
}
# 整理工作台列表上限：首页只展示「最近/最严重的 N 条/组」，完整计数照常返回
CLEANUP_ITEM_LIMIT = 20
CLEANUP_GROUP_LIMIT = 20


def _favorite_conditions() -> tuple:
    """收藏素材的基础过滤条件：已收藏 + 未删除。"""
    return (Inspiration.is_favorite.is_(True), NOT_DELETED)


def _manual_membership_subquery():
    """手动合集成员子查询（query_json IS NULL 才是手动合集；智能合集动态求值不算）。"""
    return (
        select(CollectionItem.inspiration_id)
        .join(Collection, Collection.id == CollectionItem.collection_id)
        .where(Collection.query_json.is_(None))
    )


def _view_extra_conditions(key: str) -> list:
    """收藏智能视图的附加过滤条件（不含基础收藏条件）。"""
    if key == "unrated":
        return [or_(Inspiration.rating.is_(None), Inspiration.rating == 0)]
    if key == "unfiled":
        return [Inspiration.id.not_in(_manual_membership_subquery())]
    if key == "high_rating":
        return [Inspiration.rating >= 4]
    if key == "stale":
        cutoff = utcnow() - timedelta(days=STALE_WINDOW_DAYS)
        return [
            or_(
                Inspiration.favorited_at.is_(None),
                Inspiration.favorited_at <= cutoff,
            )
        ]
    raise HTTPException(status_code=404, detail=f"未知收藏视图：{key}")


async def _count_favorites(db: AsyncSession, *extra) -> int:
    """收藏素材计数（在基础条件上叠加额外条件）。"""
    result = await db.execute(
        select(func.count(Inspiration.id)).where(*_favorite_conditions(), *extra)
    )
    return int(result.scalar() or 0)


def _safe_storage_path(relative_path: str | None) -> Path | None:
    """把素材的相对路径安全解析到 storage_root 内；越界/非法返回 None。

    安全点（整理工作台要按「文件是否存在」统计，不能信任 DB 里的历史路径）：
    - 空值 → None；
    - 绝对路径 / ``..`` 越界 / 解析异常 → None（视为不可验证 = 缺失）；
    - 解析后必须仍在 ``settings.storage_root`` 下。
    """
    if not relative_path:
        return None
    try:
        root = settings.storage_root.resolve()
        candidate = (root / relative_path).resolve()
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug("收藏文件路径无法安全解析，按缺失处理：%r（%s）", relative_path, exc)
        return None
    return candidate


def _storage_file_exists(relative_path: str | None) -> bool:
    """素材原文件是否存在于 storage_root 下（越界/空路径按不存在处理）。"""
    path = _safe_storage_path(relative_path)
    return bool(path is not None and path.exists())


async def _load_out_items(db: AsyncSession, inspiration_ids: list[str]) -> list:
    """按给定 ID 顺序加载并序列化素材（缺关联预加载会 500，统一走 load_options）。"""
    if not inspiration_ids:
        return []
    rows = (
        await db.execute(
            select(Inspiration)
            .options(*inspiration_load_options())
            .where(Inspiration.id.in_(inspiration_ids))
        )
    ).unique().scalars().all()
    by_id = {item.id: item for item in rows}
    return [
        inspiration_to_out(by_id[insp_id])
        for insp_id in inspiration_ids
        if insp_id in by_id
    ]


@router.get("/views")
async def favorites_views(db: AsyncSession = Depends(get_db)) -> dict:
    """收藏智能视图列表（四个固定视图 + 实时计数）。

    视图口径（均只算未删除收藏）：

    - ``unrated``：评分空 / 0（还没打分）；
    - ``unfiled``：不在任何手动合集的收藏（智能合集动态求值，不算整理）；
    - ``high_rating``：评分 ≥ 4；
    - ``stale``：收藏超过 90 天，或历史收藏无 ``favorited_at``。
    """
    views = []
    for key, (label, description) in FAVORITE_VIEWS.items():
        count = await _count_favorites(db, *_view_extra_conditions(key))
        views.append(
            {
                "key": key,
                "label": label,
                "description": description,
                "count": count,
            }
        )
    return {"views": views}


@router.get("/views/{key}")
async def favorites_view_items(
    key: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页获取某个收藏智能视图的素材（按收藏时间倒序、id 兜底）。

    未知 ``key`` 返回 404；排序与收藏中心「最近收藏」一致（``favorited_at DESC``，
    历史无收藏时间的排最后）。
    """
    if key not in FAVORITE_VIEWS:
        raise HTTPException(status_code=404, detail=f"未知收藏视图：{key}")
    extra = _view_extra_conditions(key)
    total = await _count_favorites(db, *extra)
    rows = (
        await db.execute(
            select(Inspiration)
            .options(*inspiration_load_options())
            .where(*_favorite_conditions(), *extra)
            .order_by(
                Inspiration.favorited_at.desc().nulls_last(),
                Inspiration.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).unique().scalars().all()
    return {
        "key": key,
        "items": [inspiration_to_out(item) for item in rows],
        "total": total,
        "page": page,
        "size": page_size,
    }


@router.get("/cleanup")
async def favorites_cleanup(db: AsyncSession = Depends(get_db)) -> dict:
    """收藏整理工作台数据（P1，2026-11-19）。

    返回：
    - ``counts``：``unrated`` / ``unfiled`` / ``missing_files`` / ``duplicate_groups`` /
      ``duplicate_items``（后两者为「总数」，不受展示上限影响）；
    - ``missing_files``：收藏中**源文件在 storage_root 下不存在**的素材（最多 20 条，
      按收藏时间倒序；越界/非法路径一律按缺失处理）；
    - ``duplicate_groups``：收藏中 ``content_hash`` 相同的组（组内 >1，最多 20 组，
      按组内条数降序、hash 升序；组内按收藏时间倒序）。

    只统计未删除收藏；``duplicate_items`` 为所有重复组内的素材总数（不是「多出来的数量」）。
    """
    unrated = await _count_favorites(db, *_view_extra_conditions("unrated"))
    unfiled = await _count_favorites(db, *_view_extra_conditions("unfiled"))

    # 1) 缺失文件：一次取收藏的 (id, file_path)，逐条做安全路径解析 + exists
    favorite_rows = (
        await db.execute(
            select(Inspiration.id, Inspiration.file_path)
            .where(*_favorite_conditions())
            .order_by(
                Inspiration.favorited_at.desc().nulls_last(),
                Inspiration.id.desc(),
            )
        )
    ).all()
    missing_ids = [
        insp_id
        for insp_id, file_path in favorite_rows
        if not _storage_file_exists(file_path)
    ]

    # 2) 重复组：按 content_hash 聚合（组内 >1），先拿总数与展示组
    duplicate_count_rows = (
        await db.execute(
            select(
                Inspiration.content_hash,
                func.count(Inspiration.id).label("cnt"),
            )
            .where(*_favorite_conditions(), Inspiration.content_hash.isnot(None))
            .group_by(Inspiration.content_hash)
            .having(func.count(Inspiration.id) > 1)
            .order_by(
                func.count(Inspiration.id).desc(),
                Inspiration.content_hash.asc(),
            )
        )
    ).all()
    duplicate_groups_total = len(duplicate_count_rows)
    duplicate_items_total = sum(int(row.cnt) for row in duplicate_count_rows)
    top_hashes = [row.content_hash for row in duplicate_count_rows[:CLEANUP_GROUP_LIMIT]]

    duplicate_items_by_hash: dict[str, list] = {hash_: [] for hash_ in top_hashes}
    if top_hashes:
        duplicate_rows = (
            await db.execute(
                select(Inspiration)
                .options(*inspiration_load_options())
                .where(
                    *_favorite_conditions(),
                    Inspiration.content_hash.in_(top_hashes),
                )
                .order_by(
                    Inspiration.favorited_at.desc().nulls_last(),
                    Inspiration.id.desc(),
                )
            )
        ).unique().scalars().all()
        for item in duplicate_rows:
            if item.content_hash in duplicate_items_by_hash:
                duplicate_items_by_hash[item.content_hash].append(
                    inspiration_to_out(item)
                )

    return {
        "counts": {
            "unrated": unrated,
            "unfiled": unfiled,
            "missing_files": len(missing_ids),
            "duplicate_groups": duplicate_groups_total,
            "duplicate_items": duplicate_items_total,
        },
        "missing_files": await _load_out_items(
            db, missing_ids[:CLEANUP_ITEM_LIMIT]
        ),
        "duplicate_groups": [
            {
                "content_hash": hash_,
                "items": duplicate_items_by_hash[hash_],
            }
            for hash_ in top_hashes
        ],
    }


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
    unfiled = await _count_favorites(
        db, Inspiration.id.not_in(_manual_membership_subquery())
    )

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
