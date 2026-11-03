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


async def test_finalize_orphan_run_logs_收尾超期_进行中台账():
    """启动时把超过 2 小时仍 running 的台账标 failed；新台账不动。"""
    async with async_session() as db:
        old = TaskRunLog(
            task_id=1,
            task_type="batch_analyze",
            attempt=0,
            started_at=utcnow() - timedelta(hours=3),
            status="running",
        )
        recent = TaskRunLog(
            task_id=2,
            task_type="batch_analyze",
            attempt=0,
            started_at=utcnow(),
            status="running",
        )
        db.add_all([old, recent])
        await db.commit()
        await db.refresh(old)
        await db.refresh(recent)
        old_id, recent_id = old.id, recent.id

    await worker._finalize_orphan_run_logs()

    async with async_session() as db:
        old_row = await db.get(TaskRunLog, old_id)
        recent_row = await db.get(TaskRunLog, recent_id)
        assert old_row is not None and old_row.status == "failed"
        assert old_row.finished_at is not None
        assert "台账未收尾" in (old_row.error or "")
        assert recent_row is not None and recent_row.status == "running"
