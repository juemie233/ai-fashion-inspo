"""人脸库扫描 API：扫描/匹配任务、候选结果查询、人工审核确认。

流程（三层）：
1. 扫描任务（face_scan）：批量检测素材人脸落库（增量/全量）；
2. 候选匹配任务（face_match）：矩阵乘产出 pending 候选；
3. 审核确认（confirm/reject/undo）：唯一写人物关联表的入口。

结果查询按人物聚合（person_type/person_id/count/best_conf）分页，
明细按人物或「未匹配」过滤，均只含非占位记录（embedding 非空）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.face import InspirationFaceDetection
from app.models.inspiration import Inspiration
from app.models.person import Blogger, Model
from app.models.task import TaskQueue
from app.schemas.face_scan import (
    FaceClusterRunIn,
    FaceConfirmIn,
    FaceMatchRunIn,
    FaceScanStartIn,
)
from app.schemas.task import TaskOut
from app.services.face_cluster import load_group_detections
from app.services.person_service import model_service, blogger_service
from app.services.task_runners.face_cluster import create_face_cluster_task
from app.services.task_runners.face_scan import (
    create_face_match_task,
    create_face_scan_task,
)

router = APIRouter(prefix="/api/face-scan", tags=["face-scan"])
# 全库重匹配入口独立前缀（与设计文档 API 清单一致）
match_router = APIRouter(prefix="/api/face-match", tags=["face-match"])


# ── 任务创建与状态 ──


@router.post("/start", status_code=201)
async def start_scan(
    data: FaceScanStartIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建人脸库扫描任务（增量/全量），返回 task_id。"""
    task = await create_face_scan_task(
        db, scope=data.scope, auto_match=data.auto_match
    )
    return {"task_id": task.id, "total": task.total}


@match_router.post("/run", status_code=201)
async def run_match(
    data: FaceMatchRunIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建全库候选匹配任务（可限定人物范围），返回 task_id。"""
    person_ids: list[int] | None = None
    if data.person_id is not None:
        person_ids = [data.person_id]
    scope = data.scope
    if data.person_type == "blogger" and scope == "all":
        scope = "bloggers"
    elif data.person_type == "model" and scope == "all":
        scope = "models"
    task = await create_face_match_task(
        db, scope=scope, person_ids=person_ids, threshold=data.threshold
    )
    return {"task_id": task.id}


@router.get("/task")
async def scan_task(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """最近一次扫描/匹配任务状态（供扫描页轮询）。"""
    scan = (
        await db.execute(
            select(TaskQueue)
            .where(TaskQueue.type == "face_scan")
            .order_by(TaskQueue.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    match = (
        await db.execute(
            select(TaskQueue)
            .where(TaskQueue.type == "face_match")
            .order_by(TaskQueue.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "scan_task": TaskOut.model_validate(scan) if scan else None,
        "match_task": TaskOut.model_validate(match) if match else None,
    }


# ── 候选结果查询 ──


@router.get("/results")
async def scan_results(
    status: str = Query("pending", pattern="^(pending|confirmed)$"),
    person_type: str | None = Query(None, pattern="^(blogger|model)$"),
    person_id: int | None = None,
    unmatched: bool = False,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """查询候选/已确认结果。

    - 未指定 person_id：按人物聚合（person_type/person_id/名字/命中数/最高分），分页
    - 指定 person_id：该人物的命中素材明细分页
    - unmatched=true：未匹配人脸明细分页（忽略 person_type/person_id）
    """
    if unmatched:
        items, total = await _unmatched_page(db, status, page, size)
        return {"mode": "unmatched", "items": items, "total": total, "page": page, "size": size}
    if person_id is None:
        items, total = await _person_aggregate_page(db, status, person_type, page, size)
        return {"mode": "persons", "items": items, "total": total, "page": page, "size": size}
    resolved_type = person_type or await _person_type_of(db, person_id)
    items, total = await _person_detail_page(db, status, resolved_type, person_id, page, size)
    return {"mode": "detail", "items": items, "total": total, "page": page, "size": size}


async def _person_type_of(db: AsyncSession, person_id: int) -> str:
    """按 id 推断人物类型（博主/模特表分别查；都查不到抛 404）。"""
    if (await db.execute(select(Blogger.id).where(Blogger.id == person_id))).scalar_one_or_none():
        return "blogger"
    if (await db.execute(select(Model.id).where(Model.id == person_id))).scalar_one_or_none():
        return "model"
    raise HTTPException(status_code=404, detail="人物未找到")


async def _person_aggregate_page(
    db: AsyncSession, status: str, person_type: str | None, page: int, size: int
) -> tuple[list[dict], int]:
    """按人物聚合：命中数 + 最高置信度，分页（博主/模特混合，按 person_type 区分）。"""
    from sqlalchemy import case

    matched = InspirationFaceDetection.matched_blogger_id
    type_expr = case(
        (matched.is_not(None), "blogger"),
        else_="model",
    ).label("person_type")
    id_expr = func.coalesce(
        InspirationFaceDetection.matched_blogger_id,
        InspirationFaceDetection.matched_model_id,
    ).label("person_id")
    filters = [
        InspirationFaceDetection.match_status == status,
        InspirationFaceDetection.embedding != b"",
        # 人工「不匹配」的人脸不再显示在候选区
        InspirationFaceDetection.match_excluded.is_(False),
        InspirationFaceDetection.matched_blogger_id.is_not(None)
        | InspirationFaceDetection.matched_model_id.is_not(None),
    ]
    if person_type == "blogger":
        filters.append(InspirationFaceDetection.matched_blogger_id.is_not(None))
    elif person_type == "model":
        filters.append(InspirationFaceDetection.matched_model_id.is_not(None))

    base = (
        select(
            type_expr,
            id_expr,
            func.count().label("cnt"),
            func.max(InspirationFaceDetection.confidence).label("best_conf"),
        )
        .where(*filters)
        .group_by("person_type", "person_id")
        .subquery()
    )
    total = (await db.execute(select(func.count()).select_from(base))).scalar() or 0
    rows = (
        await db.execute(
            select(base.c.person_type, base.c.person_id, base.c.cnt, base.c.best_conf)
            .order_by(base.c.cnt.desc(), base.c.person_id)
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    items = []
    for p_type, person_id, cnt, best_conf in rows:
        name_model = Blogger if p_type == "blogger" else Model
        person = await db.get(name_model, person_id)
        items.append(
            {
                "person_type": p_type,
                "person_id": person_id,
                "name": person.name if person else f"已删除人物 #{person_id}",
                "count": cnt,
                "best_conf": round(best_conf, 4) if best_conf is not None else None,
            }
        )
    return items, total


async def _person_detail_page(
    db: AsyncSession, status: str, person_type: str, person_id: int, page: int, size: int
) -> tuple[list[dict], int]:
    """某人物命中素材明细分页（含缩略图路径）。"""
    col = (
        InspirationFaceDetection.matched_blogger_id
        if person_type == "blogger"
        else InspirationFaceDetection.matched_model_id
    )
    base = (
        select(
            InspirationFaceDetection.id.label("detection_id"),
            InspirationFaceDetection.inspiration_id,
            InspirationFaceDetection.confidence,
            Inspiration.file_path,
            Inspiration.thumbnail_path,
        )
        .join(Inspiration, Inspiration.id == InspirationFaceDetection.inspiration_id)
        .where(
            InspirationFaceDetection.match_status == status,
            InspirationFaceDetection.embedding != b"",
            # 人工「不匹配」的人脸不再显示在候选区
            InspirationFaceDetection.match_excluded.is_(False),
            col == person_id,
        )
        .subquery()
    )
    total = (await db.execute(select(func.count()).select_from(base))).scalar() or 0
    rows = (
        await db.execute(
            select(base).order_by(base.c.confidence.desc()).offset((page - 1) * size).limit(size)
        )
    ).all()
    items = [
        {
            "detection_id": r.detection_id,
            "inspiration_id": r.inspiration_id,
            "confidence": round(r.confidence, 4) if r.confidence is not None else None,
            "file_path": r.file_path,
            "thumbnail_path": r.thumbnail_path,
        }
        for r in rows
    ]
    return items, total


async def _unmatched_page(
    db: AsyncSession, status: str, page: int, size: int
) -> tuple[list[dict], int]:
    """未匹配人脸明细分页（按素材去重：同一素材多张人脸仅显示一条）。

    子查询按 inspiration_id 分组取最小 detection_id，外层关联素材路径。
    """
    subq = (
        select(
            InspirationFaceDetection.inspiration_id,
            func.min(InspirationFaceDetection.id).label("detection_id"),
        )
        .where(
            InspirationFaceDetection.match_status == status,
            InspirationFaceDetection.embedding != b"",
            # 人工「不匹配」的人脸不再显示在未匹配区域
            InspirationFaceDetection.match_excluded.is_(False),
            InspirationFaceDetection.matched_blogger_id.is_(None),
            InspirationFaceDetection.matched_model_id.is_(None),
        )
        .group_by(InspirationFaceDetection.inspiration_id)
        .subquery()
    )
    base = (
        select(
            subq.c.detection_id,
            subq.c.inspiration_id,
            Inspiration.file_path,
            Inspiration.thumbnail_path,
        )
        .join(Inspiration, Inspiration.id == subq.c.inspiration_id)
        .order_by(subq.c.inspiration_id)
        .offset((page - 1) * size)
        .limit(size)
        .subquery()
    )
    total = (
        await db.execute(
            select(func.count()).where(
                InspirationFaceDetection.match_status == status,
                InspirationFaceDetection.embedding != b"",
                # 人工「不匹配」的人脸不再显示在未匹配区域
                InspirationFaceDetection.match_excluded.is_(False),
                InspirationFaceDetection.matched_blogger_id.is_(None),
                InspirationFaceDetection.matched_model_id.is_(None),
            )
        )
    ).scalar() or 0
    rows = (
        await db.execute(select(base))
    ).all()
    items = [
        {
            "detection_id": r.detection_id,
            "inspiration_id": r.inspiration_id,
            "file_path": r.file_path,
            "thumbnail_path": r.thumbnail_path,
        }
        for r in rows
    ]
    return items, total


# ── 人工审核确认（唯一写人物关联表的入口）──


@router.post("/confirm")
async def confirm(
    data: FaceConfirmIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """审核确认 / 驳回 / 撤销候选。

    - confirm：pending 候选 → 幂等写入人物关联表（inspiration_bloggers/models）
      + matched_* 置为该人物 + match_status=confirmed（确认后即锁定，
      重复确认幂等跳过，不报错）
    - reject：pending 候选 → 清空匹配结果（回未匹配区）
    - undo：保留兼容；已确认记录已锁定，撤销一律跳过（不提供解锁）
    """
    ids = list(dict.fromkeys(item.detection_id for item in data.items))
    rows = await db.execute(
        select(InspirationFaceDetection).where(InspirationFaceDetection.id.in_(ids))
    )
    detections = {d.id: d for d in rows.scalars().all()}
    stats = {"confirmed": 0, "rejected": 0, "undone": 0, "skipped": 0}

    if data.action == "confirm":
        # 按 (素材, 人物) 分组，复用 link_batch 幂等写关联表
        blogger_groups: dict[str, list[int]] = {}
        model_groups: dict[str, list[int]] = {}
        valid: list[tuple[InspirationFaceDetection, str, int]] = []
        for item in data.items:
            det = detections.get(item.detection_id)
            if not det or det.match_status != "pending":
                stats["skipped"] += 1
                continue
            if item.person_type not in ("blogger", "model") or not item.person_id:
                stats["skipped"] += 1
                continue
            target = blogger_groups if item.person_type == "blogger" else model_groups
            target.setdefault(det.inspiration_id, []).append(item.person_id)
            valid.append((det, item.person_type, item.person_id))
        for insp_id, person_ids in blogger_groups.items():
            result = await blogger_service.link_batch(db, insp_id, person_ids)
            stats["skipped"] += result["skipped"] + len(result["missing_ids"])
        for insp_id, person_ids in model_groups.items():
            result = await model_service.link_batch(db, insp_id, person_ids)
            stats["skipped"] += result["skipped"] + len(result["missing_ids"])
        for det, person_type, person_id in valid:
            if person_type == "blogger":
                det.matched_blogger_id = person_id
                det.matched_model_id = None
            else:
                det.matched_model_id = person_id
                det.matched_blogger_id = None
            det.match_status = "confirmed"
        stats["confirmed"] = len(valid)
    elif data.action == "reject":
        for item in data.items:
            det = detections.get(item.detection_id)
            if not det or det.match_status != "pending":
                stats["skipped"] += 1
                continue
            det.matched_blogger_id = None
            det.matched_model_id = None
            det.confidence = None
            det.match_status = None
            # 持久化「不匹配」决定：该人脸不再参与后续全库匹配，
            # 也不再显示在候选/未匹配区域（下次扫描不会重新捞起它）
            det.match_excluded = True
            stats["rejected"] += 1
    else:  # undo
        # 锁定为单向操作：已确认记录不提供解锁，撤销一律跳过（保留接口兼容）
        for _item in data.items:
            stats["skipped"] += 1

    await db.commit()
    return {"action": data.action, **stats}


# ── 人脸聚合聚类（未匹配人脸按「疑似同一人」分组，供批量指派）──


@router.post("/cluster/run", status_code=201)
async def cluster_run(
    data: FaceClusterRunIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建人脸聚合聚类任务（异步执行），返回 task_id。

    聚类基于 hnswlib ANN（O(n log n)），把全部未匹配人脸按相似度
    聚合成「疑似同一人」的组；结果存入任务 result，供 groups 接口查询。
    """
    task = await create_face_cluster_task(
        db,
        threshold=data.threshold if data.threshold is not None else 0.5,
        min_group_size=data.min_group_size if data.min_group_size is not None else 2,
    )
    return {"task_id": task.id, "message": "人脸聚合聚类任务已创建"}


@router.get("/cluster/task")
async def cluster_task(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """最近一次人脸聚合聚类任务状态（供扫描页轮询进度与结果）。"""
    task = (
        await db.execute(
            select(TaskQueue)
            .where(TaskQueue.type == "face_cluster")
            .order_by(TaskQueue.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {"cluster_task": TaskOut.model_validate(task) if task else None}


@router.get("/cluster/groups")
async def cluster_groups(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页返回人脸聚合分组（来自最近一次成功的聚类任务结果）。

    每组返回：组内人脸数 + 代表性人脸（组内 det_score 最高者）的素材信息，
    供分组列表展示缩略图；组内明细分页由 detections 接口提供。

    注意：整组指派后，已确认的人脸会被过滤掉，该组可能变空或变小。
    """
    task = (
        await db.execute(
            select(TaskQueue)
            .where(TaskQueue.type == "face_cluster", TaskQueue.status == "success")
            .order_by(TaskQueue.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if task is None or not task.result:
        return {
            "task_status": None,
            "items": [],
            "total": 0,
            "page": page,
            "size": size,
            "summary": None,
        }
    result = task.result
    groups = result.get("groups") or []

    # 动态过滤：只返回还有未确认人脸的组
    filtered_groups = []
    for g in groups:
        detection_ids = g.get("detection_ids") or []
        if not detection_ids:
            continue
        # 检查组内是否还有未确认的人脸（matched_blogger_id 和 matched_model_id 都为空）
        unmatched_count = (
            await db.execute(
                select(func.count())
                .select_from(InspirationFaceDetection)
                .where(
                    InspirationFaceDetection.id.in_(detection_ids),
                    InspirationFaceDetection.matched_blogger_id.is_(None),
                    InspirationFaceDetection.matched_model_id.is_(None),
                )
            )
        ).scalar() or 0
        if unmatched_count > 0:
            filtered_groups.append(g)

    total = len(filtered_groups)
    start = (page - 1) * size
    page_groups = filtered_groups[start : start + size]

    # 每组取「最高置信度人脸」作为代表：批量拉取 det_score / 素材路径
    # 只查询未确认的人脸作为代表
    items: list[dict] = []
    for idx, g in enumerate(page_groups, start=start):
        detection_ids = g.get("detection_ids") or []
        if not detection_ids:
            continue
        rep_row = (
            await db.execute(
                select(
                    InspirationFaceDetection.id,
                    InspirationFaceDetection.det_score,
                    InspirationFaceDetection.inspiration_id,
                    Inspiration.file_path,
                    Inspiration.thumbnail_path,
                )
                .join(Inspiration, Inspiration.id == InspirationFaceDetection.inspiration_id)
                .where(
                    InspirationFaceDetection.id.in_(detection_ids),
                    InspirationFaceDetection.matched_blogger_id.is_(None),
                    InspirationFaceDetection.matched_model_id.is_(None),
                )
                .order_by(InspirationFaceDetection.det_score.desc())
                .limit(1)
            )
        ).first()
        items.append(
            {
                "group_id": idx,
                "size": g.get("size", len(detection_ids)),
                "detection_ids": detection_ids,
                "rep_detection_id": rep_row.id if rep_row else None,
                "rep_inspiration_id": rep_row.inspiration_id if rep_row else None,
                "rep_file_path": rep_row.file_path if rep_row else None,
                "rep_thumbnail_path": rep_row.thumbnail_path if rep_row else None,
            }
        )

    return {
        "task_status": task.status,
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "summary": {
            "total_faces": result.get("total_faces"),
            "method": result.get("method"),
            "group_count": result.get("group_count"),
            "clustered_faces": result.get("clustered_faces"),
            "singletons": result.get("singletons"),
            "threshold": result.get("threshold"),
        },
    }


@router.get("/cluster/groups/{group_id}/detections")
async def cluster_group_detections(
    group_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """某聚合组的人脸明细分页（含素材路径/缩略图，供展开网格展示与勾选指派）。

    group_id 是组在最近一次聚类结果中的下标（0 起）；通过最近成功任务解析。
    """
    task = (
        await db.execute(
            select(TaskQueue)
            .where(TaskQueue.type == "face_cluster", TaskQueue.status == "success")
            .order_by(TaskQueue.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if task is None or not task.result:
        raise HTTPException(status_code=404, detail="尚未执行人脸聚合聚类")
    groups = task.result.get("groups") or []
    if not 0 <= group_id < len(groups):
        raise HTTPException(status_code=404, detail="聚合组不存在")
    detection_ids = groups[group_id].get("detection_ids") or []
    items, total = await load_group_detections(db, detection_ids, page, size)
    return {
        "group_id": group_id,
        "items": items,
        "total": total,
        "page": page,
        "size": size,
    }
