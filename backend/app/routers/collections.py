"""收藏合集的 REST API 路由。

契约见 docs/收藏合集设计方案.md「三、API 契约」：
- 手动合集（query_json IS NULL）：实体成员，支持批量加入/移出与拖拽排序；
- 智能合集（query_json 非空）：动态求值，仅支持查看内容、更新条件与固化。
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.inspiration import InspirationOut, inspiration_to_out
from app.services import collection_service

router = APIRouter(prefix="/api/collections", tags=["collections"])


# ── 请求 / 响应模型 ──


class SmartQueryJSON(BaseModel):
    """智能合集筛选条件（query_json 的契约结构；未知键忽略，向前兼容）。"""

    keyword: str | None = None  # 关键词（标签名/作者名/文件名）
    tag_ids: list[int] = Field(default_factory=list)  # 标签 ID 列表
    tag_mode: Literal["and", "or"] = "and"  # 标签组合语义
    source_types: list[str] | None = None  # 来源类型列表（None/空 = 全部）
    media_type: Literal["image", "video"] | None = None  # 媒体类型（None = 全部）
    is_favorite: bool | None = None  # 收藏筛选（null = 不筛选）
    min_rating: int | None = Field(None, ge=0, le=5)  # 评分下限（0 = 不筛选）
    start_date: str | None = None  # 上传日期下限（YYYY-MM-DD）
    end_date: str | None = None  # 上传日期上限（YYYY-MM-DD）

    def to_storage(self) -> dict[str, Any]:
        """转为入库存储结构（None 字段剔除，语义与 null 等价）。"""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class CollectionCreate(BaseModel):
    """创建合集请求体：query_json 缺省为手动合集，提供时为智能合集。"""

    name: str = Field(min_length=1, max_length=50, description="合集名 1~50 字")
    description: str | None = None
    query_json: SmartQueryJSON | None = None


class CollectionUpdate(BaseModel):
    """更新合集请求体（部分更新，仅更新显式提供的字段）。"""

    name: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = None
    cover_inspiration_id: str | None = None  # 显式 null = 清空手动封面
    query_json: SmartQueryJSON | None = None  # 仅智能合集可更新（转手动用 solidify）


class AddInspirationsRequest(BaseModel):
    """批量加入/移出素材的请求体。"""

    inspiration_ids: list[str] = Field(min_length=1, max_length=200)


class ItemReorderRequest(BaseModel):
    """合集内成员拖拽排序的请求体（按新顺序提交全部成员 ID）。"""

    ordered_ids: list[str] = Field(min_length=1, max_length=1000)


class CollectionReorderRequest(BaseModel):
    """合集列表拖拽排序的请求体（按新顺序提交合集 ID）。"""

    ordered_ids: list[int] = Field(min_length=1, max_length=1000)


class CollectionOut(BaseModel):
    """合集输出（列表 / 创建 / 更新 / 固化共用）。"""

    id: int
    name: str
    description: str | None = None
    kind: str  # manual 手动合集 / smart 智能合集
    position: int
    cover_inspiration_id: str | None = None
    cover_thumbnail_path: str | None = None
    item_count: int | None = None  # 手动 = 成员数；智能 = null（懒计算）
    query_json: dict | None = None  # 仅智能合集返回
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, dt: datetime | None) -> str | None:
        from app.utils.time import format_utc

        return format_utc(dt)


# 注意：PATCH /order 必须声明在 PATCH /{collection_id} 之前，
# 否则 "order" 会被动态路由吞掉（collection_id 解析失败 422）


@router.get("", response_model=list[CollectionOut])
async def list_collections(db: AsyncSession = Depends(get_db)) -> list[CollectionOut]:
    """获取合集列表（按 position 升序）。

    手动合集 item_count 为成员数；智能合集为 null（懒计算，避免进列表页全量求值）。
    """
    data = await collection_service.list_collections(db)
    return [CollectionOut(**d) for d in data]


@router.post("", response_model=CollectionOut, status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: CollectionCreate, db: AsyncSession = Depends(get_db)
) -> CollectionOut:
    """创建合集（重名 409）；带 query_json 时创建智能合集。"""
    data = await collection_service.create_collection(
        db,
        body.name,
        description=body.description,
        query_json=body.query_json.to_storage() if body.query_json else None,
    )
    return CollectionOut(**data)


@router.patch("/order", status_code=status.HTTP_200_OK)
async def reorder_collections(
    body: CollectionReorderRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """提交合集列表顺序（拖拽排序）：按 ordered_ids 顺序重排 position。

    未出现在 ordered_ids 中的合集保持原有相对顺序追加到末尾。
    """
    return await collection_service.reorder_collections(db, body.ordered_ids)


@router.patch("/{collection_id}", response_model=CollectionOut)
async def update_collection(
    collection_id: int,
    body: CollectionUpdate,
    db: AsyncSession = Depends(get_db),
) -> CollectionOut:
    """更新合集基础信息（部分更新）。

    query_json 仅智能合集可更新（「更新为当前筛选条件」）；手动合集传
    query_json 返回 400（转手动合集请使用 solidify）。
    """
    if "query_json" in body.model_fields_set and body.query_json is None:
        raise HTTPException(
            status_code=400, detail="query_json 不能置空，智能合集转手动请使用 solidify"
        )
    data = await collection_service.update_collection(
        db,
        collection_id,
        name=body.name,
        description=body.description,
        cover_inspiration_id=body.cover_inspiration_id,
        query_json=(
            body.query_json.to_storage()
            if "query_json" in body.model_fields_set and body.query_json is not None
            else None
        ),
        clear_cover=(
            "cover_inspiration_id" in body.model_fields_set
            and body.cover_inspiration_id is None
        ),
    )
    return CollectionOut(**data)


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(collection_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """删除合集（不动素材：手动合集级联删成员关系，智能合集仅删自身）。"""
    await collection_service.delete_collection(db, collection_id)


@router.get("/{collection_id}/inspirations")
async def list_collection_inspirations(
    collection_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页获取合集内容。

    手动合集按 position 升序；智能合集按 query_json 动态求值（与素材库
    同一套筛选口径，默认上传时间倒序）。均排除垃圾桶素材。
    """
    inspirations, total = await collection_service.list_collection_inspirations(
        db, collection_id, page=page, size=page_size
    )
    return {
        "items": [inspiration_to_out(i) for i in inspirations],
        "total": total,
    }


@router.post("/{collection_id}/inspirations", status_code=status.HTTP_200_OK)
async def add_inspirations(
    collection_id: int,
    body: AddInspirationsRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """批量加入素材到手动合集（请求内去重，已加入/不存在的素材跳过）。

    新成员追加到合集末尾；智能合集返回 400。
    """
    return await collection_service.add_inspirations(
        db, collection_id, body.inspiration_ids
    )


@router.delete("/{collection_id}/inspirations", status_code=status.HTTP_200_OK)
async def remove_inspirations(
    collection_id: int,
    body: AddInspirationsRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """批量移出素材（不在合集内的 ID 跳过）；智能合集返回 400。"""
    return await collection_service.remove_inspirations(
        db, collection_id, body.inspiration_ids
    )


@router.patch("/{collection_id}/items/order", status_code=status.HTTP_200_OK)
async def reorder_collection_items(
    collection_id: int,
    body: ItemReorderRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """提交合集内成员展示顺序（拖拽编排）：按 ordered_ids 重排 position。

    包含不属于该合集的素材返回 400；智能合集返回 400。
    """
    return await collection_service.reorder_collection_items(
        db, collection_id, body.ordered_ids
    )


@router.post("/{collection_id}/solidify", response_model=CollectionOut)
async def solidify_collection(
    collection_id: int, db: AsyncSession = Depends(get_db)
) -> CollectionOut:
    """智能合集转手动：把当前匹配素材按当前位置固化并清空 query_json。

    手动合集返回 400；固化后可手动增删成员与拖拽排序。
    """
    data = await collection_service.solidify_collection(db, collection_id)
    return CollectionOut(**data)
