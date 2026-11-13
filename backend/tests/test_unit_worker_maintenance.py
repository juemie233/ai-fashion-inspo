"""`app/worker.py` 的启动期维护任务与入口函数测试。

worker 每次启动都会做一轮「打扫」：清理过期按天分析日志、清理过期任务运行台账、
把「任务已结束/不存在但台账仍 running」的孤儿台账收尾。这些动作**失败只告警不阻断
启动**，所以一旦写错，问题会静默累积：日志撑爆磁盘、运行记录永远显示「进行中」。
`main()` 的顺序也有讲究（先建表/补列，再清理，再起心跳与主循环）。

以前这些函数没有被单测覆盖，出错只有下次出问题才会发现。
"""

import asyncio
import os
from datetime import datetime, timedelta

import pytest

from app import worker as wk
from app.database import async_session
from app.models.task import TaskQueue
from app.models.task_run_log import TaskRunLog
from app.utils.time import utcnow


class _FakeLogger:
    """记录 worker 的日志调用（项目自定义 logging 配置下 caplog 抓不到）。"""

    def __init__(self):
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def _fmt(self, message, args):
        return message % args if args else message

    def info(self, message, *args, **_kw):
        self.infos.append(self._fmt(message, args))

    def warning(self, message, *args, **_kw):
        self.warnings.append(self._fmt(message, args))

    def error(self, message, *args, **_kw):
        self.errors.append(self._fmt(message, args))

    def exception(self, message, *args, **_kw):
        self.errors.append(self._fmt(message, args))

    def debug(self, *_args, **_kw):
        pass


@pytest.fixture
def logs(monkeypatch):
    logger = _FakeLogger()
    monkeypatch.setattr(wk, "logger", logger)
    return logger


@pytest.fixture(autouse=True)
def _clean_watchdog_markers():
    wk._WATCHDOG_CANCELLED.clear()
    yield
    wk._WATCHDOG_CANCELLED.clear()


class TestCleanupOldAnalyzeLogs:
    def _touch(self, path, age_days):
        path.write_text("x", encoding="utf-8")
        ts = datetime.now().timestamp() - age_days * 86400
        os.utime(path, (ts, ts))

    def test_disabled_or_missing_dir_does_nothing(self, tmp_path, monkeypatch, logs):
        monkeypatch.setattr(wk.settings, "log_retention_days", 0)
        old = tmp_path / "analyze-2020-01-01.log"
        self._touch(old, 100)
        monkeypatch.setattr(wk, "_LOG_DIR", tmp_path)

        wk._cleanup_old_analyze_logs()
        assert old.exists()  # 保留期 0 = 不清理

        monkeypatch.setattr(wk.settings, "log_retention_days", 30)
        monkeypatch.setattr(wk, "_LOG_DIR", tmp_path / "不存在")
        wk._cleanup_old_analyze_logs()  # 目录不存在：静默返回

    def test_removes_only_expired_analyze_logs(self, tmp_path, monkeypatch, logs):
        monkeypatch.setattr(wk.settings, "log_retention_days", 30)
        monkeypatch.setattr(wk, "_LOG_DIR", tmp_path)
        expired = tmp_path / "analyze-2020-01-01.log"
        fresh = tmp_path / "analyze-2099-01-01.log"
        unrelated = tmp_path / "backend.log"
        self._touch(expired, 100)
        self._touch(fresh, 1)
        self._touch(unrelated, 100)

        wk._cleanup_old_analyze_logs()

        assert not expired.exists()
        assert fresh.exists()  # 未过期
        assert unrelated.exists()  # 只清 analyze-*.log
        assert any("删除 1 个" in line for line in logs.infos)

    def test_unlink_failure_is_logged_not_raised(self, tmp_path, monkeypatch, logs):
        """文件被占用（Windows 常见）时只告警，下一个文件照常清理。"""
        monkeypatch.setattr(wk.settings, "log_retention_days", 30)
        monkeypatch.setattr(wk, "_LOG_DIR", tmp_path)
        locked = tmp_path / "analyze-2020-01-01.log"
        normal = tmp_path / "analyze-2020-01-02.log"
        self._touch(locked, 100)
        self._touch(normal, 100)

        real_unlink = wk.Path.unlink

        def _unlink(self, *args, **kwargs):
            if self.name == locked.name:
                raise OSError("文件被占用")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(wk.Path, "unlink", _unlink)

        wk._cleanup_old_analyze_logs()  # 不抛

        assert locked.exists()
        assert not normal.exists()
        assert any("清理过期按天日志失败" in line for line in logs.warnings)


class TestCleanupOldRunLogs:
    async def test_disabled_keeps_rows(self, client, monkeypatch):
        async with async_session() as db:
            db.add(
                TaskRunLog(
                    task_id=1,
                    task_type="batch_analyze",
                    started_at=utcnow() - timedelta(days=500),
                    status="success",
                )
            )
            await db.commit()
        monkeypatch.setattr(wk.settings, "task_run_log_retention_days", 0)

        await wk._cleanup_old_run_logs()

        async with async_session() as db:
            assert (await db.execute(__import__("sqlalchemy").select(TaskRunLog))).scalars().all()

    async def test_deletes_only_expired_rows(self, client, monkeypatch, logs):
        monkeypatch.setattr(wk.settings, "task_run_log_retention_days", 90)
        async with async_session() as db:
            db.add_all(
                [
                    TaskRunLog(
                        task_id=1,
                        task_type="batch_analyze",
                        started_at=utcnow() - timedelta(days=200),
                        status="success",
                    ),
                    TaskRunLog(
                        task_id=2,
                        task_type="batch_analyze",
                        started_at=utcnow() - timedelta(days=1),
                        status="running",
                    ),
                ]
            )
            await db.commit()

        await wk._cleanup_old_run_logs()

        from sqlalchemy import select

        async with async_session() as db:
            rows = (await db.execute(select(TaskRunLog.task_id))).scalars().all()
        assert rows == [2]  # 只删超过 90 天的
        assert any("删除 1 条" in line for line in logs.infos)

    async def test_failure_is_logged_not_raised(self, monkeypatch, logs):
        monkeypatch.setattr(wk.settings, "task_run_log_retention_days", 90)

        def _boom():
            raise RuntimeError("database is locked")

        monkeypatch.setattr(wk, "async_session", _boom)

        await wk._cleanup_old_run_logs()  # 不抛

        assert any("清理过期任务运行台账失败" in line for line in logs.warnings)


class TestFinalizeOrphanRunLogs:
    async def _seed(self):
        """造四类台账：任务仍 running / 任务已结束 / 任务行不存在 / 台账已收尾。"""
        async with async_session() as db:
            running_task = TaskQueue(type="batch_analyze", status="running", total=1, done=0)
            done_task = TaskQueue(type="batch_analyze", status="success", total=1, done=1)
            db.add_all([running_task, done_task])
            await db.commit()
            await db.refresh(running_task)
            await db.refresh(done_task)
            logs = [
                TaskRunLog(
                    task_id=running_task.id,
                    task_type="batch_analyze",
                    started_at=utcnow(),
                    status="running",
                ),
                TaskRunLog(
                    task_id=done_task.id,
                    task_type="batch_analyze",
                    started_at=utcnow(),
                    status="running",
                ),
                TaskRunLog(
                    task_id=999999,  # 任务行不存在（历史遗留孤儿）
                    task_type="batch_analyze",
                    started_at=utcnow(),
                    status="running",
                ),
                TaskRunLog(
                    task_id=done_task.id,
                    task_type="batch_analyze",
                    started_at=utcnow() - timedelta(days=3),
                    status="success",
                    finished_at=utcnow() - timedelta(days=3),
                ),
            ]
            db.add_all(logs)
            await db.commit()
            for log in logs:
                await db.refresh(log)
            return [log.id for log in logs]

    async def test_only_orphans_and_finished_tasks_are_finalized(self, client, logs):
        running_id, finished_id, orphan_id, already_done_id = await self._seed()

        await wk._finalize_orphan_run_logs()

        from sqlalchemy import select

        async with async_session() as db:
            rows = {
                row.id: row
                for row in (await db.execute(select(TaskRunLog))).scalars().all()
            }

        assert rows[running_id].status == "running"  # 任务还在跑：不能动
        assert rows[finished_id].status == "failed"  # 任务已结束：台账收尾
        assert rows[finished_id].finished_at is not None
        assert rows[orphan_id].status == "failed"  # 任务行不存在：孤儿收尾
        assert rows[already_done_id].status == "success"  # 已是终态：不动
        assert any("台账收尾" in line for line in logs.warnings)

    async def test_failure_is_logged_not_raised(self, monkeypatch, logs):
        def _boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(wk, "async_session", _boom)

        await wk._finalize_orphan_run_logs()

        assert any("收尾孤儿任务运行台账失败" in line for line in logs.warnings)


class TestRetryPendingWatchdogFinalize:
    async def test_successful_retry_clears_marker(self, monkeypatch):
        retried: list[int] = []

        async def _ok(task_id):
            retried.append(task_id)

        monkeypatch.setattr(wk, "_fail_stalled_task", _ok)
        wk._WATCHDOG_CANCELLED.update({1, 2})

        await wk._retry_pending_watchdog_finalize(set())

        assert retried == [1, 2]
        assert wk._WATCHDOG_CANCELLED == set()

    async def test_failed_retry_keeps_marker_for_next_round(self, monkeypatch, logs):
        async def _boom(_task_id):
            raise RuntimeError("db locked")

        monkeypatch.setattr(wk, "_fail_stalled_task", _boom)
        wk._WATCHDOG_CANCELLED.add(7)

        await wk._retry_pending_watchdog_finalize(set())

        assert wk._WATCHDOG_CANCELLED == {7}  # 标记保留，下一轮继续重试
        assert any("看门狗收尾重试失败" in line for line in logs.errors)

    async def test_skips_tasks_still_running(self, monkeypatch):
        retried: list[int] = []

        async def _record(task_id):
            retried.append(task_id)

        monkeypatch.setattr(wk, "_fail_stalled_task", _record)
        wk._WATCHDOG_CANCELLED.update({1, 2})

        await wk._retry_pending_watchdog_finalize({1})  # 1 还在跑

        assert retried == [2]
        assert wk._WATCHDOG_CANCELLED == {1}


class TestWorkerMain:
    async def test_startup_order_and_heartbeat_cancelled(self, monkeypatch):
        """启动顺序：建表/补列 → 三类清理 → 重置遗留 → 回填 → 心跳 + 主循环。"""
        order: list[str] = []

        async def _init_db():
            order.append("init_db")

        async def _ensure_schema():
            order.append("ensure_schema")

        def _cleanup_analyze():
            order.append("cleanup_analyze")

        async def _cleanup_runs():
            order.append("cleanup_runs")

        async def _finalize_orphans():
            order.append("finalize_orphans")

        async def _reset_stale():
            order.append("reset_stale")

        async def _flush_backfills():
            order.append("flush_backfills")

        async def _heartbeat(_worker_id):
            order.append("heartbeat_start")
            await asyncio.sleep(3600)

        async def _loop(_worker_id):
            order.append("worker_loop")
            raise asyncio.CancelledError

        monkeypatch.setattr(wk, "init_db", _init_db)
        monkeypatch.setattr(wk, "ensure_schema", _ensure_schema)
        monkeypatch.setattr(wk, "_setup_logging", lambda: None)
        monkeypatch.setattr(wk, "_cleanup_old_analyze_logs", _cleanup_analyze)
        monkeypatch.setattr(wk, "_cleanup_old_run_logs", _cleanup_runs)
        monkeypatch.setattr(wk, "_finalize_orphan_run_logs", _finalize_orphans)
        monkeypatch.setattr(wk, "_build_worker_id", lambda: "worker-test")
        monkeypatch.setattr(wk, "_reset_stale_tasks", _reset_stale)
        monkeypatch.setattr(wk, "_flush_pending_backfills_on_startup", _flush_backfills)
        monkeypatch.setattr(wk, "_heartbeat_loop", _heartbeat)
        monkeypatch.setattr(wk, "_worker_loop", _loop)

        created: list[asyncio.Task] = []
        real_create_task = asyncio.create_task

        def _capture(coro, *args, **kwargs):
            task = real_create_task(coro, *args, **kwargs)
            created.append(task)
            return task

        monkeypatch.setattr(wk.asyncio, "create_task", _capture)

        with pytest.raises(asyncio.CancelledError):
            await wk.main()

        # 心跳协程是 create_task 调度的，可能在本轮事件循环尚未开始执行；
        # 只断言确定性顺序（建表 → 三类清理 → 重置 → 回填 → 主循环）。
        assert [step for step in order if step != "heartbeat_start"] == [
            "init_db",
            "ensure_schema",
            "cleanup_analyze",
            "cleanup_runs",
            "finalize_orphans",
            "reset_stale",
            "flush_backfills",
            "worker_loop",
        ]
        # 主循环退出（含取消）时心跳必须被取消，否则进程无法退出。
        # 刚 cancel 时状态是 cancelling（main 不 await 它），因此断言 cancelling()。
        assert created, "未创建心跳任务"
        assert created[0].cancelling() > 0
