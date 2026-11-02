"""任务运行台账与按天日志 handler 的回归测试。"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from app.database import async_session
from app.models.task import TaskQueue
from app.models.task_run_log import TaskRunLog
from app.utils.daily_log import DailyFileHandler
from app import worker


def test_daily_file_handler_writes_date_suffixed_file(tmp_path):
    handler = DailyFileHandler(tmp_path, prefix="analyze")
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("test.daily_log")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.info("hello")
    handler.close()

    day = datetime.now().strftime("%Y-%m-%d")
    path = tmp_path / f"analyze-{day}.log"
    assert path.exists()
    assert "hello" in path.read_text(encoding="utf-8")


async def test_start_and_finish_run_log(monkeypatch):
    monkeypatch.setattr(worker, "_gpu_model", lambda: "Test GPU 24GB")
    async with async_session() as db:
        task = TaskQueue(
            type="batch_analyze",
            status="running",
            total=10,
            done=4,
            retry_count=1,
            result={},
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        run_log_id = await worker._start_run_log(db, task)
        assert run_log_id is not None
        await worker._finish_run_log(
            db, run_log_id, status="success", item_count=task.done
        )
        row = await db.get(TaskRunLog, run_log_id)
        assert row is not None
        assert row.task_id == task.id
        assert row.task_type == "batch_analyze"
        assert row.attempt == 1
        assert row.item_count == 4
        assert row.total_count == 10
        assert row.status == "success"
        assert row.gpu_model == "Test GPU 24GB"
        assert row.finished_at is not None
        assert row.finished_at >= row.started_at


async def test_run_task_writes_ledger_on_success(monkeypatch):
    """_run_task 成功执行后必须留下一条 success 台账（含 GPU/张数/结束时间）。"""
    monkeypatch.setattr(worker, "_gpu_model", lambda: "Test GPU")
    async with async_session() as db:
        task = TaskQueue(
            type="batch_analyze", status="running", total=3, done=0, retry_count=0, result={}
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    async def fake_handler(db, task) -> None:
        task.done = 3
        await db.commit()

    monkeypatch.setitem(worker.TASK_HANDLERS, "batch_analyze", fake_handler)
    await worker._run_task(task_id)

    async with async_session() as db:
        rows = (
            await db.execute(
                select(TaskRunLog)
                .where(TaskRunLog.task_id == task_id)
                .order_by(TaskRunLog.id.desc())
            )
        ).scalars().all()
        row = rows[0]
        assert row.status == "success"
        assert row.item_count == 3
        assert row.gpu_model == "Test GPU"
        assert row.finished_at is not None


async def test_task_runs_api_返回台账(client):
    """P1-4：``GET /api/tasks/{id}/runs`` 露出台账（此前只能手写 SQL 查）。"""
    from app.utils.time import utcnow

    async with async_session() as db:
        task = TaskQueue(type="batch_analyze", status="failed", total=10, done=4, result={})
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id
        started = utcnow()
        db.add(
            TaskRunLog(
                task_id=task_id,
                task_type="batch_analyze",
                attempt=1,
                started_at=started,
                finished_at=started,
                item_count=4,
                total_count=10,
                status="failed",
                gpu_model="Test GPU",
                error="模拟失败",
            )
        )
        await db.commit()

    response = client.get(f"/api/tasks/{task_id}/runs")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    row = payload[0]
    assert row["task_id"] == task_id
    assert row["attempt"] == 1
    assert row["status"] == "failed"
    assert row["gpu_model"] == "Test GPU"
    assert row["error"] == "模拟失败"
    # 时间必须带时区后缀（前端 new Date() 才能正确转本地时间）
    assert row["started_at"].endswith("Z")
    assert row["finished_at"].endswith("Z")

    # 不存在的任务返回 404（与 /api/tasks/{id} 口径一致）
    assert client.get("/api/tasks/99999999/runs").status_code == 404
