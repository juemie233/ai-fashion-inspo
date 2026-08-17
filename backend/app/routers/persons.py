"""人物管理的 REST API 路由。

路由声明顺序注意：``/top``、``/suggestions`` 必须位于 ``/{person_id}`` 之前，
否则会被单段动态路由吞掉。
"""

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.person import (
    PersonCreate,
    PersonDetailOut,
    PersonListOut,
    PersonOut,
    PersonPhotoOut,
    PersonPhotoSetCreate,
    PersonPhotoSetDetailOut,
    PersonPhotoSetListOut,
    PersonPhotoSetOut,
    PersonPhotoSetUpdate,
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
) -> dict:
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
async def create_person(data: PersonCreate, db: AsyncSession = Depends(get_db)) -> dict:
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
) -> list[dict]:
    """按素材数倒序返回热门人物排行。"""
    return await person_service.top_persons(db, limit)


@router.get("/suggestions", response_model=list[PersonOut])
async def suggest_persons(
    name: str = Query(..., min_length=1, description="名称模糊关键字"),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """按名称模糊匹配人物（用于前端选择去重）。"""
    return await person_service.suggest_persons(db, name)


@router.get("/{person_id}", response_model=PersonDetailOut)
async def get_person(person_id: int, db: AsyncSession = Depends(get_db)) -> dict:
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
) -> dict:
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
async def delete_person(person_id: int, db: AsyncSession = Depends(get_db)) -> None:
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
    sort: str = Query("newest", pattern="^(newest|oldest|confidence)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取该人物的素材列表（分页 + 排序，排除软删除素材）。"""
    result = await person_service.list_person_inspirations(db, person_id, page, size, sort)
    if not result:
        raise HTTPException(status_code=404, detail="人物未找到")
    return result


# ── 人物照片组（模特写真：与穿搭素材分离，仅按文件夹整组导入）──
# 注意：照片组路由统一挂在 /{person_id}/photo-sets 之下（三段路径），
# 不会与单段动态路由 /{person_id} 冲突。


@router.get("/{person_id}/photo-sets", response_model=PersonPhotoSetListOut)
async def list_photo_sets(
    person_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页获取人物照片组（含照片数与封面）。"""
    try:
        items, total = await person_service.list_photo_sets(db, person_id, page, size)
    except person_service.PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    return {"items": items, "total": total, "page": page, "size": size}


@router.post(
    "/{person_id}/photo-sets",
    response_model=PersonPhotoSetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_photo_set(
    person_id: int,
    data: PersonPhotoSetCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建人物照片组（组名缺省回退「未命名照片组」）。"""
    try:
        return await person_service.create_photo_set(db, person_id, data.name)
    except person_service.PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/{person_id}/photo-sets/{set_id}", response_model=PersonPhotoSetDetailOut)
async def get_photo_set(
    person_id: int,
    set_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取照片组详情（含分页照片列表）。"""
    try:
        photo_set = await person_service.get_photo_set(db, set_id)
    except person_service.PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    if photo_set.person_id != person_id:
        raise HTTPException(status_code=404, detail="照片组未找到")

    photos, total = await person_service.list_set_photos(db, set_id, page, size)
    cover = await person_service.get_photo_set_cover(db, set_id)
    base = person_service._to_photo_set_dict(photo_set, total, cover)
    return {**base, "photos": photos, "total": total, "page": page, "size": size}


@router.patch("/{person_id}/photo-sets/{set_id}", response_model=PersonPhotoSetOut)
async def update_photo_set(
    person_id: int,
    set_id: int,
    data: PersonPhotoSetUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """更新照片组名称。"""
    try:
        photo_set = await person_service.get_photo_set(db, set_id)
    except person_service.PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    if photo_set.person_id != person_id:
        raise HTTPException(status_code=404, detail="照片组未找到")
    try:
        return await person_service.update_photo_set(db, set_id, data.name)
    except person_service.PersonConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)


@router.delete("/{person_id}/photo-sets/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_photo_set(
    person_id: int,
    set_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """删除照片组（级联删除照片与物理文件）。"""
    try:
        photo_set = await person_service.get_photo_set(db, set_id)
    except person_service.PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    if photo_set.person_id != person_id:
        raise HTTPException(status_code=404, detail="照片组未找到")
    await person_service.delete_photo_set(db, set_id)


@router.post(
    "/{person_id}/photo-sets/{set_id}/photos",
    response_model=PersonPhotoOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_photo_to_set(
    person_id: int,
    set_id: int,
    file: UploadFile = File(...),
    sort_order: int = Form(default=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """上传一张照片到照片组（组内内容去重）。"""
    try:
        photo_set = await person_service.get_photo_set(db, set_id)
    except person_service.PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    if photo_set.person_id != person_id:
        raise HTTPException(status_code=404, detail="照片组未找到")
    return await person_service.add_photo_to_set(db, set_id, file, sort_order)


@router.delete(
    "/{person_id}/photo-sets/{set_id}/photos/{photo_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_photo(
    person_id: int,
    set_id: int,
    photo_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除照片组内的单张照片（校验照片归属 set_id，防跨组误删）。"""
    try:
        photo_set = await person_service.get_photo_set(db, set_id)
    except person_service.PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    if photo_set.person_id != person_id:
        raise HTTPException(status_code=404, detail="照片组未找到")
    try:
        await person_service.delete_photo(db, photo_id, set_id=set_id)
    except person_service.PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    return {"removed": 1}
