"""AI 子路由：质量审核相关接口。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.inspiration import Inspiration
from app.routers.ai_shared import _quality_active

router = APIRouter()


@router.post("/quality-check")
async def batch_quality_check(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """批量审核所有待审核（pending）的图片素材。

    只处理图片素材；审核结果直接写回 quality_status（approved/rejected）。
    已改造为数据库驱动任务队列：创建任务记录后立即返回 task_id，
    由独立 worker 进程异步执行，前端轮询 GET /api/tasks/{task_id} 获取进度。
    """
    result = await db.execute(
        select(Inspiration.id)
        .where(
            Inspiration.quality_status == "pending",
            Inspiration.media_type == "image",
        )
        .limit(limit)
    )
    ids = [r[0] for r in result.all()]

    if not ids:
        return {"message": "没有待审核的素材", "count": 0}

    from app.services.task_runner import create_quality_check_task
    task = await create_quality_check_task(db, ids)

    return {
        "message": f"已提交 {len(ids)} 个素材进行质量审核",
        "count": len(ids),
        "task_id": task.id,
    }


@router.post("/quality-recheck")
async def recheck_quality(db: AsyncSession = Depends(get_db)):
    """重新审核所有已通过（approved）的图片素材。

    将 approved 重置为 pending 后提交任务队列批量审核，用最新审核标准重新判定。
    用于修正审核标准升级后历史素材的误判（如「只有腿部」被误判为通过）。
    """
    result = await db.execute(
        update(Inspiration)
        .where(
            Inspiration.media_type == "image",
            Inspiration.quality_status == "approved",
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


@router.get("/quality-stats")
async def quality_stats(db: AsyncSession = Depends(get_db)):
    """质量审核统计：待审核/已通过/已拒绝数量及通过率（仅图片素材）。"""
    result = await db.execute(
        select(
            func.coalesce(Inspiration.quality_status, "pending"),
            func.count(Inspiration.id),
        )
        .where(Inspiration.media_type == "image")
        .group_by(func.coalesce(Inspiration.quality_status, "pending"))
    )
    counts = {status: count for status, count in result.all()}

    pending = counts.get("pending", 0)
    approved = counts.get("approved", 0)
    rejected = counts.get("rejected", 0)
    total = pending + approved + rejected
    pass_rate = round(approved / (approved + rejected) * 100, 1) if (approved + rejected) > 0 else 0

    return {
        "total": total,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "pass_rate": pass_rate,
        "active": len(_quality_active),
    }


@router.get("/quality-active")
async def quality_active():
    """正在审核中的素材 ID 列表。"""
    return {"active": list(_quality_active), "count": len(_quality_active)}
