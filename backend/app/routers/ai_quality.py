"""AI 子路由。"""

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    analysis_log_filter as _analysis_log_filter,
)
from app.routers.ai_shared import (
    _analysis_semaphore,
    _active_analyses,
    _analysis_tasks,
    _task_by_id,
    _pending_queue,
    _queue_paused,
    _quality_active,
    _run_analysis,
    _run_quality_check,
    _update_env_file,
    _fmt_utc,
    _format_size,
)
from app.services.model_config import get_model_config, update_model_config
from app.utils.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/quality-check")
async def batch_quality_check(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """批量审核所有待审核（pending）的图片素材，后台异步执行。

    只处理图片素材；审核结果直接写回 quality_status（approved/rejected）。
    """
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path)
        .where(
            Inspiration.quality_status == "pending",
            Inspiration.media_type == "image",
        )
        .limit(limit)
    )
    items = result.all()

    if not items:
        return {"message": "没有待审核的素材", "count": 0}

    for insp_id, file_path in items:
        task = asyncio.create_task(_run_quality_check(insp_id, file_path))
        _analysis_tasks.add(task)
        task.add_done_callback(_analysis_tasks.discard)

    return {
        "message": f"已提交 {len(items)} 个素材进行质量审核",
        "count": len(items),
    }


@router.post("/quality-recheck")
async def recheck_quality(db: AsyncSession = Depends(get_db)):
    """重新审核所有已通过（approved）的图片素材。

    将 approved 重置为 pending 后立即提交批量审核，用最新审核标准重新判定。
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

    # 提交所有待审核素材（含刚重置的），信号量保证单卡并发不超过 2
    items_result = await db.execute(
        select(Inspiration.id, Inspiration.file_path).where(
            Inspiration.quality_status == "pending",
            Inspiration.media_type == "image",
        )
    )
    items = items_result.all()

    for insp_id, file_path in items:
        task = asyncio.create_task(_run_quality_check(insp_id, file_path))
        _analysis_tasks.add(task)
        task.add_done_callback(_analysis_tasks.discard)

    return {
        "message": f"已重置 {reset_count} 个已通过素材，重新提交 {len(items)} 个待审核",
        "count": len(items),
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
