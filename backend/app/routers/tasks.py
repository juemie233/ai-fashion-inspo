"""任务队列路由：查询任务状态、任务列表与取消任务。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.task import TaskQueue
from app.schemas.task import TaskListOut, TaskOut
from app.utils.time import utcnow

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# 支持「运行中取消」的任务类型：执行器内部每批检查 cancelled 后自行停止。
# 人脸扫描/匹配任务耗时较长（分钟级），用户需要能随时中断（增量语义下
# 重跑自动跳过已扫部分，中断无副作用）；其余类型任务不硬打断。
_CANCELABLE_RUNNING_TYPES = ("face_scan", "face_match")


@router.get("", response_model=TaskListOut)
async def list_tasks(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    status: str | None = None,
    type: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
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
) -> TaskQueue:
    """查询单个任务状态（供前端轮询进度）。"""
    task = await db.get(TaskQueue, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    return task


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """取消任务：pending 一律可取消；人脸扫描/匹配任务运行中也可取消。

    运行中取消的语义：任务执行器每批检查状态后自行停止（已完成的批次不
    回滚），用户可随时重新发起任务，增量语义自动跳过已完成部分。
    """
    # 先确认任务存在（404），再做条件更新，避免「读取到 pending 后 worker 已认领
    # 为 running」的 TOCTOU 竞态（否则会把 running 覆盖成 cancelled）
    task = await db.get(TaskQueue, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    result = await db.execute(
        update(TaskQueue)
        .where(
            TaskQueue.id == task_id,
            or_(
                TaskQueue.status == "pending",
                and_(
                    TaskQueue.status == "running",
                    TaskQueue.type.in_(_CANCELABLE_RUNNING_TYPES),
                ),
            ),
        )
        .values(status="cancelled", error="用户手动取消", updated_at=utcnow())
    )
    await db.commit()
    if result.rowcount == 0:
        await db.refresh(task)
        raise HTTPException(
            status_code=409,
            detail=f"任务已开始执行（当前状态 {task.status}），无法取消",
        )
    return {"message": "任务已取消", "task_id": task_id}
