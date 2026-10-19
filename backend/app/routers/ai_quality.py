"""AI 子路由：质量审核相关接口。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.inspiration import AIAnalysisLog, AIQualityReview, Inspiration
from app.routers.ai_shared import _update_env_file
from app.services.ollama_utils import is_ollama_running, start_ollama

router = APIRouter()


@router.get("/manual-upload-auto-approve")
async def get_manual_upload_auto_approve() -> dict[str, bool]:
    """获取手动上传素材是否默认免审核的配置。"""
    return {"enabled": settings.manual_upload_auto_approve}


@router.put("/manual-upload-auto-approve")
async def set_manual_upload_auto_approve(
    enabled: bool = Query(...),
    persist: bool = Query(True, description="是否持久化写入 .env 文件"),
) -> dict[str, str | bool]:
    """设置手动上传素材是否默认免审核。

    ``enabled=True`` 时，手动上传的素材直接标记为已通过，跳过质量审核队列；
    ``enabled=False`` 时恢复为待审核（pending）。
    """
    settings.manual_upload_auto_approve = enabled
    if persist:
        await _update_env_file(
            {"MANUAL_UPLOAD_AUTO_APPROVE": "true" if enabled else "false"}
        )
    return {
        "enabled": enabled,
        "message": "手动上传免审核已" + ("开启" if enabled else "关闭"),
    }


@router.post("/quality-check")
async def batch_quality_check(
    limit: int = Query(50, ge=1, le=5000),
    random: bool = Query(False, description="是否随机抽取素材（含已审查）"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    """批量审核图片素材。

    处理图片与视频素材（视频取首帧缩略图送审）；审核结果直接写回 quality_status。
    已改造为数据库驱动任务队列：创建任务记录后立即返回 task_id，
    由独立 worker 进程异步执行，前端轮询 GET /api/tasks/{task_id} 获取进度。

    参数:
        limit: 最多审核的素材数量。上限 5000——批量导入（如 f2 抖音素材）后常有
            上万条待审核，200 的上限需要点几十次；实测审核约 12.5 秒/张，
            5000 条在单并发下约 17 小时，任务支持在任务管理页**暂停/取消**
            （每批检查一次状态，已判定结果保留）。
        random: 为 True 时随机抽取 limit 个素材（含已审查，会覆盖重审）；
            为 False 时按默认顺序取前 limit 个待审核（pending）素材。
    """
    # 提交前检查 Ollama 是否运行
    if not await is_ollama_running():
        await start_ollama()

    # 随机复审抽取所有图片素材（含已审查），普通审核仅取 pending；均排除垃圾桶素材
    stmt = select(Inspiration.id).where(
        # 视频也参与审核（老 #1）：执行器会用首帧缩略图送审
        Inspiration.media_type.in_(("image", "video")),
        Inspiration.deleted_at.is_(None),
    )
    if random:
        stmt = stmt.order_by(func.random())
    else:
        stmt = stmt.where(Inspiration.quality_status == "pending")
    result = await db.execute(stmt.limit(limit))
    ids = [r[0] for r in result.all()]

    if not ids:
        return {"message": "没有可审核的素材", "count": 0}

    from app.services.task_runner import create_quality_check_task
    task = await create_quality_check_task(db, ids, random=random)

    return {
        "message": f"已提交 {len(ids)} 个素材进行质量审核",
        "count": len(ids),
        "task_id": task.id,
    }


@router.post("/quality-recheck")
async def recheck_quality(db: AsyncSession = Depends(get_db)) -> dict[str, str | int]:
    """重新审核所有已通过（approved）的图片素材。

    将 approved 重置为 pending 后提交任务队列批量审核，用最新审核标准重新判定。
    用于修正审核标准升级后历史素材的误判（如「只有腿部」被误判为通过）。
    """
    # 提交前检查 Ollama 是否运行
    if not await is_ollama_running():
        await start_ollama()

    result = await db.execute(
        update(Inspiration)
        .where(
            Inspiration.media_type == "image",
            Inspiration.quality_status == "approved",
            Inspiration.deleted_at.is_(None),
        )
        .values(quality_status="pending", quality_reason=None)
    )
    await db.commit()
    reset_count = result.rowcount

    if not reset_count:
        return {"message": "没有已通过的素材可重新审核", "count": 0}

    # 提交所有待审核素材（含刚重置的），由 worker 异步执行
    items_result = await db.execute(
        select(Inspiration.id).where(
            Inspiration.quality_status == "pending",
            Inspiration.media_type == "image",
            Inspiration.deleted_at.is_(None),
        )
    )
    ids = [r[0] for r in items_result.all()]

    from app.services.task_runner import create_quality_check_task
    task = await create_quality_check_task(db, ids)

    return {
        "message": f"已重置 {reset_count} 个已通过素材，重新提交 {len(ids)} 个待审核",
        "count": len(ids),
        "task_id": task.id,
    }


@router.get("/quality-stats-by-source")
async def quality_stats_by_source(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    """按素材来源统计审核通过率，用于发现「哪个采集源/平台质量差」。

    与 `/quality-stats` 同口径（图片 + 视频、排除垃圾桶），但按 `source_type` 分组，
    每组给出总数与三状态计数，`pass_rate` 只在「已判定的素材」上计算（pending 不进分母）。

    Returns:
        {"items": [{source_type, total, approved, rejected, pending, pass_rate}], ...}
    """
    result = await db.execute(
        select(
            Inspiration.source_type,
            func.coalesce(Inspiration.quality_status, "pending"),
            func.count(Inspiration.id),
        )
        .where(
            Inspiration.media_type.in_(("image", "video")),
            Inspiration.deleted_at.is_(None),
        )
        .group_by(Inspiration.source_type, func.coalesce(Inspiration.quality_status, "pending"))
    )

    grouped: dict[str, dict[str, int]] = {}
    for source_type, status, count in result.all():
        key = source_type or "unknown"
        bucket = grouped.setdefault(key, {"total": 0, "approved": 0, "rejected": 0, "pending": 0})
        bucket[status] = bucket.get(status, 0) + count
        bucket["total"] += count

    items = []
    for source_type, bucket in sorted(grouped.items(), key=lambda kv: -kv[1]["total"]):
        decided = bucket["approved"] + bucket["rejected"]
        items.append(
            {
                "source_type": source_type,
                "total": bucket["total"],
                "approved": bucket["approved"],
                "rejected": bucket["rejected"],
                "pending": bucket["pending"],
                # 只在已判定素材上算通过率：pending 混进分母会让「待审多」的来源显得质量差
                "pass_rate": round(bucket["approved"] / decided * 100, 1) if decided else 0,
            }
        )

    return {"items": items, "total": sum(i["total"] for i in items)}


@router.get("/quality-gray")
async def quality_gray(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """列出「灰区」素材供**人工优先复审**：初筛器最没把握的那批。

    灰区定义：最近一次审核日志里初筛器给出的垃圾概率落在
    `0.5 <= confidence < quality_classifier_threshold` 区间 —— 模型差一点就判垃圾，
    这类样本人工看一眼的收益远高于随机抽样。

    Args:
        limit: 最多返回多少条（按置信度降序 = 越接近阈值越优先）

    Returns:
        {"items": [{id, confidence, quality_status, quality_reason_code}], "threshold": 0.85}
    """
    threshold = settings.quality_classifier_threshold
    subq = (
        select(
            AIQualityReview.log_id,
            func.max(AIQualityReview.id).label("latest_id"),
        )
        .group_by(AIQualityReview.log_id)
        .subquery()
    )
    result = await db.execute(
        select(
            Inspiration.id,
            AIQualityReview.confidence,
            Inspiration.quality_status,
            Inspiration.quality_reason_code,
        )
        .join(AIAnalysisLog, AIAnalysisLog.inspiration_id == Inspiration.id)
        .join(AIQualityReview, AIQualityReview.log_id == AIAnalysisLog.id)
        .join(subq, subq.c.latest_id == AIQualityReview.id)
        .where(
            Inspiration.deleted_at.is_(None),
            AIQualityReview.confidence.isnot(None),
            AIQualityReview.confidence >= 0.5,
            AIQualityReview.confidence < threshold,
        )
        .order_by(AIQualityReview.confidence.desc())
        .limit(limit)
    )
    items = [
        {
            "id": row[0],
            "confidence": row[1],
            "quality_status": row[2],
            "quality_reason_code": row[3],
        }
        for row in result.all()
    ]
    return {"items": items, "threshold": threshold, "count": len(items)}


@router.get("/quality-stats")
async def quality_stats(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    """质量审核统计：待审核/已通过/已拒绝数量及通过率（图片 + 视频；视频按首帧审核）。"""
    result = await db.execute(
        select(
            func.coalesce(Inspiration.quality_status, "pending"),
            func.count(Inspiration.id),
        )
        .where(
            Inspiration.media_type.in_(("image", "video")),
            Inspiration.deleted_at.is_(None),
        )
        .group_by(func.coalesce(Inspiration.quality_status, "pending"))
    )
    counts = {status: count for status, count in result.all()}

    pending = counts.get("pending", 0)
    approved = counts.get("approved", 0)
    rejected = counts.get("rejected", 0)
    total = pending + approved + rejected
    pass_rate = round(approved / (approved + rejected) * 100, 1) if (approved + rejected) > 0 else 0

    # 疑似 AI 生成素材数（与 quality_status 正交，独立统计）
    ai_result = await db.execute(
        select(func.count(Inspiration.id)).where(
            Inspiration.media_type.in_(("image", "video")),
            Inspiration.is_ai_generated.is_(True),
            Inspiration.deleted_at.is_(None),
        )
    )
    ai_generated = ai_result.scalar() or 0

    # 实测平均耗时（秒）：只统计成功的审核日志（失败项耗时无参考价值），
    # 供前端把「审核全部」的耗时从写死的 12.5 秒/张换成真实值
    avg_result = await db.execute(
        select(func.avg(AIAnalysisLog.processing_time_ms)).where(
            AIAnalysisLog.log_type == "quality_check",
            AIAnalysisLog.error.is_(None),
            AIAnalysisLog.processing_time_ms.isnot(None),
        )
    )
    avg_ms = avg_result.scalar()

    # 按结构化原因码分组（仅被拒素材）：驱动前端「按原因筛选」与质量看板
    reason_result = await db.execute(
        select(Inspiration.quality_reason_code, func.count(Inspiration.id))
        .where(
            Inspiration.media_type.in_(("image", "video")),
            Inspiration.deleted_at.is_(None),
            Inspiration.quality_status == "rejected",
        )
        .group_by(Inspiration.quality_reason_code)
    )
    reason_counts = {(code or "unknown"): count for code, count in reason_result.all()}

    return {
        "total": total,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "pass_rate": pass_rate,
        "ai_generated": ai_generated,
        "avg_seconds": round(float(avg_ms) / 1000, 1) if avg_ms else 0,
        # 被拒素材的原因分布：{not_outfit: 120, single_item: 33, ...}
        "reason_counts": reason_counts,
    }
