"""出图批次 running 停滞对账（2026-11-06 数据库审查 P1-3）。"""

from __future__ import annotations

from app.database import async_session
from app.models.generation import GenerationRun
from app.models.task import TaskQueue
from app.services.generation_service import reconcile_stale_generation_runs


async def test_stale_running_runs_are_failed_after_reconcile():
    async with async_session() as db:
        # 任务已不存在（2026-10-28 run 130 的真实场景）
        missing = GenerationRun(status="running", prompt="p", task_id=999999)
        # 任务已终态
        terminal_task = TaskQueue(type="image_gen", status="failed", progress=100)
        db.add_all([missing, terminal_task])
        await db.flush()
        terminal = GenerationRun(status="running", prompt="p", task_id=terminal_task.id)
        # 任务仍在跑：不能被误杀
        live_task = TaskQueue(type="image_gen", status="running", progress=10)
        db.add_all([terminal, live_task])
        await db.flush()
        live = GenerationRun(status="running", prompt="p", task_id=live_task.id)
        db.add(live)
        await db.commit()
        ids = (missing.id, terminal.id, live.id)

    async with async_session() as db:
        result = await reconcile_stale_generation_runs(db)
        assert result["reconciled"] == 2

    async with async_session() as db:
        rows = {}
        for run_id in ids:
            row = await db.get(GenerationRun, run_id)
            assert row is not None
            rows[run_id] = row
        assert rows[ids[0]].status == "failed"
        assert "任务不存在" in (rows[ids[0]].error or "")
        assert rows[ids[1]].status == "failed"
        assert "终态" in (rows[ids[1]].error or "")
        assert rows[ids[2]].status == "running"
