"""任务队列路由：查询任务状态、任务列表与取消任务。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.task import TaskQueue
from app.schemas.task import TaskListOut, TaskOut

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


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
    task = await db.get(TaskQueue, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    if task.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"仅可取消排队中（pending）的任务，当前状态为 {task.status}",
        )
    task.status = "cancelled"
    task.error = "用户手动取消"
    await db.commit()
    return {"message": "任务已取消", "task_id": task.id}
