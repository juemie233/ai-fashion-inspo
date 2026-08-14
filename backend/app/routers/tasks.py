"""任务队列路由：查询任务状态、任务列表与取消任务。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.task import TaskQueue
from app.schemas.task import TaskListOut, TaskOut

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _utcnow() -> datetime:
    """返回当前 UTC 时间（naive datetime）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("", response_model=TaskListOut)
async def list_tasks(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    status: str | None = None,
    type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """分页查询任务列表，可按状态 / 类型筛选。"""
    query = select(TaskQueue)
    if status:
        query = query.where(TaskQueue.status == status)
    if type:
        query = query.where(TaskQueue.type == type)
    query = query.order_by(TaskQueue.id.desc())

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    tasks = result.scalars().all()

    return {
        "items": [TaskOut.model_validate(t) for t in tasks],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    """查询单个任务状态（供前端轮询进度）。"""
    task = await db.get(TaskQueue, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    return task


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    """取消排队中的任务（仅 pending 状态可取消，running 不硬打断）。"""
    # 先确认任务存在（404），再做条件更新：仅当仍为 pending 时才置为 cancelled，
    # 避免「读取到 pending 后 worker 已认领为 running」的 TOCTOU 竞态（否则会把 running 覆盖成 cancelled）。
    task = await db.get(TaskQueue, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    result = await db.execute(
        update(TaskQueue)
        .where(TaskQueue.id == task_id, TaskQueue.status == "pending")
        .values(status="cancelled", error="用户手动取消", updated_at=_utcnow())
    )
    await db.commit()
    if result.rowcount == 0:
        await db.refresh(task)
        raise HTTPException(
            status_code=409,
            detail=f"任务已开始执行（当前状态 {task.status}），无法取消",
        )
    return {"message": "任务已取消", "task_id": task_id}
