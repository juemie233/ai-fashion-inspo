"""标签管理的 REST API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.tag import TagAlias
from app.schemas.tag import (
    AliasCreate,
    AliasOut,
    ClusterApplyRequest,
    TagBatchDelete,
    TagCategoryGroup,
    TagCreate,
    TagImportRequest,
    TagMergeRequest,
    TagOut,
    TagReorderRequest,
    TagUpdate,
)
from app.services import tag_service

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("")
async def list_tags(db: AsyncSession = Depends(get_db)) -> list[TagCategoryGroup]:
    """获取所有标签，按类别分组。"""
    grouped = await tag_service.get_all_tags_grouped(db)
    return [
        TagCategoryGroup(category=cat, tags=tags)
        for cat, tags in grouped.items()
    ]


@router.post("", response_model=TagOut, status_code=status.HTTP_201_CREATED)
async def create_tag(data: TagCreate, db: AsyncSession = Depends(get_db)) -> TagOut:
    """手动创建自定义标签。"""
    try:
        tag = await tag_service.create_tag(db, data.name, data.category)
    except tag_service.TagConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)
    return TagOut(
        id=tag.id,
        name=tag.name,
        category=tag.category,
        created_at=tag.created_at,
        usage_count=0,
    )


# ============ 批量编辑 ============
# 注意：PATCH /batch-category、/batch-rename 必须声明在 PATCH /{tag_id} 之前，
# 否则会被动态路由吞掉（tag_id 解析为字符串导致 422）


@router.patch("/batch-category", status_code=status.HTTP_200_OK)
async def batch_change_category(
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """批量修改标签类别。请求体: {"tag_ids": [1,2,3], "category": "style"}"""
    tag_ids = payload.get("tag_ids", [])
    category = payload.get("category", "").strip()
    if not tag_ids or not category:
        raise HTTPException(status_code=400, detail="请提供 tag_ids 和 category")
    updated = await tag_service.batch_change_category(db, tag_ids, category)
    return {"updated": updated, "category": category}


@router.patch("/batch-rename", status_code=status.HTTP_200_OK)
async def batch_rename_tags(
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """批量重命名标签（查找替换）。请求体: {"tag_ids": [1,2], "find": "白色", "replace": "纯白"}"""
    tag_ids = payload.get("tag_ids", [])
    find_str = payload.get("find", "")
    replace_str = payload.get("replace", "")
    if not tag_ids or not find_str:
        raise HTTPException(status_code=400, detail="请提供 tag_ids 和 find 参数")

    try:
        updated = await tag_service.batch_rename_tags(db, tag_ids, find_str, replace_str)
    except tag_service.TagConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)
    return {"updated": updated, "find": find_str, "replace": replace_str}


@router.patch("/{tag_id}", response_model=TagOut)
async def update_tag(tag_id: int, data: TagUpdate, db: AsyncSession = Depends(get_db)) -> TagOut:
    """更新标签的名称、类别、置顶、排序或备注。"""
    try:
        tag = await tag_service.update_tag(
            db,
            tag_id,
            name=data.name,
            category=data.category,
            pinned=data.pinned,
            sort_order=data.sort_order,
            description=data.description,
        )
    except tag_service.TagNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except tag_service.TagConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)
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
async def delete_unused_tags(db: AsyncSession = Depends(get_db)) -> dict:
    """删除所有使用次数为 0 的标签。"""
    import logging
    _logger = logging.getLogger(__name__)

    unused = await tag_service.delete_unused_tags(db)
    if not unused:
        return {"message": "没有未使用的标签", "count": 0}

    _logger.info(f"已删除 {len(unused)} 个未使用标签: {[t.name for t in unused[:10]]}...")
    return {"message": f"已删除 {len(unused)} 个未使用标签", "count": len(unused)}


@router.post("/batch-delete", status_code=status.HTTP_200_OK)
async def batch_delete_tags(
    data: TagBatchDelete, db: AsyncSession = Depends(get_db)
) -> dict:
    """批量删除标签及其所有关联。"""
    if not data.tag_ids:
        raise HTTPException(status_code=400, detail="请提供要删除的标签 ID 列表")

    tags = await tag_service.batch_delete_tags(db, data.tag_ids)
    if not tags:
        raise HTTPException(status_code=404, detail="未找到任何标签")

    return {"message": f"已删除 {len(tags)} 个标签", "count": len(tags)}


@router.post("/merge", status_code=status.HTTP_200_OK)
async def merge_tags_endpoint(data: TagMergeRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """将源标签合并到目标标签，删除源标签。"""
    if data.source_tag_id == data.target_tag_id:
        raise HTTPException(status_code=400, detail="不能将标签合并到自身")

    try:
        source_name, target_name = await tag_service.merge_tag_pair(
            db, data.source_tag_id, data.target_tag_id
        )
    except tag_service.TagNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    return {"message": f"已将 '{source_name}' 合并到 '{target_name}'"}


@router.get("/suggestions/{name}")
async def tag_suggestions(name: str, db: AsyncSession = Depends(get_db)) -> list[dict]:
    """查找与给定名称相似的已有标签（用于去重建议）。"""
    similar = await tag_service.find_similar_tags(db, name)
    return [
        {"id": t.id, "name": t.name, "category": t.category}
        for t in similar
    ]


# ============ 统计与扫描 ============


@router.get("/stats")
async def tag_stats(db: AsyncSession = Depends(get_db)) -> dict:
    """获取标签统计数据。"""
    return await tag_service.get_tag_stats(db)


@router.get("/duplicates")
async def find_duplicate_tags(
    threshold: float = Query(0.75, ge=0.6, le=0.95),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """扫描所有标签，找出名称相似度 >= threshold 的标签对。

    结果缓存：基于标签最后修改时间 + threshold 生成 key，
    标签数据不变时直接读缓存，瞬时返回。
    """
    from app.models.tag import Tag as TagModel
    from sqlalchemy import func as sa_func

    from app.services.tag_dedupe_cache import compute_cache_key, get_cached, set_cached

    # 1. 查缓存
    # 缓存 key 基于「最后修改时间」：标签的创建/改名/改类别/合并都会刷新
    # updated_at（created_at 只记录创建，改名/改类别不刷新它，会漏失效）。
    last_mod_result = await db.execute(
        sa_func.max(TagModel.updated_at)
    )
    last_mod = str(last_mod_result.scalar() or "")
    cache_key = compute_cache_key(last_mod, threshold)
    cached = get_cached(cache_key)
    if cached:
        return {"duplicates": cached["pairs"][:50], "total": cached["total"], "cached": True}

    # 2. 未命中 → 计算
    pairs, total = await tag_service.find_duplicate_tag_pairs(db, threshold)
    set_cached(cache_key, pairs, total)
    return {"duplicates": pairs[:50], "total": total, "cached": False}


# ============ 标签详情 ============


@router.post("/{tag_id}/inspirations/batch-remove", status_code=status.HTTP_200_OK)
async def batch_remove_tag_inspirations(
    tag_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """批量解除标签与多个素材的关联。

    请求体: {"inspiration_ids": ["uuid1", "uuid2", ...]}
    """
    inspiration_ids = payload.get("inspiration_ids", [])
    if not isinstance(inspiration_ids, list) or not inspiration_ids:
        raise HTTPException(status_code=400, detail="请提供素材 ID 列表")

    removed = await tag_service.batch_remove_tag_inspirations(db, tag_id, inspiration_ids)
    return {"removed": removed}


@router.get("/{tag_id}/inspirations")
async def tag_inspirations(
    tag_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    sort: str = "newest",  # newest | oldest | confidence
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取使用指定标签的素材列表。"""
    result = await tag_service.list_tag_inspirations(db, tag_id, page, size, sort)
    if not result:
        raise HTTPException(status_code=404, detail="标签未找到")
    return result


# ============ 导入/导出 ============


@router.get("/export")
async def export_tags(db: AsyncSession = Depends(get_db)) -> dict:
    """导出所有标签为 JSON（含类别、来源、使用次数）。"""
    from datetime import datetime, timezone
    export_data = await tag_service.export_tags(db)
    return {"tags": export_data, "exported_at": datetime.now(timezone.utc).isoformat()}


@router.post("/import", status_code=status.HTTP_200_OK)
async def import_tags(
    data: TagImportRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """批量导入标签（跳过已存在的标签）。"""
    imported, skipped = await tag_service.import_tags(
        db, [(item.name, item.category) for item in data.tags]
    )
    return {
        "message": f"已导入 {imported} 个标签，跳过 {skipped} 个已存在",
        "imported": imported,
        "skipped": skipped,
    }


# ============ 自定义排序 ============


@router.post("/reorder", status_code=status.HTTP_200_OK)
async def reorder_tags(
    data: TagReorderRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """批量更新标签自定义排序权重（sort_order 越小越靠前）。"""
    if not data.items:
        raise HTTPException(status_code=400, detail="请提供排序项")

    order_map = {item.id: item.sort_order for item in data.items}
    updated, missing_ids = await tag_service.reorder_tags(db, order_map)
    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail=f"以下标签不存在，无法排序: {missing_ids}",
        )
    return {"updated": updated}


# ============ 别名管理 ============


@router.get("/aliases", status_code=status.HTTP_200_OK)
async def list_aliases(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """获取所有标签别名（含所属标签名）。"""
    return await tag_service.list_aliases(db)


@router.post("/{tag_id}/aliases", response_model=AliasOut, status_code=status.HTTP_201_CREATED)
async def create_alias(
    tag_id: int, data: AliasCreate, db: AsyncSession = Depends(get_db)
) -> TagAlias:
    """为标签添加别名（将别名归一化到该标签）。"""
    alias = data.alias.strip()
    if not alias:
        raise HTTPException(status_code=400, detail="别名为空")

    try:
        obj = await tag_service.create_alias(db, tag_id, alias)
    except tag_service.TagNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except tag_service.TagConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)
    return obj


@router.delete("/aliases/{alias_id}", status_code=status.HTTP_200_OK)
async def delete_alias(alias_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """删除标签别名。"""
    if not await tag_service.delete_alias(db, alias_id):
        raise HTTPException(status_code=404, detail="别名未找到")
    return {"message": "已删除别名"}


# ============ 操作历史 ============


@router.get("/history", status_code=status.HTTP_200_OK)
async def tag_history_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    operation: str | None = Query(
        None,
        pattern="^(create|rename|category_change|update|move|merge|alias_add|alias_remove|batch_edit|delete)$",
    ),
    tag_id: int | None = Query(None, ge=1),
    batch_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页查询标签操作历史（按时间倒序），支持按操作类型 / 标签 / 批次过滤。"""
    from app.services.tag_history_service import list_history

    return await list_history(
        db, page=page, size=size, operation=operation, tag_id=tag_id, batch_id=batch_id
    )


@router.post("/history/{history_id}/rollback", status_code=status.HTTP_200_OK)
async def rollback_tag_history(
    history_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    """回滚一条标签操作（单条操作级；标签已被后续修改时返回 409 冲突）。"""
    from app.services.tag_history_service import (
        TagHistoryNotFoundError,
        TagHistoryRollbackError,
        rollback_history,
    )

    try:
        return await rollback_history(db, history_id)
    except TagHistoryNotFoundError:
        raise HTTPException(status_code=404, detail="历史记录未找到")
    except TagHistoryRollbackError as e:
        raise HTTPException(status_code=409, detail=e.message)


# ============ 健康度扫描 ============


@router.post("/health/scan", status_code=status.HTTP_200_OK)
async def tag_health_scan(
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """提交标签健康度扫描任务（异步执行，返回 task_id 供轮询进度）。

    请求体可选: {"duplicate_threshold": 0.75}（疑似重复相似度阈值）
    """
    from app.services.task_runner import create_tag_health_scan_task

    threshold = float((payload or {}).get("duplicate_threshold", 0.75))
    if not (0.6 <= threshold <= 0.95):
        raise HTTPException(status_code=400, detail="duplicate_threshold 需在 0.6 ~ 0.95 之间")
    task = await create_tag_health_scan_task(db, duplicate_threshold=threshold)
    return {"message": f"已提交健康度扫描任务 #{task.id}", "task_id": task.id}


@router.get("/health/{issue_type}", status_code=status.HTTP_200_OK)
async def tag_health_issues(
    issue_type: str,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取最近一次健康度扫描的问题明细（分页）。

    issue_type: orphan | low_frequency | low_quality_name | duplicate
    """
    from sqlalchemy import select

    from app.models.task import TaskQueue
    from app.services.tag_health import get_health_issue_detail

    latest = await db.execute(
        select(TaskQueue)
        .where(TaskQueue.type == "tag_health_scan", TaskQueue.status == "success")
        .order_by(TaskQueue.id.desc())
        .limit(1)
    )
    task = latest.scalar_one_or_none()
    if task is None or not task.result:
        raise HTTPException(status_code=404, detail="尚未完成健康度扫描，请先提交扫描任务")

    try:
        return await get_health_issue_detail(db, task.result, issue_type, page, size)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============ 自动聚类 ============


@router.post("/clusters/scan", status_code=status.HTTP_200_OK)
async def tag_clusters_scan(
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """提交自动聚类扫描任务（异步执行，返回 task_id 供轮询进度）。

    请求体可选: {"threshold": 0.75, "use_cooccurrence_boost": true, "min_group_size": 2}
    """
    from app.services.task_runner import create_tag_cluster_scan_task

    payload = payload or {}
    threshold = float(payload.get("threshold", 0.75))
    if not (0.6 <= threshold <= 0.95):
        raise HTTPException(status_code=400, detail="threshold 需在 0.6 ~ 0.95 之间")
    min_group_size = int(payload.get("min_group_size", 2))
    if min_group_size < 2:
        raise HTTPException(status_code=400, detail="min_group_size 至少为 2")
    task = await create_tag_cluster_scan_task(
        db,
        threshold=threshold,
        use_cooccurrence_boost=bool(payload.get("use_cooccurrence_boost", True)),
        min_group_size=min_group_size,
    )
    return {"message": f"已提交聚类任务 #{task.id}", "task_id": task.id}


@router.post("/clusters/apply", status_code=status.HTTP_200_OK)
async def tag_clusters_apply(
    data: ClusterApplyRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """应用选中的候选组：组内合并（可选保留源名为别名），全部写操作历史。

    每个组可传 group_id（从最近一次聚类扫描结果解析成员），
    或直接传 target_tag_id + source_tag_ids（不依赖扫描结果）。
    """
    from sqlalchemy import select

    from app.models.task import TaskQueue
    from app.services.tag_cluster import apply_tag_clusters

    groups = [g.model_dump() for g in data.groups]

    # 仅传 group_id 的组：从最近一次成功的聚类扫描结果解析成员
    unresolved = [g for g in groups if g.get("target_tag_id") is None]
    if unresolved:
        latest = await db.execute(
            select(TaskQueue)
            .where(TaskQueue.type == "tag_cluster_scan", TaskQueue.status == "success")
            .order_by(TaskQueue.id.desc())
            .limit(1)
        )
        task = latest.scalar_one_or_none()
        if task is None or not task.result:
            raise HTTPException(
                status_code=400,
                detail="请先完成聚类扫描，或直接指定 target_tag_id 与 source_tag_ids",
            )
        group_map = {g["id"]: g for g in task.result.get("groups", [])}
        for g in unresolved:
            src = group_map.get(g.get("group_id"))
            if not src:
                raise HTTPException(status_code=400, detail=f"候选组 {g.get('group_id')} 不存在")
            target = src["suggested_target"]
            g["target_tag_id"] = target["id"]
            g["source_tag_ids"] = [
                m["id"] for m in src["members"] if m["id"] != target["id"]
            ]

    return await apply_tag_clusters(db, groups, batch_id=data.batch_id)


# ============ 网络图分析 ============


@router.post("/network/analyze", status_code=status.HTTP_200_OK)
async def tag_network_analyze(
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """提交网络图分析任务（异步执行，返回 task_id 供轮询进度）。

    请求体可选: {"limit": 100, "min_count": 2, "category": null,
                "with_communities": true, "with_centrality": true,
                "max_edges_per_node": 10}（每节点保留权重最高的 N 条边，
                缓解全连接稠密图的「网格状」显示；0 表示不剪枝）
    """
    from app.services.task_runner import create_tag_network_analyze_task

    payload = payload or {}
    task = await create_tag_network_analyze_task(
        db,
        limit=int(payload.get("limit", 100)),
        min_count=int(payload.get("min_count", 2)),
        category=payload.get("category"),
        with_communities=bool(payload.get("with_communities", True)),
        with_centrality=bool(payload.get("with_centrality", True)),
        max_edges_per_node=int(payload.get("max_edges_per_node", 0)),
    )
    return {"message": f"已提交图分析任务 #{task.id}", "task_id": task.id}


# ============ 批量高级编辑 ============


@router.post("/batch-edit", status_code=status.HTTP_200_OK)
async def tag_batch_edit(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """批量高级编辑：正则查找替换 / 前后缀增删 / 格式归一化 / 正则批量合并。

    请求体::
        {
            "dry_run": true,
            "rules": [
                {"type": "regex_replace", "pattern": "...", "replacement": "...",
                 "scope": {"tag_ids": [1, 2]}},
                {"type": "affix", "mode": "add_suffix", "text": "风",
                 "scope": {"category": "style"}},
                {"type": "normalize", "ops": ["fullwidth_to_halfwidth", "trim"],
                 "scope": {"source": "ai_generated"}},
                {"type": "regex_merge", "pattern": "^(.+)毛衣$", "target_template": "$1",
                 "scope": {"search": "毛衣"}},
            ],
        }

    dry_run=true 返回逐条预览（不落库）；dry_run=false 执行并写操作历史
    （同批次共享 batch_id）。冲突策略：新名已存在时自动合并到该目标标签。
    """
    from app.services.tag_batch_edit import batch_edit_tags

    rules = payload.get("rules") or []
    dry_run = bool(payload.get("dry_run", True))
    if not rules:
        raise HTTPException(status_code=400, detail="请至少提供一条规则")
    try:
        return await batch_edit_tags(db, rules, dry_run=dry_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============ 颜色剥离治理 ============


@router.post("/color-strip/dry-run", status_code=status.HTTP_200_OK)
async def tag_color_strip_dry_run(
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """单品标签颜色剥离 dry-run：返回统计与样例，不写库。

    请求体可选: {"category": "item_type", "limit": 0}
    （category 参与治理的标签类别；limit 只处理前 N 个，0 表示全部）
    """
    from app.services.tag_color_strip import dry_run_color_strip

    payload = payload or {}
    category = str(payload.get("category", "item_type")).strip() or "item_type"
    limit = int(payload.get("limit", 0) or 0)
    return await dry_run_color_strip(db, category=category, limit=limit)


@router.post("/color-strip/apply", status_code=status.HTTP_200_OK)
async def tag_color_strip_apply(
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """单品标签颜色剥离执行：剥离颜色前缀重命名/合并并补建颜色关联（单事务）。

    请求体可选: {"category": "item_type", "limit": 0}
    建议先调用 dry-run 预览统计与样例后再执行。
    """
    from app.services.tag_color_strip import apply_color_strip

    payload = payload or {}
    category = str(payload.get("category", "item_type")).strip() or "item_type"
    limit = int(payload.get("limit", 0) or 0)
    return await apply_color_strip(db, category=category, limit=limit)


# ============ 层级树 ============


@router.get("/tree", status_code=status.HTTP_200_OK)
async def tag_tree(
    parent_id: int | None = Query(None, ge=1),
    page: int = Query(1, ge=1),
    size: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取层级树某一层的节点（懒加载）；parent_id 缺省/null 表示根节点。"""
    from app.services.tag_query import get_tag_tree_children

    return await get_tag_tree_children(db, parent_id, page, size)


@router.post("/move", status_code=status.HTTP_200_OK)
async def tag_move(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """批量移动标签层级。请求体: {"moves": [{"tag_id": 5, "parent_id": 12},
    {"tag_id": 7, "parent_id": null}]}（parent_id=null 表示移到根）。"""
    from app.services.tag_crud import move_tags

    moves = payload.get("moves") or []
    moved, errors = await move_tags(db, moves)
    return {"moved": moved, "errors": errors}


# ============ 使用效果分析 ============


@router.get("/effect/trending", status_code=status.HTTP_200_OK)
async def tag_effect_trending(
    days: int = Query(30, ge=1, le=365),
    top: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """热度升降榜：对比最近 days 天与前一 days 天的素材关联数。"""
    from app.services.tag_effect import get_trending_tags

    return await get_trending_tags(db, days=days, top=top)


@router.get("/effect/combinations", status_code=status.HTTP_200_OK)
async def tag_effect_combinations(
    limit: int = Query(20, ge=1, le=100),
    min_count: int = Query(2, ge=1),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """标签组合排行：活跃标签子集内两两共现计数（按次数降序）。"""
    from app.services.tag_effect import get_tag_combinations

    return await get_tag_combinations(db, limit=limit, min_count=min_count)


@router.get("/effect/coverage", status_code=status.HTTP_200_OK)
async def tag_effect_coverage(db: AsyncSession = Depends(get_db)) -> dict:
    """覆盖度统计：素材带标签比例、单素材平均标签数、按类别覆盖率。"""
    from app.services.tag_effect import get_tag_coverage

    return await get_tag_coverage(db)


@router.get("/effect/source_dist", status_code=status.HTTP_200_OK)
async def tag_effect_source_dist(db: AsyncSession = Depends(get_db)) -> dict:
    """标签来源分布：每来源标签数/总使用/平均使用 + Top 低效 AI 标签。"""
    from app.services.tag_effect import get_tag_source_dist

    return await get_tag_source_dist(db)


# ============ 共现网络与使用趋势 ============


@router.get("/cooccurrence-network", status_code=status.HTTP_200_OK)
async def cooccurrence_network(
    limit: int = Query(30, ge=2, le=100),
    min_count: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """返回使用次数 top-N 标签之间的共现网络（节点 + 加权边）。"""
    return await tag_service.get_cooccurrence_network(db, limit, min_count)


@router.get("/top", status_code=status.HTTP_200_OK)
async def top_tags(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """返回使用次数最多的标签排行。"""
    return await tag_service.get_top_tags(db, limit)


@router.get("/{tag_id}/trend", status_code=status.HTTP_200_OK)
async def tag_trend(
    tag_id: int,
    granularity: str = Query("month", pattern="^(month|week|day)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取标签的使用趋势（按素材创建时间分桶统计）。"""
    result = await tag_service.get_tag_trend(db, tag_id, granularity)
    if not result:
        raise HTTPException(status_code=404, detail="标签未找到")
    return result
