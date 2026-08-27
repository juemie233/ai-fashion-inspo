"""任务队列路由：查询任务状态、任务列表、取消任务与删除任务。"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.task import TaskQueue
from app.schemas.task import TaskCancelOut, TaskListOut, TaskOut
from app.utils.time import utcnow
from app.worker import _STALE_HEARTBEAT_THRESHOLD

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# 支持「运行中取消」的任务类型：执行器内部每批检查 cancelled 后自行停止。
# 人脸扫描/匹配任务耗时较长（分钟级），用户需要能随时中断（增量语义下
# 重跑自动跳过已扫部分，中断无副作用）；标签网络分析支持暂停/恢复（断点续算）；
# 其余类型任务不硬打断。
_CANCELABLE_RUNNING_TYPES = ("face_scan", "face_match", "tag_network_analyze")


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


@router.post("/{task_id}/pause", response_model=TaskCancelOut)
async def pause_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """暂停任务（仅 tag_network_analyze 支持）：

    - 标记为 ``paused``，保存当前中间状态（last_stage + stage_state）；
    - 执行器感知到 paused 状态后保存状态并返回。
    """
    task = await db.get(TaskQueue, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")

    if task.status != "running" or task.type != "tag_network_analyze":
        raise HTTPException(
            status_code=400,
            detail=f"仅运行中的 tag_network_analyze 任务可暂停（当前状态 {task.status}）",
        )

    # 标记为 paused，等待执行器保存状态并返回
    result = await db.execute(
        update(TaskQueue)
        .where(TaskQueue.id == task_id, TaskQueue.status == "running")
        .values(status="paused", paused_at=utcnow(), updated_at=utcnow())
    )
    await db.commit()
    if result.rowcount == 0:
        await db.refresh(task)
        raise HTTPException(
            status_code=400, detail=f"任务状态已变化（当前状态 {task.status}），无法暂停"
        )
    return {"message": "任务已暂停", "task_id": task_id}


@router.post("/{task_id}/resume", response_model=TaskCancelOut)
async def resume_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """恢复任务（仅 tag_network_analyze 支持）：

    - 从 ``paused`` 状态恢复为 ``running``；
    - 保留 last_stage 和 stage_state，执行器从中续算。
    """
    task = await db.get(TaskQueue, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")

    if task.status != "paused" or task.type != "tag_network_analyze":
        raise HTTPException(
            status_code=400,
            detail=f"仅已暂停的 tag_network_analyze 任务可恢复（当前状态 {task.status}）",
        )

    # 恢复为 running，保留 last_stage 和 stage_state
    result = await db.execute(
        update(TaskQueue)
        .where(TaskQueue.id == task_id, TaskQueue.status == "paused")
        .values(status="running", paused_at=None, updated_at=utcnow())
    )
    await db.commit()
    if result.rowcount == 0:
        await db.refresh(task)
        raise HTTPException(
            status_code=400, detail=f"任务状态已变化（当前状态 {task.status}），无法恢复"
        )
    return {"message": "任务已恢复", "task_id": task_id}


@router.delete("/{task_id}", response_model=TaskCancelOut)
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除任务记录：终态（success/failed/cancelled）与「僵尸 running」可物理删除。

    - pending：拒绝删除（待执行任务会重新排队；如确需移除请走取消接口，
      其对 pending 即物理删除）
    - running：仅当心跳超时（认领它的 worker 已死，如停电/进程崩溃遗留的
      僵尸任务）可删除；worker 正在正常执行（心跳新鲜）的任务拒绝删除
    供任务管理页「删除任务」按钮清理历史记录（采集任务走采集专用删除接口）。
    """
    task = await db.get(TaskQueue, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    if task.status == "pending":
        raise HTTPException(
            status_code=400,
            detail="任务状态为 pending，不能删除（如需移除请使用取消操作）",
        )
    stale_before = utcnow() - timedelta(seconds=_STALE_HEARTBEAT_THRESHOLD)
    if task.status == "running":
        # 心跳判定与 worker 启动时的僵尸重置（_reset_stale_tasks）一致
        zombie = task.heartbeat_at is None or task.heartbeat_at < stale_before
        if not zombie:
            raise HTTPException(
                status_code=400,
                detail="任务正在执行中（心跳正常），不能删除",
            )
    # 原子删除：二次确认状态仍落在可删范围（终态或僵尸 running），
    # 防止删除瞬间正在执行 / 已被 worker 重置为 pending
    result = await db.execute(
        delete(TaskQueue).where(
            TaskQueue.id == task_id,
            or_(
                TaskQueue.status.in_(("success", "failed", "cancelled")),
                and_(
                    TaskQueue.status == "running",
                    or_(
                        TaskQueue.heartbeat_at.is_(None),
                        TaskQueue.heartbeat_at < stale_before,
                    ),
                ),
            ),
        )
    )
    await db.commit()
    if result.rowcount == 0:
        current = await db.get(TaskQueue, task_id)
        state = current.status if current else "已删除"
        raise HTTPException(
            status_code=400,
            detail=f"任务状态已变化（当前状态 {state}），不能删除",
        )
    return {"message": "任务已删除", "task_id": task_id, "deleted": True}
