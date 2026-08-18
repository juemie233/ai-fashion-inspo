"""职业模特管理 REST API（含写真照片组）。

与博主（bloggers）已物理拆分：模特拥有独立写真照片组
（model_photo_sets，不进入素材库）。
路由声明顺序注意：``/top``、``/suggestions`` 必须位于 ``/{model_id}`` 之前，
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
    ModelCreate,
    ModelDetailOut,
    ModelListOut,
    ModelOut,
    ModelPhotoOut,
    ModelPhotoSetCreate,
    ModelPhotoSetDetailOut,
    ModelPhotoSetListOut,
    ModelPhotoSetOut,
    ModelPhotoSetUpdate,
    ModelUpdate,
)
from app.services.person_service import (
    PersonConflictError,
    PersonHasInspirationsError,
    PersonNotFoundError,
    model_service,
)

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=ModelListOut)
async def list_models(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    search: str | None = Query(None, description="名称模糊搜索"),
    platform: str | None = Query(None, description="平台筛选"),
    sort: str = Query("newest", pattern="^(newest|name|count)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页获取模特列表，支持名称搜索与平台筛选。"""
    items, total = await model_service.list_items(
        db, page=page, size=size, search=search, platform=platform, sort=sort
    )
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
async def create_model(data: ModelCreate, db: AsyncSession = Depends(get_db)) -> dict:
    """创建职业模特。"""
    return await model_service.create(
        db,
        name=data.name,
        platform=data.platform,
        platform_user_id=data.platform_user_id,
        xhs_id=data.xhs_id,
        ip_location=data.ip_location,
        profile_url=data.profile_url,
        avatar_path=data.avatar_path,
        bio=data.bio,
    )


@router.get("/top", response_model=list[ModelOut])
async def top_models(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """按素材数倒序返回热门模特排行。"""
    return await model_service.top(db, limit)


@router.get("/suggestions", response_model=list[ModelOut])
async def suggest_models(
    name: str = Query(..., min_length=1, description="名称模糊关键字"),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """按名称模糊匹配模特（用于前端选择去重）。"""
    return await model_service.suggest(db, name)


@router.get("/{model_id}", response_model=ModelDetailOut)
async def get_model(model_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """获取模特详情（含素材数与风格画像）。"""
    try:
        model = await model_service.get(db, model_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)

    count = await model_service.count_inspirations(db, model_id)
    profile = await model_service.style_profile(db, model_id)
    base = model_service._to_dict(model, count)
    return {**base, "style_profile": profile}


@router.patch("/{model_id}", response_model=ModelOut)
async def update_model(
    model_id: int, data: ModelUpdate, db: AsyncSession = Depends(get_db)
) -> dict:
    """更新模特信息（部分更新；显式传 null 的字段会被清空）。"""
    try:
        return await model_service.update(db, model_id, data.model_dump(exclude_unset=True))
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except PersonConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(model_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """删除模特：仅当该模特无关联素材时允许删除，否则返回 400。"""
    try:
        await model_service.delete(db, model_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except PersonHasInspirationsError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get("/{model_id}/inspirations")
async def model_inspirations(
    model_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    sort: str = Query("newest", pattern="^(newest|oldest|confidence)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取该模特的素材列表（分页 + 排序，排除软删除素材）。"""
    result = await model_service.list_inspirations(db, model_id, page, size, sort)
    if not result:
        raise HTTPException(status_code=404, detail="模特未找到")
    return result


# ── 模特照片组（写真：与穿搭素材分离，仅按文件夹整组导入）──
# 注意：照片组路由统一挂在 /{model_id}/photo-sets 之下（三段路径），
# 不会与单段动态路由 /{model_id} 冲突。


@router.get("/{model_id}/photo-sets", response_model=ModelPhotoSetListOut)
async def list_photo_sets(
    model_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页获取模特照片组（含照片数与封面）。"""
    try:
        items, total = await model_service.list_photo_sets(db, model_id, page, size)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    return {"items": items, "total": total, "page": page, "size": size}


@router.post(
    "/{model_id}/photo-sets",
    response_model=ModelPhotoSetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_photo_set(
    model_id: int,
    data: ModelPhotoSetCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建模特照片组（组名缺省回退「未命名照片组」）。"""
    try:
        return await model_service.create_photo_set(db, model_id, data.name)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/{model_id}/photo-sets/{set_id}", response_model=ModelPhotoSetDetailOut)
async def get_photo_set(
    model_id: int,
    set_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取照片组详情（含分页照片列表）。"""
    try:
        photo_set = await model_service.get_photo_set(db, set_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    if photo_set.model_id != model_id:
        raise HTTPException(status_code=404, detail="照片组未找到")

    photos, total = await model_service.list_set_photos(db, set_id, page, size)
    cover = await model_service.get_photo_set_cover(db, set_id)
    base = model_service._to_photo_set_dict(photo_set, total, cover)
    return {**base, "photos": photos, "total": total, "page": page, "size": size}


@router.patch("/{model_id}/photo-sets/{set_id}", response_model=ModelPhotoSetOut)
async def update_photo_set(
    model_id: int,
    set_id: int,
    data: ModelPhotoSetUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """更新照片组名称。"""
    try:
        photo_set = await model_service.get_photo_set(db, set_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    if photo_set.model_id != model_id:
        raise HTTPException(status_code=404, detail="照片组未找到")
    try:
        return await model_service.update_photo_set(db, set_id, data.name)
    except PersonConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)


@router.delete("/{model_id}/photo-sets/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_photo_set(
    model_id: int,
    set_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """删除照片组（级联删除照片与物理文件）。"""
    try:
        photo_set = await model_service.get_photo_set(db, set_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    if photo_set.model_id != model_id:
        raise HTTPException(status_code=404, detail="照片组未找到")
    await model_service.delete_photo_set(db, set_id)


@router.post(
    "/{model_id}/photo-sets/{set_id}/photos",
    response_model=ModelPhotoOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_photo_to_set(
    model_id: int,
    set_id: int,
    file: UploadFile = File(...),
    sort_order: int = Form(default=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """上传一张照片到照片组（组内内容去重）。"""
    try:
        photo_set = await model_service.get_photo_set(db, set_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    if photo_set.model_id != model_id:
        raise HTTPException(status_code=404, detail="照片组未找到")
    return await model_service.add_photo_to_set(db, set_id, file, sort_order)


@router.delete(
    "/{model_id}/photo-sets/{set_id}/photos/{photo_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_photo(
    model_id: int,
    set_id: int,
    photo_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除照片组内的单张照片（校验照片归属 set_id，防跨组误删）。"""
    try:
        photo_set = await model_service.get_photo_set(db, set_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    if photo_set.model_id != model_id:
        raise HTTPException(status_code=404, detail="照片组未找到")
    try:
        await model_service.delete_photo(db, photo_id, set_id=set_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    return {"removed": 1}
