"""标签管理的 REST API 路由。"""

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.inspiration import Inspiration
from app.models.tag import Tag, InspirationTag
from app.schemas.tag import (
    TagBatchDelete,
    TagCreate,
    TagCategoryGroup,
    TagImportRequest,
    TagMergeRequest,
    TagOut,
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


@router.get("/popular")
async def popular_tags(db: AsyncSession = Depends(get_db)):
    """获取热门标签（按使用次数降序排列，前50条）。"""
    grouped = await get_all_tags_grouped(db)
    all_tags = []
    for cat, tags in grouped.items():
        all_tags.extend(tags)
    all_tags.sort(key=lambda t: t["usage_count"], reverse=True)
    return all_tags[:50]


@router.post("", response_model=TagOut, status_code=status.HTTP_201_CREATED)
async def create_tag(data: TagCreate, db: AsyncSession = Depends(get_db)):
    """手动创建自定义标签。"""
    # 检查标签是否已存在
    result = await db.execute(select(Tag).where(Tag.name == data.name.strip()))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"标签 '{data.name}' 已存在",
        )

    tag = Tag(name=data.name.strip(), category=data.category, source="manual")
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
    """更新标签的名称或类别。"""
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="标签未找到")

    if data.name is not None:
        # 检查名称是否冲突
        result = await db.execute(
            select(Tag).where(Tag.name == data.name.strip(), Tag.id != tag_id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=409, detail=f"标签 '{data.name}' 已存在"
            )
        tag.name = data.name.strip()

    if data.category is not None:
        tag.category = data.category

    await db.flush()
    await db.refresh(tag)
    return TagOut(
        id=tag.id,
        name=tag.name,
        category=tag.category,
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


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: int, db: AsyncSession = Depends(get_db)):
    """删除标签及其所有素材关联。"""
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="标签未找到")
    await db.delete(tag)
    await db.flush()


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


# ============ 类别管理 ============


# 默认类别配置
_DEFAULT_CATEGORIES = {
    "style": "风格", "item_type": "单品类型", "color": "颜色",
    "fit": "版型", "body_part": "穿着方式",
    "attribute": "属性", "season": "季节", "material": "面料",
    "brand": "品牌", "free": "其他", "outfit": "穿搭大标签",
}


@router.get("/categories")
async def list_categories():
    """获取所有可用标签类别。"""
    return {"categories": [
        {"key": k, "label": v} for k, v in _DEFAULT_CATEGORIES.items()
    ]}


@router.post("/categories")
async def add_category(payload: dict = Body(...)):
    """动态添加标签类别。请求体: {"key": "new_cat", "label": "新类别"}"""
    key = payload.get("key", "").strip()
    label = payload.get("label", "").strip()
    if not key or not label:
        raise HTTPException(status_code=400, detail="请提供 key 和 label")
    if key in _DEFAULT_CATEGORIES:
        raise HTTPException(status_code=409, detail=f"类别 '{key}' 已存在")
    _DEFAULT_CATEGORIES[key] = label
    return {"message": f"已添加类别 '{label}'", "key": key, "label": label}


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
