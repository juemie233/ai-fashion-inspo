"""标签管理的 REST API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select, delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.inspiration import Inspiration
from app.models.tag import Tag, InspirationTag, TagAlias
from app.schemas.tag import (
    AliasCreate,
    AliasOut,
    TagBatchDelete,
    TagCreate,
    TagCategoryGroup,
    TagImportRequest,
    TagMergeRequest,
    TagOut,
    TagReorderRequest,
    TagUpdate,
)
from app.services.tag_service import (
    find_similar_tags,
    get_all_tags_grouped,
    merge_tags,
)

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("")
async def list_tags(db: AsyncSession = Depends(get_db)):
    """获取所有标签，按类别分组。"""
    grouped = await get_all_tags_grouped(db)
    return [
        TagCategoryGroup(category=cat, tags=tags)
        for cat, tags in grouped.items()
    ]


@router.post("", response_model=TagOut, status_code=status.HTTP_201_CREATED)
async def create_tag(data: TagCreate, db: AsyncSession = Depends(get_db)):
    """手动创建自定义标签。"""
    # 先做别名归一化（DB 别名 → 硬编码同义词），使「纯白」自动落到既有规范名「白色」
    from app.utils.tag_normalizer import normalize_tag_name_async

    name = (await normalize_tag_name_async(db, data.name)).strip()

    # 检查标签是否已存在（用归一化后的规范名查重）
    result = await db.execute(select(Tag).where(Tag.name == name))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"标签 '{data.name}' 已存在",
        )

    tag = Tag(name=name, category=data.category, source="manual")
    db.add(tag)
    await db.flush()
    await db.refresh(tag)

    return TagOut(
        id=tag.id,
        name=tag.name,
        category=tag.category,
        created_at=tag.created_at,
        usage_count=0,
    )


@router.patch("/{tag_id}", response_model=TagOut)
async def update_tag(tag_id: int, data: TagUpdate, db: AsyncSession = Depends(get_db)):
    """更新标签的名称、类别、置顶、排序或备注。"""
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="标签未找到")

    if data.name is not None:
        # 先做别名归一化，保证改名后仍命中规范名（用户自定义的 DB 别名优先）
        from app.utils.tag_normalizer import normalize_tag_name_async

        name = (await normalize_tag_name_async(db, data.name)).strip()

        # 检查新名称是否与其它主标签冲突
        result = await db.execute(
            select(Tag).where(Tag.name == name, Tag.id != tag_id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=409, detail=f"标签 '{data.name}' 已存在"
            )
        # 检查新名称是否已是其它标签的别名（避免改名后产生歧义）
        alias_conflict = await db.execute(
            select(TagAlias).where(TagAlias.alias == name)
        )
        if alias_conflict.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"标签名 '{data.name}' 已作为其它标签的别名使用",
            )
        tag.name = name

    if data.category is not None:
        tag.category = data.category

    if data.pinned is not None:
        tag.pinned = data.pinned

    if data.sort_order is not None:
        tag.sort_order = data.sort_order

    if data.description is not None:
        tag.description = data.description

    await db.flush()
    await db.refresh(tag)
    return TagOut(
        id=tag.id,
        name=tag.name,
        category=tag.category,
        pinned=tag.pinned,
        sort_order=tag.sort_order,
        description=tag.description,
        created_at=tag.created_at,
        usage_count=0,
    )


@router.delete("/unused", status_code=status.HTTP_200_OK)
async def delete_unused_tags(db: AsyncSession = Depends(get_db)):
    """删除所有使用次数为 0 的标签。"""
    import logging
    _logger = logging.getLogger(__name__)

    used_subquery = select(InspirationTag.tag_id).distinct()
    result = await db.execute(
        select(Tag).where(Tag.id.notin_(used_subquery))
    )
    unused = result.scalars().all()

    if not unused:
        return {"message": "没有未使用的标签", "count": 0}

    # 先删关联表中的残留记录（防御性清理），再删标签
    unused_ids = [t.id for t in unused]
    await db.execute(
        delete(InspirationTag).where(InspirationTag.tag_id.in_(unused_ids))
    )
    await db.execute(
        delete(Tag).where(Tag.id.in_(unused_ids))
    )
    await db.commit()

    _logger.info(f"已删除 {len(unused)} 个未使用标签: {[t.name for t in unused[:10]]}...")
    return {"message": f"已删除 {len(unused_ids)} 个未使用标签", "count": len(unused_ids)}


@router.post("/batch-delete", status_code=status.HTTP_200_OK)
async def batch_delete_tags(
    data: TagBatchDelete, db: AsyncSession = Depends(get_db)
):
    """批量删除标签及其所有关联。"""
    if not data.tag_ids:
        raise HTTPException(status_code=400, detail="请提供要删除的标签 ID 列表")

    tags_result = await db.execute(
        select(Tag).where(Tag.id.in_(data.tag_ids))
    )
    tags = tags_result.scalars().all()
    if not tags:
        raise HTTPException(status_code=404, detail="未找到任何标签")

    for tag in tags:
        await db.delete(tag)
    await db.flush()

    return {"message": f"已删除 {len(tags)} 个标签", "count": len(tags)}


@router.post("/merge", status_code=status.HTTP_200_OK)
async def merge_tags_endpoint(data: TagMergeRequest, db: AsyncSession = Depends(get_db)):
    """将源标签合并到目标标签，删除源标签。"""
    if data.source_tag_id == data.target_tag_id:
        raise HTTPException(status_code=400, detail="不能将标签合并到自身")

    source = await db.get(Tag, data.source_tag_id)
    target = await db.get(Tag, data.target_tag_id)
    if not source:
        raise HTTPException(
            status_code=404, detail=f"源标签 {data.source_tag_id} 未找到"
        )
    if not target:
        raise HTTPException(
            status_code=404, detail=f"目标标签 {data.target_tag_id} 未找到"
        )

    await merge_tags(db, data.source_tag_id, data.target_tag_id)
    return {"message": f"已将 '{source.name}' 合并到 '{target.name}'"}


@router.get("/suggestions/{name}")
async def tag_suggestions(name: str, db: AsyncSession = Depends(get_db)):
    """查找与给定名称相似的已有标签（用于去重建议）。"""
    similar = await find_similar_tags(db, name)
    return [
        {"id": t.id, "name": t.name, "category": t.category}
        for t in similar
    ]


# ============ 批量编辑 ============


@router.patch("/batch-category", status_code=status.HTTP_200_OK)
async def batch_change_category(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """批量修改标签类别。请求体: {"tag_ids": [1,2,3], "category": "style"}"""
    tag_ids = payload.get("tag_ids", [])
    category = payload.get("category", "").strip()
    if not tag_ids or not category:
        raise HTTPException(status_code=400, detail="请提供 tag_ids 和 category")
    result = await db.execute(
        update(Tag).where(Tag.id.in_(tag_ids)).values(category=category)
    )
    await db.commit()
    return {"updated": result.rowcount, "category": category}


@router.patch("/batch-rename", status_code=status.HTTP_200_OK)
async def batch_rename_tags(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """批量重命名标签（查找替换）。请求体: {"tag_ids": [1,2], "find": "白色", "replace": "纯白"}"""
    tag_ids = payload.get("tag_ids", [])
    find_str = payload.get("find", "")
    replace_str = payload.get("replace", "")
    if not tag_ids or not find_str:
        raise HTTPException(status_code=400, detail="请提供 tag_ids 和 find 参数")

    result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids)))
    tags = result.scalars().all()
    # 预检：新名称是否会与已有标签冲突
    for tag in tags:
        if find_str in tag.name:
            new_name = tag.name.replace(find_str, replace_str)
            if new_name != tag.name:
                conflict = await db.execute(
                    select(Tag.id).where(Tag.name == new_name, Tag.id != tag.id)
                )
                if conflict.scalar_one_or_none():
                    raise HTTPException(
                        status_code=409,
                        detail=f"重命名冲突: '{tag.name}' → '{new_name}' 与已有标签同名",
                    )
    updated = 0
    for tag in tags:
        if find_str in tag.name:
            tag.name = tag.name.replace(find_str, replace_str)
            updated += 1
    await db.commit()
    return {"updated": updated, "find": find_str, "replace": replace_str}


# ============ 统计与扫描 ============


@router.get("/stats")
async def tag_stats(db: AsyncSession = Depends(get_db)):
    """获取标签统计数据。"""
    # 总数
    total_result = await db.execute(select(func.count()).select_from(Tag))
    total = total_result.scalar() or 0

    # 按来源统计
    source_result = await db.execute(
        select(Tag.source, func.count()).group_by(Tag.source)
    )
    by_source = {row[0]: row[1] for row in source_result}

    # 按类别统计
    cat_result = await db.execute(
        select(Tag.category, func.count()).group_by(Tag.category).order_by(func.count().desc())
    )
    by_category = {row[0]: row[1] for row in cat_result}

    # 未使用标签数
    used_subquery = select(InspirationTag.tag_id).distinct()
    unused_result = await db.execute(
        select(func.count()).select_from(Tag).where(Tag.id.notin_(used_subquery))
    )
    unused = unused_result.scalar() or 0

    # 总关联数
    link_result = await db.execute(select(func.count()).select_from(InspirationTag))
    total_links = link_result.scalar() or 0

    return {
        "total": total,
        "unused": unused,
        "total_links": total_links,
        "by_source": by_source,
        "by_category": by_category,
    }


@router.get("/duplicates")
async def find_duplicate_tags(
    threshold: float = 0.75, db: AsyncSession = Depends(get_db)
):
    """扫描所有标签，找出名称相似度 >= threshold 的标签对。"""
    result = await db.execute(select(Tag).order_by(Tag.name))
    all_tags = result.scalars().all()

    from app.services.tag_service import _similarity

    # O(n²) 相似度计算在 threadpool 中执行，避免阻塞事件循环
    def _compute_pairs():
        pairs = []
        for i in range(len(all_tags)):
            for j in range(i + 1, len(all_tags)):
                sim = _similarity(all_tags[i].name, all_tags[j].name)
                if sim >= threshold and sim < 1.0:
                    pairs.append({
                        "tag_a": {
                            "id": all_tags[i].id,
                            "name": all_tags[i].name,
                            "category": all_tags[i].category,
                        },
                        "tag_b": {
                            "id": all_tags[j].id,
                            "name": all_tags[j].name,
                            "category": all_tags[j].category,
                        },
                        "similarity": round(sim, 2),
                    })
        pairs.sort(key=lambda p: p["similarity"], reverse=True)
        return pairs

    pairs = await run_in_threadpool(_compute_pairs)
    return {"duplicates": pairs[:50], "total": len(pairs)}


# ============ 标签详情 ============


@router.post("/{tag_id}/inspirations/batch-remove", status_code=status.HTTP_200_OK)
async def batch_remove_tag_inspirations(
    tag_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """批量解除标签与多个素材的关联。

    请求体: {"inspiration_ids": ["uuid1", "uuid2", ...]}
    """
    inspiration_ids = payload.get("inspiration_ids", [])
    if not isinstance(inspiration_ids, list) or not inspiration_ids:
        raise HTTPException(status_code=400, detail="请提供素材 ID 列表")

    result = await db.execute(
        delete(InspirationTag).where(
            InspirationTag.tag_id == tag_id,
            InspirationTag.inspiration_id.in_(inspiration_ids),
        )
    )
    await db.commit()
    return {"removed": result.rowcount}


@router.get("/{tag_id}/inspirations")
async def tag_inspirations(
    tag_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    sort: str = "newest",  # newest | oldest | confidence
    db: AsyncSession = Depends(get_db),
):
    """获取使用指定标签的素材列表。"""
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="标签未找到")

    # 统计总数
    count_result = await db.execute(
        select(func.count()).where(InspirationTag.tag_id == tag_id)
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
        .where(InspirationTag.tag_id == tag_id)
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


# ============ 导入/导出 ============


@router.get("/export")
async def export_tags(db: AsyncSession = Depends(get_db)):
    """导出所有标签为 JSON（含类别、来源、使用次数）。"""
    grouped = await get_all_tags_grouped(db)
    export_data = []
    for category, tags in grouped.items():
        for t in tags:
            export_data.append({
                "name": t["name"],
                "category": t["category"],
                "source": t.get("source", "seed"),
                "usage_count": t["usage_count"],
            })
    from datetime import datetime, timezone
    return {"tags": export_data, "exported_at": datetime.now(timezone.utc).isoformat()}


@router.post("/import", status_code=status.HTTP_200_OK)
async def import_tags(
    data: TagImportRequest, db: AsyncSession = Depends(get_db)
):
    """批量导入标签（跳过已存在的标签）。"""
    imported = 0
    skipped = 0
    for item in data.tags:
        existing = await db.execute(
            select(Tag).where(Tag.name == item.name.strip())
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        tag = Tag(
            name=item.name.strip(),
            category=item.category,
            source="manual",
        )
        db.add(tag)
        imported += 1

    await db.flush()
    return {
        "message": f"已导入 {imported} 个标签，跳过 {skipped} 个已存在",
        "imported": imported,
        "skipped": skipped,
    }


# ============ 自定义排序 ============


@router.post("/reorder", status_code=status.HTTP_200_OK)
async def reorder_tags(
    data: TagReorderRequest, db: AsyncSession = Depends(get_db)
):
    """批量更新标签自定义排序权重（sort_order 越小越靠前）。"""
    if not data.items:
        raise HTTPException(status_code=400, detail="请提供排序项")

    order_map = {item.id: item.sort_order for item in data.items}
    result = await db.execute(select(Tag).where(Tag.id.in_(order_map.keys())))
    tags = result.scalars().all()
    if len(tags) != len(order_map):
        found_ids = {t.id for t in tags}
        missing_ids = [i for i in order_map if i not in found_ids]
        raise HTTPException(
            status_code=404,
            detail=f"以下标签不存在，无法排序: {missing_ids}",
        )
    for tag in tags:
        tag.sort_order = order_map[tag.id]
    await db.commit()
    return {"updated": len(tags)}


# ============ 别名管理 ============


@router.get("/aliases", status_code=status.HTTP_200_OK)
async def list_aliases(db: AsyncSession = Depends(get_db)):
    """获取所有标签别名（含所属标签名）。"""
    result = await db.execute(
        select(TagAlias.id, TagAlias.tag_id, TagAlias.alias, Tag.name)
        .join(Tag, Tag.id == TagAlias.tag_id)
        .order_by(Tag.name, TagAlias.alias)
    )
    return [
        {"id": r[0], "tag_id": r[1], "alias": r[2], "tag_name": r[3]}
        for r in result.all()
    ]


@router.post("/{tag_id}/aliases", response_model=AliasOut, status_code=status.HTTP_201_CREATED)
async def create_alias(
    tag_id: int, data: AliasCreate, db: AsyncSession = Depends(get_db)
):
    """为标签添加别名（将别名归一化到该标签）。"""
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="标签未找到")

    alias = data.alias.strip()
    if not alias:
        raise HTTPException(status_code=400, detail="别名为空")

    # 别名不能与任何主标签同名（否则产生歧义）
    existing_tag = await db.execute(select(Tag.id).where(Tag.name == alias))
    if existing_tag.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"别名 '{alias}' 与已有标签同名")

    existing_alias = await db.execute(
        select(TagAlias).where(TagAlias.alias == alias)
    )
    if existing_alias.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"别名 '{alias}' 已存在")

    obj = TagAlias(tag_id=tag_id, alias=alias)
    db.add(obj)
    try:
        # 用 SAVEPOINT 隔离插入：并发创建同名字别名时，后者触发 IntegrityError，
        # 回滚后重查并返回已存在的别名，避免 500。
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        db.expunge(obj)
        existing = await db.execute(
            select(TagAlias).where(TagAlias.alias == alias)
        )
        existing_obj = existing.scalar_one_or_none()
        if existing_obj:
            return existing_obj
        raise HTTPException(status_code=409, detail=f"别名 '{alias}' 已存在")
    await db.refresh(obj)
    return obj


@router.delete("/aliases/{alias_id}", status_code=status.HTTP_200_OK)
async def delete_alias(alias_id: int, db: AsyncSession = Depends(get_db)):
    """删除标签别名。"""
    obj = await db.get(TagAlias, alias_id)
    if not obj:
        raise HTTPException(status_code=404, detail="别名未找到")
    await db.delete(obj)
    await db.commit()
    return {"message": "已删除别名"}


# ============ 共现网络与使用趋势 ============


@router.get("/cooccurrence-network", status_code=status.HTTP_200_OK)
async def cooccurrence_network(
    limit: int = Query(30, ge=2, le=100),
    min_count: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """返回使用次数 top-N 标签之间的共现网络（节点 + 加权边）。"""
    import itertools
    from collections import defaultdict

    # 取使用次数最多的 top-N 标签作为网络节点
    top_result = await db.execute(
        select(
            Tag.id,
            Tag.name,
            Tag.category,
            func.count(InspirationTag.inspiration_id).label("cnt"),
        )
        .join(InspirationTag, Tag.id == InspirationTag.tag_id)
        .group_by(Tag.id)
        .order_by(func.count(InspirationTag.inspiration_id).desc())
        .limit(limit)
    )
    tags = [(r[0], r[1], r[2], r[3]) for r in top_result.all()]
    tag_ids = [t[0] for t in tags]

    if not tag_ids:
        return {"nodes": [], "edges": []}

    # 一次性查出这些标签的所有素材关联，在内存中统计共现
    links_result = await db.execute(
        select(InspirationTag.inspiration_id, InspirationTag.tag_id).where(
            InspirationTag.tag_id.in_(tag_ids)
        )
    )
    insp_map: dict[str, set[int]] = defaultdict(set)
    for insp_id, tag_id in links_result.all():
        insp_map[insp_id].add(tag_id)

    pair_count: dict[tuple[int, int], int] = defaultdict(int)
    for tag_set in insp_map.values():
        for a, b in itertools.combinations(sorted(tag_set), 2):
            pair_count[(a, b)] += 1

    nodes = [
        {"id": t[0], "name": t[1], "category": t[2], "usage_count": t[3]}
        for t in tags
    ]
    edges = [
        {"source": a, "target": b, "weight": w}
        for (a, b), w in pair_count.items()
        if w >= min_count
    ]
    return {"nodes": nodes, "edges": edges}


@router.get("/top", status_code=status.HTTP_200_OK)
async def top_tags(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """返回使用次数最多的标签排行。"""
    result = await db.execute(
        select(
            Tag.id,
            Tag.name,
            Tag.category,
            func.count(InspirationTag.inspiration_id).label("cnt"),
        )
        .join(InspirationTag, Tag.id == InspirationTag.tag_id)
        .group_by(Tag.id)
        .order_by(func.count(InspirationTag.inspiration_id).desc())
        .limit(limit)
    )
    return [
        {"id": r[0], "name": r[1], "category": r[2], "usage_count": r[3]}
        for r in result.all()
    ]


@router.get("/{tag_id}/trend", status_code=status.HTTP_200_OK)
async def tag_trend(
    tag_id: int,
    granularity: str = Query("month", pattern="^(month|week|day)$"),
    db: AsyncSession = Depends(get_db),
):
    """获取标签的使用趋势（按素材创建时间分桶统计）。"""
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="标签未找到")

    fmt = {"month": "%Y-%m", "week": "%Y-W%W", "day": "%Y-%m-%d"}[granularity]
    result = await db.execute(
        select(
            func.strftime(fmt, Inspiration.created_at).label("bucket"),
            func.count().label("cnt"),
        )
        .join(InspirationTag, InspirationTag.inspiration_id == Inspiration.id)
        .where(InspirationTag.tag_id == tag_id)
        .group_by("bucket")
        .order_by("bucket")
    )
    return {
        "tag": {"id": tag.id, "name": tag.name},
        "granularity": granularity,
        "trend": [{"bucket": r[0], "count": r[1]} for r in result.all()],
    }
