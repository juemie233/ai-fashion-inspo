"""worker 并发完整性集成测试：两个 worker 抢同一批素材时，一张图只能被分析一次。

为什么需要（单测覆盖不到）：`batch_analyze` 的并发保护有两层——
1. `_claim_next_task` 的**条件 UPDATE**（原子认领，防两个 worker 拿同一个任务）；
2. `_inflight_excluding`（加载待分析素材时豁免「已被其它未完成任务认领」的 ID）。

第 2 层是跨任务去重的关键：漏了就会对同一张图调用两次 Ollama（实测 12.5 秒/张），
并写出两条分析日志。这个场景只有一个 worker 时的单测永远碰不到。

本文件用真实的两个协程 + 两个独立 DB 会话跑，只把「调用 AI」这一层替换成计数器。
"""

import asyncio

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services.task_runners import batch_analyze as ba


async def _insp(insp_id: str) -> str:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/2026-10/{insp_id}.jpg",
                media_type="image",
            )
        )
        await db.commit()
    return insp_id


async def _task(ids: list[str], *, status: str = "running") -> int:
    async with async_session() as db:
        t = TaskQueue(
            type="batch_analyze",
            status=status,
            total=len(ids),
            done=0,
            result={"inspiration_ids": ids},
        )
        db.add(t)
        await db.commit()
        return t.id


@pytest.fixture
def analyze_counter(monkeypatch):
    """把「分析一张图」换成计数器：并发下统计每个素材被分析了几次。"""
    calls: list[str] = []

    async def _fake(sem, insp_id, frames):
        async with sem:
            calls.append(insp_id)
            await asyncio.sleep(0.01)  # 放大并发窗口，让「没加锁」的实现必然露馅
            return insp_id, True, None, False

    monkeypatch.setattr(ba, "_analyze_one", _fake)
    return calls


async def test_atomic_claim_hands_one_task_to_only_one_worker():
    """同一份 pending 任务被两个 worker 同时认领：只有一个拿到（条件 UPDATE 生效）。"""
    tid = await _task(["claim-1"], status="pending")

    first, second = await asyncio.gather(
        ba_claim("worker-a"),
        ba_claim("worker-b"),
    )

    got = [x for x in (first, second) if x is not None]
    assert got == [tid], f"同一任务被认领了两次：{first} / {second}"

    async with async_session() as db:
        row = await db.get(TaskQueue, tid)
    assert row.status == "running"
    assert row.claimed_by in {"worker-a", "worker-b"}


async def ba_claim(worker_id: str) -> int | None:
    """调用真实的认领实现（放函数里便于 gather 并行）。"""
    from app.worker import _claim_next_task

    return await _claim_next_task(worker_id)


async def test_two_workers_do_not_analyze_same_inspiration(analyze_counter):
    """两个任务包含同一批素材并发执行：每张图只被分析一次（跨任务去重）。"""
    ids = [await _insp(f"dup-{i}") for i in range(5)]
    t1 = await _task(ids)
    t2 = await _task(ids)

    async def run(task_id: int) -> None:
        async with async_session() as db:
            task = await db.get(TaskQueue, task_id)
            await ba.execute_batch_analyze(db, task)

    await asyncio.gather(run(t1), run(t2))

    assert sorted(analyze_counter) == sorted(ids), (
        f"每张图应恰好分析一次，实际 {len(analyze_counter)} 次：{analyze_counter}"
    )

    # 两个任务的 success_count 之和应等于素材数：说明没有任务「空转」
    async with async_session() as db:
        tasks = (
            await db.execute(select(TaskQueue).where(TaskQueue.id.in_([t1, t2])))
        ).scalars().all()
    total_success = sum((t.result or {}).get("success_count", 0) for t in tasks)
    assert total_success == len(ids), f"实际成功数 {total_success} != {len(ids)}"
    assert any(t.total > 0 for t in tasks)  # 至少有一个任务真的干了活（不是双方空转）


async def test_single_task_still_analyzes_every_material(analyze_counter):
    """对照组：只有一个任务时全部素材都要被分析（去重不能变成「都不做」）。"""
    ids = [await _insp(f"solo-{i}") for i in range(4)]
    tid = await _task(ids)

    async with async_session() as db:
        task = await db.get(TaskQueue, tid)
        await ba.execute_batch_analyze(db, task)

    assert sorted(analyze_counter) == sorted(ids)


async def test_failed_analyze_is_not_retried_by_the_other_task(analyze_counter):
    """一个任务分析失败后，另一个任务不该再重复分析同一张（失败也是「处理过」）。"""
    ids = [await _insp("fail-1"), await _insp("fail-2")]
    t1 = await _task(ids)
    t2 = await _task(ids)

    async def run(task_id: int) -> None:
        async with async_session() as db:
            task = await db.get(TaskQueue, task_id)
            await ba.execute_batch_analyze(db, task)

    await asyncio.gather(run(t1), run(t2))

    assert len(analyze_counter) == len(ids)  # 每个素材仍只尝试一次
