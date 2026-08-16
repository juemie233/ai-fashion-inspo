"""AI 子路由：质量审核相关接口。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.inspiration import Inspiration
from app.routers.ai_shared import _update_env_file

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
    limit: int = Query(50, ge=1, le=200),
    random: bool = Query(False, description="是否随机抽取素材（含已审查）"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    """批量审核图片素材。

    只处理图片素材；审核结果直接写回 quality_status（approved/rejected）。
    已改造为数据库驱动任务队列：创建任务记录后立即返回 task_id，
    由独立 worker 进程异步执行，前端轮询 GET /api/tasks/{task_id} 获取进度。

    参数:
        limit: 最多审核的素材数量。
        random: 为 True 时随机抽取 limit 个素材（含已审查，会覆盖重审）；
            为 False 时按默认顺序取前 limit 个待审核（pending）素材。
    """
    # 随机复审抽取所有图片素材（含已审查），普通审核仅取 pending；均排除垃圾桶素材
    stmt = select(Inspiration.id).where(
        Inspiration.media_type == "image",
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


@router.get("/quality-stats")
async def quality_stats(db: AsyncSession = Depends(get_db)) -> dict[str, int | float]:
    """质量审核统计：待审核/已通过/已拒绝数量及通过率（仅图片素材）。"""
    result = await db.execute(
        select(
            func.coalesce(Inspiration.quality_status, "pending"),
            func.count(Inspiration.id),
        )
        .where(
            Inspiration.media_type == "image",
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
            Inspiration.media_type == "image",
            Inspiration.is_ai_generated.is_(True),
            Inspiration.deleted_at.is_(None),
        )
    )
    ai_generated = ai_result.scalar() or 0

    return {
        "total": total,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "pass_rate": pass_rate,
        "ai_generated": ai_generated,
    }
