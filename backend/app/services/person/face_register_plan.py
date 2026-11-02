"""批量注册候选计划 + 批量注册执行。

对应方案：``docs/features/人脸注册与候选审核方案.md`` 阶段 1。

为什么单独成模块：
- ``list_face_candidates`` 是**单博主**口径（人物详情页打开一次算一次），批量页面对
  484 位博主不能逐个全量聚类（实测单人大簇 0.1~0.3s，全部跑完 20s+）。这里先用一条
  聚合 SQL 找出「未匹配单脸检测 ≥ 阈值」的博主，再只对这些博主做聚类，避免空跑；
- 计划接口是**只读**的：UI 先看规模与封面，用户点「采用」才走批量注册；注册最多
  20 位/批，由前端循环，不新增任务类型（避免动 TASK_HANDLERS 与移动端文案）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.face import BloggerFaceEmbedding, InspirationFaceDetection
from app.models.inspiration import Inspiration
from app.config import settings
from app.models.person import Blogger, InspirationBlogger
from app.services.blogger_face import MIN_FACE_WIDTH_RATIO, register_blogger_face
from app.services.person.face_candidates import list_face_candidates

logger = logging.getLogger(__name__)

# 单批最多注册人数（HTTP 单次 ≤5 位；前端按 5 位循环，避免 30s 超时）
MAX_BATCH_SIZE = 5
# 单个候选计划里每位博主最多返回几个簇（前端「换一组」轮换）
MAX_CLUSTERS_PER_BLOGGER = 5


def _face_width_ratio(candidate: dict) -> float | None:
    """估算候选素材的人脸宽度占比（与注册服务的质量门槛同口径）。

    bbox 是送检图坐标系；用缩略图或原图宽度即可还原比例（整图等比缩放不改变比例）。
    文件读不到/bbox 缺失返回 None（视为不可用于注册，避免点了才报错）。
    """
    bbox = candidate.get("bbox")
    # 注册服务对图片用原图算比例、对视频用首帧缩略图算；这里必须同口径：
    # 用缩略图宽度算图片会得到虚高的比例（实测 17% vs 实际 4.8%）而漏过滤。
    if str(candidate.get("media_type") or "") == "video":
        raw = candidate.get("thumbnail_path") or candidate.get("file_path")
    else:
        raw = candidate.get("file_path") or candidate.get("thumbnail_path")
    if not bbox or not raw:
        return None
    try:
        values = json.loads(bbox) if isinstance(bbox, str) else bbox
        if not isinstance(values, list) or len(values) != 4:
            return None
        path = Path(settings.storage_root) / str(raw)
        if not path.exists():
            return None
        with Image.open(path) as image:
            width = float(image.size[0])
        if width <= 0:
            return None
        return (float(values[2]) - float(values[0])) / width
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("_face_width_ratio: 忽略异常（预期降级）：%s", exc)
        return None


async def build_register_plan(
    db: AsyncSession,
    *,
    limit: int = 200,
    min_cluster_size: int = 3,
    min_det_score: float = 0.5,
    only_with_candidates: bool = True,
) -> dict:
    """构造「有素材但未注册」博主的批量注册候选计划（只读、不写库）。

    Args:
        db: 数据库会话。
        limit: 返回条目上限（完整统计放在 summary 里）。
        min_cluster_size: 单簇最少出现次数。
        min_det_score: 参与聚类的检测置信度下限。
        only_with_candidates: 只返回有候选的博主；为 False 时无候选博主带 skip_reason。

    Returns:
        ``{"items": [...], "summary": {...}}``；items 按「最大簇 size × 平均 det_score」
        降序，每项含 clusters（最多 5 个）供前端「换一组」。
    """
    registered_subq = select(BloggerFaceEmbedding.blogger_id)

    # 全部「有素材、未注册」的博主（含无候选的，用于 summary 与 no_candidates 列表）
    total_rows = (
        await db.execute(
            select(
                Blogger.id,
                Blogger.name,
                Blogger.avatar_path,
                func.count(InspirationBlogger.inspiration_id).label("inspiration_count"),
            )
            .join(InspirationBlogger, InspirationBlogger.blogger_id == Blogger.id)
            .where(Blogger.id.not_in(registered_subq))
            .group_by(Blogger.id, Blogger.name, Blogger.avatar_path)
            .order_by(func.count(InspirationBlogger.inspiration_id).desc())
        )
    ).all()
    unregistered = {
        int(row[0]): {
            "name": row[1] or f"博主 #{row[0]}",
            "avatar_path": row[2],
            "inspiration_count": int(row[3] or 0),
        }
        for row in total_rows
    }

    # 预筛：只保留「未匹配单脸检测 ≥ min_cluster_size」的博主，避免对无候选者做聚类
    single_face_subq = (
        select(InspirationFaceDetection.inspiration_id)
        .group_by(InspirationFaceDetection.inspiration_id)
        .having(func.count() == 1)
    )
    candidate_rows = (
        await db.execute(
            select(
                InspirationBlogger.blogger_id,
                func.count(func.distinct(InspirationFaceDetection.id)).label("candidate_faces"),
            )
            .join(
                InspirationFaceDetection,
                InspirationFaceDetection.inspiration_id == InspirationBlogger.inspiration_id,
            )
            .join(Inspiration, Inspiration.id == InspirationFaceDetection.inspiration_id)
            .where(
                InspirationBlogger.blogger_id.in_(list(unregistered)),
                Inspiration.deleted_at.is_(None),
                InspirationFaceDetection.matched_blogger_id.is_(None),
                InspirationFaceDetection.matched_model_id.is_(None),
                # IS NOT TRUE：历史行可能为 NULL，IS FALSE 会把它们全排掉
                InspirationFaceDetection.match_excluded.is_not(True),
                InspirationFaceDetection.det_score >= min_det_score,
                InspirationFaceDetection.inspiration_id.in_(single_face_subq),
            )
            .group_by(InspirationBlogger.blogger_id)
            .having(func.count(func.distinct(InspirationFaceDetection.id)) >= min_cluster_size)
        )
    ).all()
    candidate_counts = {int(row[0]): int(row[1]) for row in candidate_rows}

    items: list[dict] = []
    for blogger_id in candidate_counts:
        profile = unregistered[blogger_id]
        result = await list_face_candidates(
            db,
            blogger_id,
            min_det_score=min_det_score,
            min_cluster_size=min_cluster_size,
            max_clusters=MAX_CLUSTERS_PER_BLOGGER,
        )
        clusters = []
        for c in result["clusters"]:
            # 只保留「人脸宽度 ≥ 注册质量门槛」的素材：注册服务会拒绝 5% 以下的人脸，
            # 计划阶段先过滤，避免用户点了「采用」才看到「所有图片均未检出清晰人脸」。
            usable = []
            for cand in c.get("candidates") or []:
                ratio = _face_width_ratio(cand)
                if ratio is None or ratio < MIN_FACE_WIDTH_RATIO:
                    continue
                cand = dict(cand)
                cand["face_width_ratio"] = round(ratio, 4)
                usable.append(cand)
            if not usable:
                continue
            # 按置信度排序后取前 3 张（候选列表本身已按 det_score 降序）
            suggested: list[str] = []
            for cand in usable:
                iid = str(cand.get("inspiration_id") or "")
                if iid and iid not in suggested:
                    suggested.append(iid)
                if len(suggested) >= 3:
                    break
            clusters.append(
                {
                    "size": int(c["size"]),
                    "avg_det_score": float(c.get("avg_score") or 0.0),
                    "suggested_inspiration_ids": suggested,
                    "cover_detection_ids": [
                        int(cand["detection_id"])
                        for cand in usable
                        if cand.get("detection_id") is not None
                    ],
                    "candidates": usable[:3],
                }
            )
        if not clusters:
            continue
        item = {
            "person_type": "blogger",
            "person_id": blogger_id,
            "name": profile["name"],
            "avatar_path": profile["avatar_path"],
            "inspiration_count": profile["inspiration_count"],
            "registered": False,
            "clusters": clusters,
            "skip_reason": None,
        }
        item["_rank"] = max(c["size"] * max(c["avg_det_score"], 0.01) for c in clusters)
        items.append(item)

    with_candidates = len(items)
    items.sort(key=lambda item: item["_rank"], reverse=True)
    for item in items:
        item.pop("_rank", None)

    if not only_with_candidates:
        for blogger_id, profile in unregistered.items():
            if blogger_id in candidate_counts:
                continue
            items.append(
                {
                    "person_type": "blogger",
                    "person_id": blogger_id,
                    "name": profile["name"],
                    "avatar_path": profile["avatar_path"],
                    "inspiration_count": profile["inspiration_count"],
                    "registered": False,
                    "clusters": [],
                    "skip_reason": "素材里没有稳定人脸（单脸未匹配检测不足 3 次）",
                }
            )

    return {
        "items": items[: max(0, limit)],
        "summary": {
            "total": len(unregistered),
            "with_candidates": with_candidates,
            "no_candidates": len(unregistered) - with_candidates,
            "min_cluster_size": min_cluster_size,
            "min_det_score": min_det_score,
        },
    }


async def register_bloggers_batch(db: AsyncSession, items: list) -> dict:
    """逐位执行注册（串行，最多 20 位/批）；子服务不可用时快速失败。

    Args:
        db: 数据库会话。
        items: ``FaceRegisterBatchItem`` 列表（person_type/person_id/inspiration_ids）。

    Returns:
        ``{"items": [{person_id, ok, face_count, error}], "succeeded": N, "failed": M}``。
    """
    results: list[dict] = []
    service_down = False
    for item in items:
        person_id = int(item.person_id)
        if service_down:
            results.append(
                {
                    "person_id": person_id,
                    "ok": False,
                    "face_count": 0,
                    "error": "人脸识别子服务不可用，本批剩余未执行",
                }
            )
            continue
        if item.person_type != "blogger":
            results.append(
                {"person_id": person_id, "ok": False, "face_count": 0, "error": "暂只支持博主注册"}
            )
            continue
        inspiration_ids = [str(i).strip() for i in item.inspiration_ids if str(i).strip()][:5]
        if not inspiration_ids:
            results.append(
                {
                    "person_id": person_id,
                    "ok": False,
                    "face_count": 0,
                    "error": "至少选择 1 张素材",
                }
            )
            continue
        # 先校验素材确实属于该博主（register_blogger_face 内部会跳过不属于的素材，
        # 但那样会静默少注册；批量操作必须显式失败让用户知道）
        owned = (
            await db.execute(
                select(InspirationBlogger.inspiration_id).where(
                    InspirationBlogger.blogger_id == person_id,
                    InspirationBlogger.inspiration_id.in_(inspiration_ids),
                )
            )
        ).scalars().all()
        if set(owned) != set(inspiration_ids):
            results.append(
                {
                    "person_id": person_id,
                    "ok": False,
                    "face_count": 0,
                    "error": "所选素材不属于该博主，请刷新计划后重试",
                }
            )
            continue
        try:
            result = await register_blogger_face(db, person_id, [], inspiration_ids)
            results.append(
                {
                    "person_id": person_id,
                    "ok": True,
                    "face_count": int(result.get("photos_used") or 0),
                    "error": None,
                    "warnings": result.get("warnings") or [],
                }
            )
        except HTTPException as exc:
            error = str(exc.detail)
            results.append(
                {"person_id": person_id, "ok": False, "face_count": 0, "error": error}
            )
            if exc.status_code == 503:
                service_down = True
        except Exception as exc:  # noqa: BLE001 —— 单条失败不能中断整批
            logger.exception("批量注册失败 blogger_id=%s", person_id)
            results.append(
                {
                    "person_id": person_id,
                    "ok": False,
                    "face_count": 0,
                    "error": f"注册异常：{exc}",
                }
            )
    succeeded = sum(1 for r in results if r["ok"])
    return {
        "items": results,
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
    }
