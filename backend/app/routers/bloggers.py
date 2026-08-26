"""穿搭博主管理 REST API。

与模特（models）已物理拆分：博主拥有平台主页/小红书号/CSV 导入等能力。
路由声明顺序注意：``/top``、``/suggestions``、``/import-csv`` 必须位于
``/{blogger_id}`` 之前，否则会被单段动态路由吞掉。
"""

import json

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
from app.services.blogger_face import get_blogger_face_status, register_blogger_face
from app.services.face_thumbnail import (
    delete_face_thumbnail,
    ensure_blogger_face_thumbnail,
    ensure_blogger_face_thumbnails,
)

router = APIRouter(prefix="/api/bloggers", tags=["bloggers"])


@router.get("", response_model=BloggerListOut)
async def list_bloggers(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    search: str | None = Query(None, description="名称模糊搜索"),
    platform: str | None = Query(None, description="平台筛选"),
    sort: str = Query("newest", pattern="^(newest|name|count)$"),
    # 人脸检测约束：仅保留已注册人脸库的博主（确保只匹配候选人脸库内的人）
    face_registered_only: bool = Query(False, description="仅返回已注册人脸库的博主"),
    # 人物组折叠（方案 B）：同组只返回主记录，组内账号放 group_members；
    # 仅在未指定平台时生效（平台筛选是单平台视角，保持平铺）
    grouped: bool = Query(True, description="按人物组折叠（同组只显示主账号）"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页获取博主列表，支持名称搜索、平台筛选与人脸检测约束。

    人脸检测约束：
    - face_registered_only=true：仅返回已注册人脸库的博主，确保人脸检测
      只匹配候选人脸库内的人；防止将库外人物误匹配

    人物组折叠（方案 B）：
    - grouped=true（默认）：同组只返回主账号（素材数最多者或手动指定），
      组内其余账号在 group_members 中，供前端展开显示与多平台徽标；
    - 平台筛选时自动平铺（同组该平台账号各自显示）。
    """
    items, total = await blogger_service.list_items(
        db,
        page=page,
        size=size,
        search=search,
        platform=platform,
        sort=sort,
        face_registered_only=face_registered_only,
        grouped=grouped,
    )
    # 批量补齐人脸缩略图（一次查询候选检测 + 缺失缓存裁剪），返回 face_thumb_path
    thumbs = await ensure_blogger_face_thumbnails(db, [i["id"] for i in items])
    for item in items:
        item["face_thumb_path"] = thumbs.get(item["id"])
        for member in item.get("group_members") or []:
            member["face_thumb_path"] = thumbs.get(member["id"])

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


@router.post("/enrich-missing-profile", status_code=status.HTTP_201_CREATED)
async def enrich_missing_profile(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建「博主主页信息补全」异步任务（人物管理页一键补全）。

    body: {"blogger_ids": [1, 2]}（可选；缺省 = 全部缺失主页信息的小红书博主）
    缺失定义：profile_url 或 platform_user_id 为空。
    任务执行：本地互推（URL↔ID）优先，缺失时按小红书号搜索用户匹配。
    返回 task_id；进度/明细通过任务接口轮询。
    """
    blogger_ids = body.get("blogger_ids")
    if blogger_ids is not None and (
        not isinstance(blogger_ids, list)
        or not all(isinstance(i, int) for i in blogger_ids)
    ):
        raise HTTPException(status_code=422, detail="blogger_ids 必须为整数数组")
    from app.services.task_runners.enrich_blogger_profile import (
        MAX_ENRICH_PER_TASK,
        create_enrich_blogger_profile_task,
    )

    task, total = await create_enrich_blogger_profile_task(db, blogger_ids)
    if task is None:
        raise HTTPException(
            status_code=400,
            detail="没有缺失主页信息的小红书博主可补全",
        )
    truncated = total > MAX_ENRICH_PER_TASK
    return {
        "task_id": task.id,
        "total": total,
        "truncated": truncated,
        "message": (
            f"本次将补全 {total} 位博主"
            + ("（超过单次上限，其余请完成后再发起）" if truncated else "")
        ),
    }


@router.get("/missing-profile")
async def missing_profile_bloggers(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """缺失主页信息的小红书博主列表（人物管理页补全功能用）。

    缺失定义：profile_url 或 platform_user_id 为空。
    """
    from app.services.blogger_enrichment_service import (
        list_missing_profile_bloggers,
    )

    bloggers = await list_missing_profile_bloggers(db)
    return {
        "items": [
            {"id": b.id, "name": b.name, "xhs_id": b.xhs_id}
            for b in bloggers
        ],
        "total": len(bloggers),
    }


@router.post("/enrich-skip")
async def enrich_skip(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """手动跳过补全（确定性无法获取信息的博主，从缺失列表移除）。

    body: {"blogger_ids": [1, 2], "reason": "可选跳过原因"}
    """
    blogger_ids = body.get("blogger_ids")
    if not isinstance(blogger_ids, list) or not all(
        isinstance(i, int) for i in blogger_ids
    ):
        raise HTTPException(status_code=422, detail="blogger_ids 必须为整数数组")
    from app.services.blogger_enrichment_service import mark_skipped

    count = await mark_skipped(db, blogger_ids, str(body.get("reason") or "手动跳过"))
    return {"skipped": count}


@router.post("/enrich-unskip")
async def enrich_unskip(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """解除跳过（博主重新纳入补全范围）。

    body: {"blogger_ids": [1, 2]}
    """
    blogger_ids = body.get("blogger_ids")
    if not isinstance(blogger_ids, list) or not all(
        isinstance(i, int) for i in blogger_ids
    ):
        raise HTTPException(status_code=422, detail="blogger_ids 必须为整数数组")
    from app.services.blogger_enrichment_service import unskip

    count = await unskip(db, blogger_ids)
    return {"unskipped": count}


@router.get("/enrich-skips")
async def enrich_skips(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """已跳过补全的博主列表（含原因，供前端管理与解除）。"""
    from app.services.blogger_enrichment_service import list_skipped

    items = await list_skipped(db)
    return {"items": items, "total": len(items)}


@router.get("/stats")
async def blogger_stats(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """博主数量统计：总数 + 各平台分布（穿搭博主页顶部统计卡片）。"""
    return await blogger_service.platform_stats(db)


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
    items = await blogger_service.top(db, limit)
    thumbs = await ensure_blogger_face_thumbnails(db, [i["id"] for i in items])
    for item in items:
        item["face_thumb_path"] = thumbs.get(item["id"])
    return items


@router.get("/suggestions", response_model=list[BloggerOut])
async def suggest_bloggers(
    name: str = Query(..., min_length=1, description="名称模糊关键字"),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """按名称模糊匹配博主（用于前端选择去重）。"""
    items = await blogger_service.suggest(db, name)
    thumbs = await ensure_blogger_face_thumbnails(db, [i["id"] for i in items])
    for item in items:
        item["face_thumb_path"] = thumbs.get(item["id"])
    return items


# ── 人物组（方案 B）：同一现实人物跨平台账号绑定 ──
# 注意：/groups/* 静态段路由必须先于 /{blogger_id} 声明，否则会被单段
# 动态路由吞掉（blogger_id 解析为 "groups" → 422）。


@router.post("/groups/link", status_code=status.HTTP_200_OK)
async def link_bloggers_group_api(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """把两个博主绑定为同一人（新建组），或把博主并入已有组。

    请求体（二选一）:
        {"blogger_id": 1, "target_blogger_id": 2}   # 两账号组成新组
        {"blogger_id": 1, "group_id": 5}            # 并入已有组

    返回: {"group_id", "group_name", "primary_blogger_id", "member_ids"}
    """
    from app.services.person.person_groups import link_bloggers

    blogger_id = body.get("blogger_id")
    target_blogger_id = body.get("target_blogger_id")
    group_id = body.get("group_id")
    if not isinstance(blogger_id, int):
        raise HTTPException(status_code=422, detail="blogger_id 必须为整数")
    if target_blogger_id is not None and not isinstance(target_blogger_id, int):
        raise HTTPException(status_code=422, detail="target_blogger_id 必须为整数")
    if group_id is not None and not isinstance(group_id, int):
        raise HTTPException(status_code=422, detail="group_id 必须为整数")
    try:
        return await link_bloggers(db, blogger_id, target_blogger_id, group_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except PersonConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)


@router.post("/groups/unlink", status_code=status.HTTP_200_OK)
async def unlink_blogger_group_api(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """把博主移出人物组（变独立账号）；组内仅剩 1 个账号时自动清理组。

    请求体: {"blogger_id": 1}
    """
    from app.services.person.person_groups import unlink_blogger

    blogger_id = body.get("blogger_id")
    if not isinstance(blogger_id, int):
        raise HTTPException(status_code=422, detail="blogger_id 必须为整数")
    try:
        return await unlink_blogger(db, blogger_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except PersonConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)


@router.post("/groups/{group_id}/set-primary", status_code=status.HTTP_200_OK)
async def set_primary_blogger_api(
    group_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """手动指定组内主账号（列表折叠展示位）。

    请求体: {"blogger_id": 1}
    """
    from app.services.person.person_groups import set_primary_blogger

    blogger_id = body.get("blogger_id")
    if not isinstance(blogger_id, int):
        raise HTTPException(status_code=422, detail="blogger_id 必须为整数")
    try:
        return await set_primary_blogger(db, group_id, blogger_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except PersonConflictError as e:
        raise HTTPException(status_code=409, detail=e.message)


@router.get("/groups/{group_id}", status_code=status.HTTP_200_OK)
async def group_info_api(
    group_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """查询人物组完整信息（组内各账号 + 主账号）。"""
    from app.services.person.person_groups import get_group_info

    try:
        return await get_group_info(db, group_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


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
    base["face_thumb_path"] = await ensure_blogger_face_thumbnail(db, blogger_id)
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
    # 清理人脸缩略图缓存（博主已删，避免残留孤儿文件）
    delete_face_thumbnail(blogger_id)


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


@router.post("/{blogger_id}/generate-bio")
async def generate_blogger_bio(
    blogger_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """根据该博主的标签画像调用本地大模型生成一段简介（不自动入库，由前端确认后保存）。"""
    from app.routers._person_bio import generate_person_bio_endpoint

    return await generate_person_bio_endpoint(db, blogger_service, blogger_id)


# ── 博主人脸注册（素材人脸自动匹配依赖此特征库；职业模特无此人脸能力）──


@router.post("/{blogger_id}/face")
async def register_blogger_face_api(
    blogger_id: int,
    files: list[UploadFile] = File(None, description="博主正脸照片（1~5 张，可选）"),
    inspiration_ids: str | None = Form(
        None, description='已关联素材 ID 列表，JSON 数组字符串，如 "[uuid1,uuid2]"'
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """注册/重新注册博主人脸：上传照片与/或选择已关联素材（合计 1~5 张）。

    两种来源可同时提供，也可只提供一种；素材文件缺失/读取失败自动跳过并
    在返回的 warnings 中提示。
    """
    image_bytes_list = []
    for f in files or []:
        data = await f.read()
        if data:
            image_bytes_list.append(data)

    ids: list[str] = []
    if inspiration_ids:
        try:
            parsed = json.loads(inspiration_ids)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422, detail="inspiration_ids 格式错误：应为 JSON 数组"
            )
        if not isinstance(parsed, list):
            raise HTTPException(
                status_code=422, detail="inspiration_ids 格式错误：应为 JSON 数组"
            )
        ids = [str(i) for i in parsed if str(i).strip()]

    return await register_blogger_face(db, blogger_id, image_bytes_list, ids)


@router.get("/{blogger_id}/face")
async def blogger_face_status_api(
    blogger_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """查询博主人脸注册状态。"""
    return await get_blogger_face_status(db, blogger_id)
