"""worker 心跳租约：基于心跳的遗留 running 任务重置逻辑。"""

from datetime import timedelta

from app.database import async_session
from app.models.task import TaskQueue
from app.utils.time import utcnow
from app.worker import _reset_stale_tasks


async def _add_running_task(heartbeat_age_seconds: float | None) -> int:
    """插入一条 running 任务，heartbeat_at 为「距今 N 秒前」或 None。"""
    hb = (
        utcnow() - timedelta(seconds=heartbeat_age_seconds)
        if heartbeat_age_seconds is not None
        else None
    )
    async with async_session() as db:
        task = TaskQueue(
            type="batch_analyze", status="running", progress=0, total=1, done=0,
            result={}, max_retries=2, claimed_by="worker-x", heartbeat_at=hb,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task.id


async def test_reset_stale_tasks_only_resets_stale_heartbeat(client):
    """仅重置心跳超时的 running 任务，心跳新鲜的存活任务不受影响。"""
    stale_id = await _add_running_task(heartbeat_age_seconds=120)  # 超时（> 90s）
    fresh_id = await _add_running_task(heartbeat_age_seconds=5)    # 新鲜

    await _reset_stale_tasks()

    async with async_session() as db:
        stale = await db.get(TaskQueue, stale_id)
        fresh = await db.get(TaskQueue, fresh_id)
        assert stale.status == "pending"  # 已重置待重新执行
        assert stale.claimed_by is None
        assert fresh.status == "running"  # 存活 worker 的任务不受影响


async def test_reset_stale_tasks_resets_null_heartbeat(client):
    """心跳为空的 running 任务（旧版本遗留）也应被重置。"""
    tid = await _add_running_task(heartbeat_age_seconds=None)

    await _reset_stale_tasks()

    async with async_session() as db:
        task = await db.get(TaskQueue, tid)
        assert task.status == "pending"
