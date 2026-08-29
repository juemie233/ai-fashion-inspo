"""收藏合集服务：手动合集 CRUD、批量加入/移出、拖拽排序与智能合集动态求值。

两类合集同表区分（见 docs/收藏合集设计方案.md）：
- ``query_json IS NULL``：手动合集，成员关系实体化在 collection_items；
- ``query_json IS NOT NULL``：智能合集，成员由筛选条件动态求值，
  items 表不存它的行，可一键固化（solidify）转手动。

口径约定：
- 智能合集求值复用 ``inspiration_query.build_filters_from_query_json``，
  与素材库列表/搜索同一套筛选逻辑；
- 所有合集内容查询一律排除垃圾桶素材（deleted_at 非空），恢复后自动重现；
- 手动合集内容按 position 升序；智能合集按上传时间倒序（与素材库默认排序一致）。
"""

import json

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.collection import Collection, CollectionItem
from app.models.inspiration import Inspiration, NOT_DELETED
from app.models.person import InspirationBlogger, InspirationModel
from app.models.tag import InspirationTag
from app.services.inspiration_query import build_filters_from_query_json


def _load_query_json(collection: Collection) -> dict:
    """解析智能合集的 query_json，损坏的 JSON 按空条件处理（向前兼容）。"""
    try:
        data = json.loads(collection.query_json or "")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ── 智能合集动态求值 ──


def _smart_base_query(collection: Collection):
    """构建智能合集的动态求值查询（含标签/博主/模特预加载与垃圾桶排除）。"""
    filters = build_filters_from_query_json(_load_query_json(collection))
    return (
        select(Inspiration)
        .options(
            selectinload(Inspiration.tags).selectinload(InspirationTag.tag),
            selectinload(Inspiration.bloggers).selectinload(InspirationBlogger.blogger),
            selectinload(Inspiration.models).selectinload(InspirationModel.model),
        )
        .where(*filters)
    )


async def evaluate_smart_collection(
    db: AsyncSession, collection: Collection, page: int = 1, size: int | None = None
) -> tuple[list[Inspiration], int]:
    """对智能合集动态求值，返回（素材列表, 总数）。

    排序与素材库默认一致（上传时间倒序）；size 为 None 时返回全量
    （固化用），否则分页。
    """
    query = _smart_base_query(collection).order_by(
        Inspiration.created_at.desc(), Inspiration.id.desc()
    )
    total = (
        await db.execute(
            select(func.count()).select_from(query.subquery())
        )
    ).scalar() or 0
    if size is not None:
        query = query.offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    return result.unique().scalars().all(), total


# ── 手动合集成员查询 ──


def _manual_content_query(collection_id: int):
    """构建手动合集内容查询：成员按 position 升序，排除垃圾桶素材。"""
    return (
        select(Inspiration)
        .options(
            selectinload(Inspiration.tags).selectinload(InspirationTag.tag),
            selectinload(Inspiration.bloggers).selectinload(InspirationBlogger.blogger),
            selectinload(Inspiration.models).selectinload(InspirationModel.model),
        )
        .join(CollectionItem, CollectionItem.inspiration_id == Inspiration.id)
        .where(
            CollectionItem.collection_id == collection_id,
            NOT_DELETED,
        )
        .order_by(CollectionItem.position.asc(), CollectionItem.added_at.asc())
    )


# ── 通用查询与校验 ──


async def get_collection(db: AsyncSession, collection_id: int) -> Collection:
    """按 ID 获取合集，不存在抛出 404。"""
    collection = await db.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="合集未找到")
    return collection


async def _require_manual(collection: Collection) -> None:
    """智能合集不支持实体成员操作（加入/移出/排序），返回 400。"""
    if collection.query_json is not None:
        raise HTTPException(
            status_code=400, detail="智能合集不支持该操作，请先固化（solidify）转手动合集"
        )


async def _name_taken(db: AsyncSession, name: str, exclude_id: int | None = None) -> bool:
    """检查合集名是否已存在（重名 409）。"""
    query = select(Collection.id).where(Collection.name == name)
    if exclude_id is not None:
        query = query.where(Collection.id != exclude_id)
    return (await db.execute(query)).scalar() is not None


# ── 序列化 ──


async def collection_to_dict(db: AsyncSession, collection: Collection) -> dict:
    """序列化合集为 API 响应（契约见设计文档「三、API 契约」）。

    - kind: manual / smart；
    - item_count: 手动 = 成员数；智能 = null（懒计算，进入合集页才查询）；
    - 封面：手动指定的封面优先（素材被物理删除时外键自动置空）；未指定时
      手动合集取「加入最早」的未删除成员，智能合集取动态求值结果第一张。
    """
    if collection.query_json is None:
        item_count = (
            await db.execute(
                select(func.count())
                .select_from(CollectionItem)
                .where(CollectionItem.collection_id == collection.id)
            )
        ).scalar() or 0
        query_json = None
    else:
        item_count = None
        query_json = _load_query_json(collection)

    cover_id, cover_thumbnail = await _resolve_cover(db, collection)
    return {
        "id": collection.id,
        "name": collection.name,
        "description": collection.description,
        "kind": collection.kind,
        "position": collection.position,
        "cover_inspiration_id": cover_id,
        "cover_thumbnail_path": cover_thumbnail,
        "item_count": item_count,
        "query_json": query_json,
        "created_at": collection.created_at,
        "updated_at": collection.updated_at,
    }


async def _resolve_cover(
    db: AsyncSession, collection: Collection
) -> tuple[str | None, str | None]:
    """解析合集封面，返回（封面素材 ID, 封面缩略图路径）。"""
    if collection.cover_inspiration_id:
        cover = await db.get(Inspiration, collection.cover_inspiration_id)
        # 封面素材被移入垃圾桶时同样回退（垃圾桶素材不作为合集内容展示）
        if cover and cover.deleted_at is None:
            return cover.id, cover.thumbnail_path
    if collection.query_json is None:
        # 手动合集：取「加入最早」的未删除成员
        row = (
            await db.execute(
                select(Inspiration)
                .join(CollectionItem, CollectionItem.inspiration_id == Inspiration.id)
                .where(
                    CollectionItem.collection_id == collection.id,
                    NOT_DELETED,
                )
                .order_by(CollectionItem.added_at.asc(), CollectionItem.position.asc())
                .limit(1)
            )
        ).scalars().first()
    else:
        # 智能合集：取动态求值结果第一张（limit 1，不做全量求值）
        row = (
            await db.execute(
                _smart_base_query(collection)
                .order_by(Inspiration.created_at.desc(), Inspiration.id.desc())
                .limit(1)
            )
        ).unique().scalars().first()
    if row:
        return row.id, row.thumbnail_path
    return None, None


# ── CRUD ──


async def list_collections(db: AsyncSession) -> list[dict]:
    """合集列表（按 position 升序），逐个序列化。"""
    result = await db.execute(
        select(Collection).order_by(Collection.position.asc(), Collection.id.asc())
    )
    collections = result.scalars().all()
    return [await collection_to_dict(db, c) for c in collections]


async def create_collection(
    db: AsyncSession,
    name: str,
    description: str | None = None,
    query_json: dict | None = None,
) -> dict:
    """创建合集；query_json 非空时为智能合集。重名返回 409。"""
    if await _name_taken(db, name):
        raise HTTPException(status_code=409, detail=f"合集名称「{name}」已存在")
    # position 追加到列表末尾（越大越靠后）
    max_position = (
        await db.execute(select(func.max(Collection.position)))
    ).scalar()
    collection = Collection(
        name=name,
        description=description,
        position=(max_position or 0) + 1,
        query_json=json.dumps(query_json, ensure_ascii=False) if query_json else None,
    )
    db.add(collection)
    await db.commit()
    await db.refresh(collection)
    return await collection_to_dict(db, collection)


async def update_collection(
    db: AsyncSession,
    collection_id: int,
    name: str | None = None,
    description: str | None = None,
    cover_inspiration_id: str | None = None,
    query_json: dict | None = None,
    clear_cover: bool = False,
) -> dict:
    """更新合集基础信息（部分更新，仅更新显式传入的字段）。

    - 重名返回 409；
    - clear_cover=True 表示显式传 cover_inspiration_id=null，清空手动封面
      （回退自动封面）；
    - query_json 仅智能合集可更新（「更新为当前筛选条件」），手动合集 400。
    """
    collection = await get_collection(db, collection_id)

    if name is not None and name != collection.name:
        if await _name_taken(db, name, exclude_id=collection.id):
            raise HTTPException(status_code=409, detail=f"合集名称「{name}」已存在")
        collection.name = name
    if description is not None:
        collection.description = description
    if clear_cover:
        collection.cover_inspiration_id = None
    elif cover_inspiration_id is not None:
        cover = await db.get(Inspiration, cover_inspiration_id)
        if not cover:
            raise HTTPException(status_code=404, detail="封面素材未找到")
        collection.cover_inspiration_id = cover_inspiration_id

    # update_inspiration 接口用 sentinel 区分「未传」与「显式置空」，
    # 这里由路由层约定：query_json 仅在显式提供时更新（见路由模块）
    if query_json is not None:
        if collection.query_json is None:
            raise HTTPException(
                status_code=400, detail="手动合集不支持更新筛选条件"
            )
        collection.query_json = json.dumps(query_json, ensure_ascii=False)

    await db.commit()
    await db.refresh(collection)
    return await collection_to_dict(db, collection)


async def delete_collection(db: AsyncSession, collection_id: int) -> None:
    """删除合集（手动合集级联删成员关系、智能合集仅删自身，素材均无损）。"""
    collection = await get_collection(db, collection_id)
    await db.delete(collection)
    await db.commit()


# ── 成员批量加入 / 移出 ──


async def add_inspirations(
    db: AsyncSession, collection_id: int, inspiration_ids: list[str]
) -> dict:
    """批量加入素材到手动合集（请求内去重、跳过已加入与不存在的素材）。

    智能合集返回 400；新成员 append 到末尾（position = 当前最大值 + 1）。
    """
    collection = await get_collection(db, collection_id)
    await _require_manual(collection)

    # 请求内去重，保持首次出现顺序
    seen: set[str] = set()
    ordered_ids = []
    for insp_id in inspiration_ids:
        if insp_id not in seen:
            seen.add(insp_id)
            ordered_ids.append(insp_id)

    # 过滤不存在的素材
    existing = (
        await db.execute(select(Inspiration.id).where(Inspiration.id.in_(ordered_ids)))
    ).scalars().all()
    not_found = len(ordered_ids) - len(existing)

    # 过滤已加入的成员
    existing_members = set(
        (
            await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == collection.id,
                    CollectionItem.inspiration_id.in_(existing),
                )
            )
        ).scalars().all()
    )
    to_add = [i for i in existing if i not in existing_members]

    max_position = (
        await db.execute(
            select(func.max(CollectionItem.position)).where(
                CollectionItem.collection_id == collection.id
            )
        )
    ).scalar() or 0
    for offset, insp_id in enumerate(to_add, start=1):
        db.add(
            CollectionItem(
                collection_id=collection.id,
                inspiration_id=insp_id,
                position=max_position + offset,
            )
        )
    await db.commit()
    return {
        "added": len(to_add),
        "skipped": len(existing_members),
        "not_found": not_found,
    }


async def remove_inspirations(
    db: AsyncSession, collection_id: int, inspiration_ids: list[str]
) -> dict:
    """批量移出素材（不在合集内的 ID 静默跳过）。智能合集返回 400。"""
    collection = await get_collection(db, collection_id)
    await _require_manual(collection)

    result = await db.execute(
        delete(CollectionItem).where(
            CollectionItem.collection_id == collection.id,
            CollectionItem.inspiration_id.in_(inspiration_ids),
        )
    )
    await db.commit()
    return {"removed": result.rowcount or 0}


# ── 排序 ──


async def reorder_collection_items(
    db: AsyncSession, collection_id: int, ordered_ids: list[str]
) -> dict:
    """提交合集内成员展示顺序（拖拽编排）。

    ordered_ids 按新顺序重排 position（0 起连续编号）；集合内已去重；
    未出现在 ordered_ids 中的成员保持原有相对顺序追加到末尾。
    """
    collection = await get_collection(db, collection_id)
    await _require_manual(collection)

    result = await db.execute(
        select(CollectionItem).where(CollectionItem.collection_id == collection.id)
    )
    items = result.scalars().all()
    member_map = {item.inspiration_id: item for item in items}

    # 去重并校验：包含不属于该合集的素材时返回 400
    seen: set[str] = set()
    unique_ids = []
    for insp_id in ordered_ids:
        if insp_id in seen:
            continue
        if insp_id not in member_map:
            raise HTTPException(
                status_code=400, detail="排序列表包含不属于该合集的素材"
            )
        seen.add(insp_id)
        unique_ids.append(insp_id)

    # 未提交的成员按当前 position 追加到末尾
    rest = [
        item for item in sorted(items, key=lambda i: i.position)
        if item.inspiration_id not in seen
    ]
    for position, insp_id in enumerate(
        unique_ids + [item.inspiration_id for item in rest]
    ):
        member_map[insp_id].position = position
    await db.commit()
    return {"updated": len(unique_ids) + len(rest)}


async def reorder_collections(db: AsyncSession, ordered_ids: list[int]) -> dict:
    """提交合集列表顺序（拖拽排序）：按 ordered_ids 顺序重排 position。

    未出现在 ordered_ids 中的合集保持原有相对顺序追加到末尾；
    包含不存在的合集 ID 时返回 404。
    """
    collections = (
        (await db.execute(select(Collection))).scalars().all()
    )
    collection_map = {c.id: c for c in collections}

    unique_ids: list[int] = []
    seen: set[int] = set()
    for cid in ordered_ids:
        if cid in seen:
            continue
        if cid not in collection_map:
            raise HTTPException(status_code=404, detail="合集未找到")
        seen.add(cid)
        unique_ids.append(cid)

    rest = [
        c for c in sorted(collections, key=lambda c: (c.position, c.id))
        if c.id not in seen
    ]
    ordered = unique_ids + [c.id for c in rest]
    for position, cid in enumerate(ordered):
        collection_map[cid].position = position
    await db.commit()
    return {"updated": len(ordered)}


# ── 合集内容查询 ──


async def list_collection_inspirations(
    db: AsyncSession, collection_id: int, page: int = 1, size: int = 50
) -> tuple[list[Inspiration], int]:
    """查询合集内容（分页）：手动按 position 升序；智能按 query_json 动态求值。

    均排除垃圾桶素材；智能合集求值复用素材库筛选逻辑（口径一致）。
    """
    collection = await get_collection(db, collection_id)
    if collection.query_json is None:
        query = _manual_content_query(collection.id)
        total = (
            await db.execute(
                select(func.count()).select_from(query.subquery())
            )
        ).scalar() or 0
        result = await db.execute(query.offset((page - 1) * size).limit(size))
        return result.unique().scalars().all(), total
    return await evaluate_smart_collection(db, collection, page=page, size=size)


# ── 固化（智能 → 手动）──


async def solidify_collection(db: AsyncSession, collection_id: int) -> dict:
    """智能合集固化：把当前匹配素材按当前位置写入 collection_items 并清空 query_json。

    固化后可手动增删与拖拽排序；手动合集调用返回 400。
    """
    collection = await get_collection(db, collection_id)
    if collection.query_json is None:
        raise HTTPException(status_code=400, detail="手动合集无需固化")

    inspirations, _total = await evaluate_smart_collection(db, collection)

    # 覆盖式写入：清掉历史成员行（正常为空），按动态求值顺序连续编号
    await db.execute(
        delete(CollectionItem).where(CollectionItem.collection_id == collection.id)
    )
    for position, inspiration in enumerate(inspirations):
        db.add(
            CollectionItem(
                collection_id=collection.id,
                inspiration_id=inspiration.id,
                position=position,
            )
        )
    collection.query_json = None
    await db.commit()
    await db.refresh(collection)
    return await collection_to_dict(db, collection)
