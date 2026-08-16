"""人物管理的 REST API 路由。

路由声明顺序注意：``/top``、``/suggestions`` 必须位于 ``/{person_id}`` 之前，
否则会被单段动态路由吞掉。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.person import (
    PersonCreate,
    PersonDetailOut,
    PersonListOut,
    PersonOut,
    PersonUpdate,
)
from app.services import person_service

router = APIRouter(prefix="/api/persons", tags=["persons"])


@router.get("", response_model=PersonListOut)
async def list_persons(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    search: str | None = Query(None, description="名称模糊搜索"),
    person_type: str | None = Query(None, description="内容类型：model | blogger"),
    platform: str | None = Query(None, description="平台筛选"),
    sort: str = Query("newest", pattern="^(newest|name|count)$"),
    db: AsyncSession = Depends(get_db),
):
    """分页获取人物列表，支持名称搜索与内容类型/平台筛选。"""
    items, total = await person_service.list_persons(
        db,
        page=page,
        size=size,
        search=search,
        person_type=person_type,
        platform=platform,
        sort=sort,
    )
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("", response_model=PersonOut, status_code=status.HTTP_201_CREATED)
async def create_person(data: PersonCreate, db: AsyncSession = Depends(get_db)):
    """创建人物（职业模特 / 穿搭博主）。"""
    return await person_service.create_person(
        db,
        name=data.name,
        person_type=data.person_type,
        platform=data.platform,
        platform_user_id=data.platform_user_id,
        profile_url=data.profile_url,
        avatar_path=data.avatar_path,
        bio=data.bio,
    )


@router.get("/top", response_model=list[PersonOut])
async def top_persons(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """按素材数倒序返回热门人物排行。"""
    return await person_service.top_persons(db, limit)


@router.get("/suggestions", response_model=list[PersonOut])
async def suggest_persons(
    name: str = Query(..., min_length=1, description="名称模糊关键字"),
    db: AsyncSession = Depends(get_db),
):
    """按名称模糊匹配人物（用于前端选择去重）。"""
    return await person_service.suggest_persons(db, name)


@router.get("/{person_id}", response_model=PersonDetailOut)
async def get_person(person_id: int, db: AsyncSession = Depends(get_db)):
    """获取人物详情（含素材数与风格画像）。"""
    try:
        person = await person_service.get_person(db, person_id)
    except person_service.PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)

    count = await person_service.get_person_inspiration_count(db, person_id)
    profile = await person_service.get_person_style_profile(db, person_id)
    base = person_service._to_person_dict(person, count)
    return {**base, "style_profile": profile}


@router.patch("/{person_id}", response_model=PersonOut)
async def update_person(
    person_id: int, data: PersonUpdate, db: AsyncSession = Depends(get_db)
):
    """更新人物信息（部分更新；显式传 null 的字段会被清空）。"""
    try:
        return await person_service.update_person(
            db, person_id, data.model_dump(exclude_unset=True)
        )
    except person_service.PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except person_service.PersonConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)


@router.delete("/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_person(person_id: int, db: AsyncSession = Depends(get_db)):
    """删除人物（inspiration_persons 关联级联删除）。"""
    try:
        await person_service.delete_person(db, person_id)
    except person_service.PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/{person_id}/inspirations")
async def person_inspirations(
    person_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    sort: str = "newest",  # newest | oldest | confidence
    db: AsyncSession = Depends(get_db),
):
    """获取该人物的素材列表（分页 + 排序，排除软删除素材）。"""
    result = await person_service.list_person_inspirations(db, person_id, page, size, sort)
    if not result:
        raise HTTPException(status_code=404, detail="人物未找到")
    return result
