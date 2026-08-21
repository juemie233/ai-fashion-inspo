"""任务队列路由：查询任务状态、任务列表与取消任务。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.task import TaskQueue
from app.schemas.task import TaskCancelOut, TaskListOut, TaskOut
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


@router.post("/{task_id}/cancel", response_model=TaskCancelOut)
async def cancel_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """取消任务：

    - ``pending``（等待运行）：物理删除该任务记录（需求：取消后从历史与
      ``task_queue`` 表中直接移除，不保留）。
    - ``running`` 的人脸扫描/匹配任务：标记为 cancelled（运行中取消的既有能力，
      记录保留；执行器每批检查后自行停止）。
    - 其余状态（success/failed/cancelled 及不可运行中取消的 running 类型）：
      返回 400，记录保持不变。

    删除采用「带状态条件的原子 DELETE」：若与 worker 认领（pending→running）
    发生竞态，删除落空（rowcount=0），按非 pending 状态处理并返回 400，
    避免误删正在执行的任务。
    """
    # 先确认任务存在（404），后续按状态分支处理
    task = await db.get(TaskQueue, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")

    if task.status == "pending":
        result = await db.execute(
            delete(TaskQueue).where(TaskQueue.id == task_id, TaskQueue.status == "pending")
        )
        await db.commit()
        if result.rowcount == 0:
            # 竞态：读取 pending 后任务已被 worker 认领为 running（或已被并发删除），
            # 按非 pending 处理，绝不误删正在执行的任务
            current = await db.get(TaskQueue, task_id)
            state = current.status if current else "已删除"
            raise HTTPException(
                status_code=400,
                detail=f"任务已开始执行（当前状态 {state}），不能删除",
            )
        return {"message": "任务已删除", "task_id": task_id, "deleted": True}

    if task.status == "running" and task.type in _CANCELABLE_RUNNING_TYPES:
        # 运行中取消（仅人脸扫描/匹配）：保留记录，标记 cancelled
        result = await db.execute(
            update(TaskQueue)
            .where(
                TaskQueue.id == task_id,
                TaskQueue.status == "running",
                TaskQueue.type.in_(_CANCELABLE_RUNNING_TYPES),
            )
            .values(status="cancelled", error="用户手动取消", updated_at=utcnow())
        )
        await db.commit()
        if result.rowcount == 0:
            await db.refresh(task)
            raise HTTPException(
                status_code=400, detail=f"任务状态已变化（当前状态 {task.status}），无法取消"
            )
        return {"message": "任务已取消", "task_id": task_id}

    raise HTTPException(
        status_code=400,
        detail=f"仅等待中的任务可以取消并删除（当前状态 {task.status}）",
    )
