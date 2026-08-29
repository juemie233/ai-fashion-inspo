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


async def test_reset_stale_tasks_late_sweep_after_fresh_restart(client):
    """快速重启场景回归：关闭服务后 90s 内重启，启动时心跳仍「新鲜」，
    启动清扫会跳过该任务（存活误判）；心跳过期后的**后续清扫**必须能
    把它重置回 pending——否则任务永久卡在 running（真实案例：向量回填
    任务重启后卡死，无人再认领）。"""
    tid = await _add_running_task(heartbeat_age_seconds=5)  # 重启时心跳仍新鲜

    await _reset_stale_tasks()  # 模拟启动时的一次清扫

    async with async_session() as db:
        task = await db.get(TaskQueue, tid)
        assert task.status == "running"  # 启动清扫确实跳过（未误伤新鲜心跳）

    # 模拟时间流逝：认领它的旧 worker 已死，心跳停止更新而过期
    async with async_session() as db:
        task = await db.get(TaskQueue, tid)
        task.heartbeat_at = utcnow() - timedelta(seconds=120)
        await db.commit()

    await _reset_stale_tasks()  # 模拟运行期的周期性清扫（worker 主循环每 30s）

    async with async_session() as db:
        task = await db.get(TaskQueue, tid)
        assert task.status == "pending"  # 已被后续清扫重置，等待重新认领
        assert task.claimed_by is None
