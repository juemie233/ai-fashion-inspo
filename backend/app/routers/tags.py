"""标签管理的 REST API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.tag import Tag
from app.schemas.tag import TagCreate, TagCategoryGroup, TagMergeRequest, TagOut, TagUpdate
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

    tag = Tag(name=data.name.strip(), category=data.category)
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
