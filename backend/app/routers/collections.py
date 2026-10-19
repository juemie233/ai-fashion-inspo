"""收藏合集的 REST API 路由。

契约见 docs/收藏合集设计方案.md「三、API 契约」：
- 手动合集（query_json IS NULL）：实体成员，支持批量加入/移出与拖拽排序；
- 智能合集（query_json 非空）：动态求值，仅支持查看内容、更新条件与固化。
"""


from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.collection import (
    CollectionCreate,
    CollectionUpdate,
    AddInspirationsRequest,
    ItemReorderRequest,
    CollectionReorderRequest,
    CollectionOut,
)
from app.schemas.inspiration import inspiration_to_out
from app.services import collection_service

router = APIRouter(prefix="/api/collections", tags=["collections"])


# ── 请求 / 响应模型 ──


# 注意：PATCH /order 必须声明在 PATCH /{collection_id} 之前，
# 否则 "order" 会被动态路由吞掉（collection_id 解析失败 422）


@router.get("", response_model=list[CollectionOut])
async def list_collections(db: AsyncSession = Depends(get_db)) -> list[CollectionOut]:
    """获取合集列表（一级按 position 升序，二级紧随其父）。

    手动合集 item_count 为成员数；智能合集为 null（懒计算，避免进列表页全量求值）。
    """
    data = await collection_service.list_collections(db)
    return [CollectionOut(**d) for d in data]


@router.post("", response_model=CollectionOut, status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: CollectionCreate, db: AsyncSession = Depends(get_db)
) -> CollectionOut:
    """创建合集（同层重名 409）；带 query_json 时创建智能合集。

    ``parent_id`` 非空时创建二级收藏夹（父必须是一级合集）。
    """
    data = await collection_service.create_collection(
        db,
        body.name,
        description=body.description,
        query_json=body.query_json.to_storage() if body.query_json else None,
        parent_id=body.parent_id,
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
