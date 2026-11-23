"""收藏合集的 REST API 路由。

契约见 docs/features/收藏合集两级结构.md「三、API 契约」：
- 手动合集（query_json IS NULL）：实体成员，支持批量加入/移出与拖拽排序；
- 智能合集（query_json 非空）：动态求值，仅支持查看内容、更新条件与固化。
"""


from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.collection import (
    CollectionCreate,
    CollectionUpdate,
    CollectionExportRequest,
    CollectionExpandRequest,
    CollectionMergeRequest,
    CollectionSuggestRequest,
    CollectionTransferRequest,
    AddInspirationsRequest,
    ItemReorderRequest,
    CollectionReorderRequest,
    CollectionOut,
)
from app.schemas.inspiration import inspiration_to_out
from app.services import (
    collection_ai_service,
    collection_export_service,
    collection_service,
    collection_suggest_service,
)

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
    q: str | None = Query(None, max_length=100, description="关键词（作者/正文/标签名模糊）"),
    media_type: str | None = Query(None, pattern="^(image|video)$"),
    quality_status: str | None = Query(
        None, pattern="^(pending|approved|rejected)$", description="质量状态"
    ),
    rating_min: int | None = Query(None, ge=1, le=5, description="评分下限（评分 ≥ 该值）"),
    is_favorite: bool | None = Query(None, description="仅收藏 / 仅未收藏"),
    sort: str | None = Query(
        None,
        pattern="^(manual|newest|oldest|rating|rating_asc)$",
        description="排序：手动合集默认 manual；智能合集默认 newest（显式 manual 返回 400）",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页获取合集内容（支持搜索 / 筛选 / 排序）。

    - 手动合集：默认按 position 升序（成员拖拽顺序），筛选后再分页；
    - 智能合集：按 query_json 动态求值（与素材库同一套筛选口径），默认上传时间倒序；
    - 排序：manual（仅手动合集）/ newest / oldest / rating / rating_asc；
    - 筛选：关键词（作者名/正文/标签名模糊）、media_type、quality_status、
      rating_min、is_favorite；均排除垃圾桶素材。
    """
    inspirations, total = await collection_service.list_collection_inspirations(
        db,
        collection_id,
        page=page,
        size=page_size,
        q=q,
        media_type=media_type,
        quality_status=quality_status,
        rating_min=rating_min,
        is_favorite=is_favorite,
        sort=sort,
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


@router.post("/{source_id}/inspirations/transfer", status_code=status.HTTP_200_OK)
async def transfer_inspirations(
    source_id: int,
    body: CollectionTransferRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """批量移动/复制合集成员到多个目标合集（整单事务）。

    请求体：``{ids, target_ids, mode=move|copy}``。``ids`` 必须全部属于源合集；
    目标已存在则跳过，其余 append 到目标末尾；``move`` 才从源合集移除。
    源/目标都必须是普通手动合集（非自动维护、非系统收藏根、非分类节点）。
    """
    return await collection_service.transfer_inspirations(
        db, source_id, body.ids, body.target_ids, body.mode
    )


@router.post("/{target_id}/merge", status_code=status.HTTP_200_OK)
async def merge_collections(
    target_id: int,
    body: CollectionMergeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """把多个源合集合并进目标合集，成功后删除源合集（整单事务）。

    请求体：``{source_ids}``。源成员按 position/added_at 顺序 append，目标已有跳过；
    源不能等于目标；源/目标都必须是普通手动合集。返回合并数/新增/跳过/被删源 ID。
    """
    return await collection_service.merge_collections(db, target_id, body.source_ids)


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


@router.post("/{collection_id}/ai-summary", status_code=status.HTTP_200_OK)
async def generate_ai_summary(
    collection_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    """合集 AI 整理预览（规则分组 + Ollama 起名，**不写库**）。

    - 采样最多 80 条素材；规则按标签词频取 ≤5 个主题标签分组（允许重叠，每组 ≤12 个
      item_ids）；无标签时按主色/评分兜底 1 组；
    - AI 生成 summary / 3 个名称建议 / 理由 / 5~8 个主题标签 / 每组中文主题名；
      JSON 解析失败时分组名回退 tag，其余字段规则兜底；
    - Ollama 超时/不可用返回 503 中文提示；空合集 400；
    - 结果只供前端预览，由前端用现有 PATCH 应用名称/描述（不自动写入）。
    """
    return await collection_ai_service.generate_summary(db, collection_id)


@router.post("/{collection_id}/to-semi", response_model=CollectionOut)
async def convert_to_semi_manual(
    collection_id: int, db: AsyncSession = Depends(get_db)
) -> CollectionOut:
    """把智能合集转为「半智能」：保留规则匹配，同时允许补充实体成员。

    转后内容 = ``query_json`` 规则匹配 ∪ ``collection_items`` 成员，
    可用「按此扩充」/手动加入/移出；手动合集或系统收藏根返回 400。
    """
    data = await collection_service.enable_semi_manual(db, collection_id)
    return CollectionOut(**data)


@router.post("/{collection_id}/suggest-items", status_code=status.HTTP_200_OK)
async def suggest_collection_items(
    collection_id: int,
    body: CollectionSuggestRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """「按此合集扩充」生成候选素材（**只读，不写库**）。

    召回：种子成员图像向量质心 Top200 + 种子 Top 标签命中的素材；打分沿用
    0.6 视觉 + 0.4 标签。只服务用户自建手动合集（智能/抖音同步/分类节点 400）。
    响应含 ``items[{inspiration, similarity, shared_tags, outfit_tags, reason,
    match_source}]`` 与 ``profile{top_tags, outfit_tags, seeds_used,
    vector_available}``，供前端预览勾选后再调 ``POST /{id}/expansions``。
    """
    result = await collection_suggest_service.suggest_items(
        db,
        collection_id,
        limit=body.limit,
        min_similarity=body.min_similarity,
        outfit_only=body.outfit_only,
        exclude_other_auto=body.exclude_other_auto,
    )
    return {
        "items": [
            {
                "inspiration": inspiration_to_out(item["inspiration"]),
                "similarity": item["similarity"],
                "shared_tags": item["shared_tags"],
                "outfit_tags": item["outfit_tags"],
                "match_source": item["match_source"],
                "reason": item["reason"],
            }
            for item in result["items"]
        ],
        "total": result["total"],
        "profile": result["profile"],
    }


@router.post("/{collection_id}/expansions", status_code=status.HTTP_200_OK)
async def expand_collection(
    collection_id: int,
    body: CollectionExpandRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """把勾选的候选素材按「扩充批次」写入手动合集。

    只允许用户自建手动合集（抖音同步合集 400：同步只增不删，写了会让本地夹与
    抖音不一致）；每批生成 ``batch_id``，成员 ``added_by='ai_suggest'``，
    可调 ``DELETE /{id}/expansions/{batch_id}`` 一键撤销。
    """
    return await collection_service.expand_collection(
        db, collection_id, body.inspiration_ids
    )


@router.delete(
    "/{collection_id}/expansions/{batch_id}", status_code=status.HTTP_200_OK
)
async def undo_collection_expansion(
    collection_id: int,
    batch_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """撤销某一扩充批次：只删该批次、``added_by='ai_suggest'`` 的成员。

    手工加入与抖音同步的成员不受影响；批次不存在返回 404。
    """
    return await collection_service.undo_expansion(db, collection_id, batch_id)


@router.post("/{collection_id}/exports", status_code=status.HTTP_200_OK)
async def create_collection_export(
    collection_id: int,
    body: CollectionExportRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """生成合集导出（CSV / Markdown / ZIP / PDF），返回下载 token。

    手动/智能合集均可；只导出未删除素材（垃圾桶排除）；空合集 400；
    超过 500 条 400（提示分批）。文件保留 24 小时，生成前会清理过期导出。
    响应字段：``{token, filename, format, item_count, skipped_count, size_bytes,
    download_url, expires_at}``，下载地址固定为 ``/api/exports/{token}``。
    """
    return await collection_export_service.create_export(
        db, collection_id, body.format
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
