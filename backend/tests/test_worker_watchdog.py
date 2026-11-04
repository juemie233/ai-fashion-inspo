"""worker 看门狗与台账收尾回归（2026-11-02 卡死事故加固）。

现场：worker 进程活着但心跳停了，任务 #745 卡在 running、后续 4 个出图任务
永远排队。加固后：
- 心跳超时 → supervisor 重启 worker（见 scripts/ops/supervisor.py，另有用例）；
- 任务无进度超时 → worker 看门狗取消它并标 failed，释放并发槽位；
- 崩溃遗留的 running 台账 → worker 启动时补 finished_at。
"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import update

from app import worker
from app.database import async_session
from app.models.task import TaskQueue
from app.models.task_run_log import TaskRunLog
from app.utils.time import utcnow


async def _add_task(*, status: str, claimed_by: str | None, updated_ago_seconds: int) -> int:
    """插入一条任务并把 updated_at 调到「N 秒前」。"""
    async with async_session() as db:
        task = TaskQueue(
            type="batch_analyze",
            status=status,
            progress=0,
            total=1,
            done=0,
            result={},
            claimed_by=claimed_by,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id
        await db.execute(
            update(TaskQueue)
            .where(TaskQueue.id == task_id)
            .values(updated_at=utcnow() - timedelta(seconds=updated_ago_seconds))
        )
        await db.commit()
    return task_id


async def test_stalled_task_ids_只返回本worker且超时的running任务():
    """看门狗判定：同 worker + running + 超过阈值没更新，三个条件缺一不可。"""
    stalled = await _add_task(status="running", claimed_by="w-test", updated_ago_seconds=3600)
    fresh = await _add_task(status="running", claimed_by="w-test", updated_ago_seconds=5)
    other_worker = await _add_task(status="running", claimed_by="w-other", updated_ago_seconds=3600)
    pending = await _add_task(status="pending", claimed_by=None, updated_ago_seconds=3600)

    ids = await worker._stalled_task_ids("w-test", stall_seconds=1800)

    assert ids == [stalled]
    assert fresh not in ids and other_worker not in ids and pending not in ids


async def test_心跳不刷新updated_at_看门狗仍能识别卡死():
    """P0-1：心跳只写 heartbeat_at，不能把「执行器进度时间」一起刷新。

    历史 bug：心跳 UPDATE 触发 TaskQueue.updated_at 的 onupdate=utcnow，每 10 秒
    刷新一次，看门狗要求 30 分钟不动，正常路径下永远不会触发。
    """
    task_id = await _add_task(status="running", claimed_by="w-beat", updated_ago_seconds=3600)
    async with async_session() as db:
        before = (await db.get(TaskQueue, task_id)).updated_at

    await worker._write_heartbeat("w-beat")

    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert task is not None
        assert task.updated_at == before, "心跳刷新了 updated_at，看门狗会永久失效"
        assert task.heartbeat_at is not None
        heartbeat_age = utcnow() - task.heartbeat_at
        assert heartbeat_age < timedelta(seconds=30)

    ids = await worker._stalled_task_ids("w-beat", stall_seconds=1800)
    assert task_id in ids


async def test_心跳不刷新_updated_at_的_orm_update语义():
    """确认「显式 updated_at=原列表达式」能压过 onupdate（SQLAlchemy 行为回归）。"""
    task_id = await _add_task(status="running", claimed_by="w-beat", updated_ago_seconds=3600)
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        old = task.updated_at
        # 模拟除心跳外的普通 ORM 写入：updated_at 必须刷新（进度语义）
        task.progress = 50
        await db.commit()
        async with async_session() as probe:
            assert (await probe.get(TaskQueue, task_id)).updated_at > old


async def test_fail_stalled_task_标失败并补台账结束时间():
    """看门狗取消后：任务 failed + 错误可读 + 台账 finished_at 补齐。"""
    task_id = await _add_task(status="running", claimed_by="w-test", updated_ago_seconds=3600)
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        run_log_id = await worker._start_run_log(db, task)
    assert run_log_id is not None
    worker._RUN_LOG_IDS[task_id] = run_log_id

    await worker._fail_stalled_task(task_id)

    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert task is not None
        assert task.status == "failed"
        assert "看门狗" in (task.error or "")
        row = await db.get(TaskRunLog, run_log_id)
        assert row is not None
        assert row.status == "failed"
        assert row.finished_at is not None
        assert "看门狗" in (row.error or "")
    assert task_id not in worker._RUN_LOG_IDS  # 收尾后清理缓存


async def test_run_task_safe_看门狗取消会写终态(monkeypatch):
    """看门狗取消 → 走 _fail_stalled_task 收尾；随后仍抛出 CancelledError 释放槽位。"""
    finalized: list[int] = []

    async def _hang(_task_id: int) -> None:
        await asyncio.sleep(3600)

    async def _fake_fail(task_id: int) -> None:
        finalized.append(task_id)

    monkeypatch.setattr(worker, "_run_task", _hang)
    monkeypatch.setattr(worker, "_fail_stalled_task", _fake_fail)

    coro = asyncio.create_task(worker._run_task_safe(7))
    await asyncio.sleep(0)  # 让协程进入 _run_task 的 sleep
    worker._WATCHDOG_CANCELLED.add(7)
    coro.cancel()
    with pytest.raises(asyncio.CancelledError):
        await coro

    assert finalized == [7]
    assert 7 not in worker._WATCHDOG_CANCELLED  # 标记消费后清理


async def test_run_task_safe_进程关闭取消不写终态(monkeypatch):
    """没有看门狗标记的取消（进程关闭）不能把任务标 failed——留给下次启动按心跳重置。"""
    finalized: list[int] = []

    async def _hang(_task_id: int) -> None:
        await asyncio.sleep(3600)

    async def _fake_fail(task_id: int) -> None:
        finalized.append(task_id)

    monkeypatch.setattr(worker, "_run_task", _hang)
    monkeypatch.setattr(worker, "_fail_stalled_task", _fake_fail)

    coro = asyncio.create_task(worker._run_task_safe(8))
    await asyncio.sleep(0)
    coro.cancel()
    with pytest.raises(asyncio.CancelledError):
        await coro

    assert finalized == []


async def test_watchdog_finalize_失败保留标记_下轮重试成功(monkeypatch):
    """P1-4：收尾失败不能先丢标记，否则任务永久 running；主循环重试成功后清标记。"""
    finalized: list[int] = []

    async def _hang(_task_id: int) -> None:
        await asyncio.sleep(3600)

    async def _fail(_task_id: int) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(worker, "_run_task", _hang)
    monkeypatch.setattr(worker, "_fail_stalled_task", _fail)

    coro = asyncio.create_task(worker._run_task_safe(9))
    await asyncio.sleep(0)
    worker._WATCHDOG_CANCELLED.add(9)
    coro.cancel()
    with pytest.raises(asyncio.CancelledError):
        await coro

    assert 9 in worker._WATCHDOG_CANCELLED, "收尾失败时标记必须保留待重试"

    async def _ok(task_id: int) -> None:
        finalized.append(task_id)

    monkeypatch.setattr(worker, "_fail_stalled_task", _ok)
    await worker._retry_pending_watchdog_finalize(set())
    assert finalized == [9]
    assert 9 not in worker._WATCHDOG_CANCELLED


async def test_finalize_orphan_run_logs_只收尾非running任务的台账():
    """P1-3：任务仍 running 的台账（无论多久）不能动；任务结束/不存在的才收尾。"""
    async with async_session() as db:
        running_task = TaskQueue(
            type="batch_analyze", status="running", progress=0, total=1, done=0, result={}
        )
        done_task = TaskQueue(
            type="batch_analyze", status="success", progress=100, total=1, done=1, result={}
        )
        db.add_all([running_task, done_task])
        await db.commit()
        await db.refresh(running_task)
        await db.refresh(done_task)
        running_id, done_id = running_task.id, done_task.id

        running_log = TaskRunLog(
            task_id=running_id,
            task_type="batch_analyze",
            attempt=0,
            started_at=utcnow() - timedelta(hours=5),
            status="running",
        )
        done_log = TaskRunLog(
            task_id=done_id,
            task_type="batch_analyze",
            attempt=0,
            started_at=utcnow() - timedelta(hours=5),
            status="running",
        )
        missing_log = TaskRunLog(
            task_id=999_999,
            task_type="batch_analyze",
            attempt=0,
            started_at=utcnow() - timedelta(hours=5),
            status="running",
        )
        db.add_all([running_log, done_log, missing_log])
        await db.commit()
        await db.refresh(running_log)
        await db.refresh(done_log)
        await db.refresh(missing_log)
        log_ids = (running_log.id, done_log.id, missing_log.id)

    await worker._finalize_orphan_run_logs()

    async with async_session() as db:
        running_row = await db.get(TaskRunLog, log_ids[0])
        done_row = await db.get(TaskRunLog, log_ids[1])
        missing_row = await db.get(TaskRunLog, log_ids[2])
        assert running_row is not None and running_row.status == "running"
        assert done_row is not None and done_row.status == "failed"
        assert done_row.finished_at is not None
        assert "台账未收尾" in (done_row.error or "")
        assert missing_row is not None and missing_row.status == "failed"
