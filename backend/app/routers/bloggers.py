"""穿搭博主管理 REST API。

与模特（models）已物理拆分：博主拥有平台主页/小红书号/CSV 导入等能力。
路由声明顺序注意：``/top``、``/suggestions``、``/import-csv`` 必须位于
``/{blogger_id}`` 之前，否则会被单段动态路由吞掉。
"""

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.person import (
    BloggerCreate,
    BloggerDetailOut,
    BloggerListOut,
    BloggerOut,
    BloggerUpdate,
    PersonImportResult,
)
from app.services.person_service import (
    PersonConflictError,
    PersonHasInspirationsError,
    PersonNotFoundError,
    blogger_service,
)

router = APIRouter(prefix="/api/bloggers", tags=["bloggers"])


@router.get("", response_model=BloggerListOut)
async def list_bloggers(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    search: str | None = Query(None, description="名称模糊搜索"),
    platform: str | None = Query(None, description="平台筛选"),
    sort: str = Query("newest", pattern="^(newest|name|count)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页获取博主列表，支持名称搜索与平台筛选。"""
    items, total = await blogger_service.list_items(
        db, page=page, size=size, search=search, platform=platform, sort=sort
    )
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("", response_model=BloggerOut, status_code=status.HTTP_201_CREATED)
async def create_blogger(data: BloggerCreate, db: AsyncSession = Depends(get_db)) -> dict:
    """创建穿搭博主。"""
    return await blogger_service.create(
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


@router.post(
    "/import-csv",
    response_model=PersonImportResult,
    status_code=status.HTTP_200_OK,
)
async def import_bloggers_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """上传 CSV 批量导入博主（按 xhs_id upsert，昵称/小红书号必填）。

    CSV 表头：nickname, xhs_id, ip_location（列顺序不限，编码 UTF-8）；
    重复导入相同 xhs_id 不会产生重复记录，而是更新昵称与 IP 属地。
    """
    return await blogger_service.import_from_csv(db, file)


@router.get("/ip-stats")
async def blogger_ip_stats(
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """按 IP 属地分组统计博主数量（空属地归入「未知」）。

    供人物管理页展示穿搭博主的地域分布（柱状图）。
    """
    return await blogger_service.ip_location_stats(db, limit=limit)


@router.get("/top", response_model=list[BloggerOut])
async def top_bloggers(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """按素材数倒序返回热门博主排行。"""
    return await blogger_service.top(db, limit)


@router.get("/suggestions", response_model=list[BloggerOut])
async def suggest_bloggers(
    name: str = Query(..., min_length=1, description="名称模糊关键字"),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """按名称模糊匹配博主（用于前端选择去重）。"""
    return await blogger_service.suggest(db, name)


@router.get("/{blogger_id}", response_model=BloggerDetailOut)
async def get_blogger(blogger_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """获取博主详情（含素材数与风格画像）。"""
    try:
        blogger = await blogger_service.get(db, blogger_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)

    count = await blogger_service.count_inspirations(db, blogger_id)
    profile = await blogger_service.style_profile(db, blogger_id)
    base = blogger_service._to_dict(blogger, count)
    return {**base, "style_profile": profile}


@router.patch("/{blogger_id}", response_model=BloggerOut)
async def update_blogger(
    blogger_id: int, data: BloggerUpdate, db: AsyncSession = Depends(get_db)
) -> dict:
    """更新博主信息（部分更新；显式传 null 的字段会被清空）。"""
    try:
        return await blogger_service.update(
            db, blogger_id, data.model_dump(exclude_unset=True)
        )
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except PersonConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)


@router.delete("/{blogger_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_blogger(blogger_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """删除博主：仅当该博主无关联素材时允许删除，否则返回 400。"""
    try:
        await blogger_service.delete(db, blogger_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except PersonHasInspirationsError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get("/{blogger_id}/inspirations")
async def blogger_inspirations(
    blogger_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    sort: str = Query("newest", pattern="^(newest|oldest|confidence)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取该博主的素材列表（分页 + 排序，排除软删除素材）。"""
    result = await blogger_service.list_inspirations(db, blogger_id, page, size, sort)
    if not result:
        raise HTTPException(status_code=404, detail="博主未找到")
    return result
