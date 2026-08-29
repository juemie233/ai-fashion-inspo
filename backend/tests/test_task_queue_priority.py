"""任务队列优先级与并发配置：认领排序、并发度读取、生命周期事件广播。"""

from sqlalchemy import select

import app.services.task_events as task_events_module
from app.database import async_session
from app.models.task import TaskQueue
from app.services.task_runner import TASK_HANDLERS, _analyze_concurrency
from app.services.task_runners.batch_analyze import (
    create_batch_analyze_task,
    create_multi_analyze_task,
)
from app.services.task_runners.batch_delete import create_batch_delete_task
from app.worker import _claim_next_task, _run_task_safe


async def _add_task(
    priority: int = 0, type_: str = "batch_analyze", status: str = "pending"
) -> int:
    """插入一条任务并返回 ID（默认 pending；worker 执行路径测试需先置 running）。"""
    async with async_session() as db:
        task = TaskQueue(
            type=type_, status=status, priority=priority,
            progress=0, total=1, done=0, result={}, max_retries=2,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task.id


async def test_claim_order_prefers_higher_priority(client):
    """worker 认领按 priority DESC 排序：高优先级任务先于先创建的低优先级任务。"""
    low_id = await _add_task(priority=0)     # 先创建但优先级低
    high_id = await _add_task(priority=10)   # 后创建但优先级高

    claimed = await _claim_next_task("worker-test")

    assert claimed == high_id


async def test_claim_order_fifo_within_same_priority(client):
    """同优先级保持 FIFO：按 id ASC 认领。"""
    first_id = await _add_task(priority=0)
    second_id = await _add_task(priority=0)

    claimed = await _claim_next_task("worker-test")

    assert claimed == first_id
    claimed2 = await _claim_next_task("worker-test")
    assert claimed2 == second_id


async def test_claim_skips_not_yet_retryable(client):
    """设置了 next_retry_at（未到期）的任务即使优先级高也不被认领。"""
    from datetime import timedelta

    from app.utils.time import utcnow

    async with async_session() as db:
        task = TaskQueue(
            type="batch_analyze", status="pending", priority=100,
            progress=0, total=1, done=0, result={}, max_retries=2,
            next_retry_at=utcnow() + timedelta(seconds=600),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        blocked_id = task.id

    later_id = await _add_task(priority=0)
    claimed = await _claim_next_task("worker-test")
    assert claimed == later_id
    assert claimed != blocked_id


async def test_default_and_custom_priority(client):
    """创建函数 priority 参数：默认 0，可显式传高优先级；批量删除为 -5 低优先级。"""
    async with async_session() as db:
        batch = await create_batch_analyze_task(db, ["i1"])
        multi = await create_multi_analyze_task(db, ["i1"], [{"model": "m", "prompt": "p"}])
        urgent = await create_batch_analyze_task(db, ["i2"], priority=10)
        delete_task = await create_batch_delete_task(db, ["i3"], label="ids")

        assert batch.priority == 0
        assert multi.priority == 0
        assert urgent.priority == 10
        # 批量删除属清理类任务，固定低优先级，不与分析链路抢队列
        assert delete_task.priority == -5


async def test_analyze_concurrency_reads_settings(client, monkeypatch):
    """批内并发度从 settings.analyze_concurrency 动态读取，且最小为 1。"""
    from app.config import settings

    monkeypatch.setattr(settings, "analyze_concurrency", 3)
    assert _analyze_concurrency() == 3

    monkeypatch.setattr(settings, "analyze_concurrency", 0)
    assert _analyze_concurrency() == 1  # 非法值（<=0）兜底为 1


async def test_run_task_broadcasts_lifecycle_events(client, monkeypatch):
    """任务执行广播生命周期事件：running → success；失败任务广播 failed。"""
    events: list[dict] = []

    async def _fake_broadcast(payload: dict) -> None:
        events.append(payload)

    monkeypatch.setattr(task_events_module, "broadcast_task_event", _fake_broadcast)

    async def _ok_handler(db, task):
        task.done = task.total or 1
        task.progress = 100
        await db.commit()

    async def _boom_handler(db, task):
        raise RuntimeError("模拟永久失败")

    monkeypatch.setitem(TASK_HANDLERS, "batch_analyze", _ok_handler)
    monkeypatch.setitem(TASK_HANDLERS, "quality_check", _boom_handler)

    ok_id = await _add_task(type_="batch_analyze", status="running")
    fail_id = await _add_task(type_="quality_check", status="running")

    await _run_task_safe(ok_id)
    await _run_task_safe(fail_id)

    ok_events = [e for e in events if e["task_id"] == ok_id]
    fail_events = [e for e in events if e["task_id"] == fail_id]

    assert [e["event"] for e in ok_events] == ["running", "success"]
    assert ok_events[-1]["progress"] == 100
    assert ok_events[-1]["status"] == "success"

    assert [e["event"] for e in fail_events] == ["running", "failed"]
    assert fail_events[-1]["error"] == "模拟永久失败"
    assert all(e["type"] == "task_event" for e in events)
    assert all(e["task_type"] in ("batch_analyze", "quality_check") for e in events)


async def test_worker_concurrency_config(client, monkeypatch):
    """worker 并发配置项存在且可从 settings 读取（默认 1）。"""
    from app.config import settings

    assert settings.worker_concurrency == 1
    monkeypatch.setattr(settings, "analyze_concurrency", 2)
    assert settings.analyze_concurrency == 2
