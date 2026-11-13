"""`app/worker.py` 剩余边界路径：认领失败分类、台账写失败容错、看门狗与主循环异常。

`test_unit_worker.py` 已覆盖正常状态机（认领/重试/失败/崩溃恢复），本文件补的是
**异常分支**——这些分支平时不跑，真出现时恰好是「DB 忙 / 磁盘满 / 任务被删」的
故障现场：

- 认领时 SQLite 报的锁以外的错（磁盘 I/O、schema 问题）**不能吞**，要抛出让大家看见；
- 台账写入/收尾失败只能告警 + 回滚，**不能阻断任务执行**（台账是观察手段不是依赖）；
- 看门狗要真的 cancel 卡死任务并标记，收尾失败时标记保留、下一轮重试；
- 主循环单轮异常（DB 瞬时故障）只记日志继续轮询，不能让 worker 进程退出。

注意：`_gpu_model` 带 `lru_cache`，每个用例前后都要 `cache_clear()`，否则互相污染。
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app import worker as wk
from app.database import async_session
from app.models.task import TaskQueue
from app.services.task_runners.common import PermanentTaskError, RecoverableTaskError


class _FakeLogger:
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
def _clean_state():
    wk._RUN_LOG_IDS.clear()
    wk._WATCHDOG_CANCELLED.clear()
    wk._gpu_model.cache_clear()
    yield
    wk._RUN_LOG_IDS.clear()
    wk._WATCHDOG_CANCELLED.clear()
    wk._gpu_model.cache_clear()


def _operational(message: str) -> OperationalError:
    return OperationalError("UPDATE task_queue", {}, Exception(message))


class _Result:
    def __init__(self, *, row=None, rowcount=1):
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row


class _FakeSession:
    """按顺序返回预设 execute 结果；commit/rollback 可配置抛错。"""

    def __init__(self, results=(), *, commit_error=None, rollback_error=None):
        self._results = list(results)
        self._commit_error = commit_error
        self._rollback_error = rollback_error
        self.commits = 0
        self.rollbacks = 0

    def add(self, _row):
        return None

    async def execute(self, _stmt):
        item = self._results.pop(0) if self._results else _Result()
        if isinstance(item, Exception):
            raise item
        return item

    async def commit(self):
        self.commits += 1
        if self._commit_error is not None:
            raise self._commit_error

    async def rollback(self):
        self.rollbacks += 1
        if self._rollback_error is not None:
            raise self._rollback_error

    async def get(self, _model, _pk):
        return None


class _FakeSessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return False


class TestClaimErrorClassification:
    async def test_non_lock_operational_error_propagates(self, monkeypatch):
        """只有 database is locked 才静默跳过；磁盘 I/O 等错误必须抛出。"""
        # 错误要落在「认领 UPDATE」那一步（SELECT 不在 try 内）
        session = _FakeSession([_Result(row=(7,)), _operational("disk I/O error")])
        monkeypatch.setattr(wk, "async_session", lambda: _FakeSessionCtx(session))

        with pytest.raises(OperationalError):
            await wk._claim_next_task("w1")

    async def test_locked_error_is_silently_skipped(self, monkeypatch):
        session = _FakeSession([_Result(row=(7,)), _operational("database is locked")])
        monkeypatch.setattr(wk, "async_session", lambda: _FakeSessionCtx(session))

        assert await wk._claim_next_task("w1") is None

    async def test_lost_race_returns_none_when_update_matches_nothing(self, monkeypatch):
        """查询到任务后、更新前被其它 worker 抢走 → rowcount 0，本轮什么都不执行。"""
        session = _FakeSession([_Result(row=(7,)), _Result(rowcount=0)])
        monkeypatch.setattr(wk, "async_session", lambda: _FakeSessionCtx(session))

        assert await wk._claim_next_task("w1") is None
        assert session.commits == 1  # 更新语句已提交（只是影响 0 行）


class TestGpuModel:
    def test_env_override_wins_and_truncates(self, monkeypatch):
        monkeypatch.setenv("GPU_MODEL", "  " + "X" * 200 + "  ")
        value = wk._gpu_model()
        assert value is not None
        assert value == ("X" * 200)[:128]

    def test_reads_nvidia_smi_and_dedupes(self, monkeypatch):
        monkeypatch.delenv("GPU_MODEL", raising=False)

        def _run(*_args, **_kwargs):
            return SimpleNamespace(returncode=0, stdout="RTX 4090\nRTX 4090\nA100\n")

        monkeypatch.setattr(wk.subprocess, "run", _run)
        assert wk._gpu_model() == "RTX 4090; A100"

    def test_nonzero_exit_or_exception_returns_none(self, monkeypatch):
        monkeypatch.delenv("GPU_MODEL", raising=False)
        monkeypatch.setattr(
            wk.subprocess,
            "run",
            lambda *_a, **_k: SimpleNamespace(returncode=1, stdout=""),
        )
        assert wk._gpu_model() is None

        wk._gpu_model.cache_clear()
        monkeypatch.setattr(
            wk.subprocess,
            "run",
            lambda *_a, **_k: (_ for _ in ()).throw(OSError("nvidia-smi 不存在")),
        )
        assert wk._gpu_model() is None


class TestRunLogFailures:
    async def test_start_run_log_failure_is_swallowed(self, logs):
        """台账写失败（DB 忙/磁盘满）只告警 + 回滚，返回 None 让任务照常执行。"""
        session = _FakeSession(commit_error=RuntimeError("disk full"))
        task = SimpleNamespace(id=3, type="batch_analyze", total=1, retry_count=0)

        assert await wk._start_run_log(session, task) is None

        assert session.rollbacks == 1
        assert any("写任务运行台账失败" in line for line in logs.warnings)

    async def test_start_run_log_rollback_failure_is_swallowed(self, logs):
        session = _FakeSession(
            commit_error=RuntimeError("disk full"),
            rollback_error=RuntimeError("rollback failed too"),
        )
        task = SimpleNamespace(id=3, type="batch_analyze", total=1, retry_count=0)

        assert await wk._start_run_log(session, task) is None  # 两层异常都不外泄

    async def test_finish_run_log_noop_cases(self):
        session = _FakeSession()
        await wk._finish_run_log(session, None, status="success", item_count=1)
        await wk._finish_run_log(session, 42, status="success", item_count=1)  # row 不存在
        assert session.commits == 0

    async def test_finish_run_log_failure_is_swallowed(self, logs):
        class _BoomDb(_FakeSession):
            async def get(self, _model, _pk):
                raise RuntimeError("db gone")

        session = _BoomDb(rollback_error=RuntimeError("rollback failed"))

        await wk._finish_run_log(session, 7, status="failed", item_count=0, error="x")

        assert any("写任务运行台账收尾失败" in line for line in logs.warnings)


class TestRunTaskVanishedDuringFailure:
    """失败处理前任务已被删/被外部改走：`_reload_for_failure` 返回 None 时必须直接退出。"""

    async def _task(self) -> int:
        async with async_session() as db:
            task = TaskQueue(type="batch_analyze", status="running", total=1, done=0)
            db.add(task)
            await db.commit()
            return task.id

    @pytest.mark.parametrize(
        "error",
        [RecoverableTaskError("Ollama 宕机"), PermanentTaskError("图片损坏"), RuntimeError("超时")],
    )
    async def test_returns_early_when_reload_finds_nothing(self, monkeypatch, error):
        async def _handler(_db, _task):
            raise error

        monkeypatch.setitem(wk.TASK_HANDLERS, "batch_analyze", _handler)

        async def _gone(_db, _task_id):
            return None

        monkeypatch.setattr(wk, "_reload_for_failure", _gone)
        task_id = await self._task()

        await wk._run_task(task_id)  # 不抛

        async with async_session() as db:
            row = await db.get(TaskQueue, task_id)
        assert row.status == "running"  # 没有收尾写入（任务行已无人负责）


class TestFailStalledTask:
    async def test_missing_task_is_noop(self, logs):
        await wk._fail_stalled_task(999999)  # 不抛、不写库

    async def test_running_task_is_marked_failed(self, client):
        async with async_session() as db:
            task = TaskQueue(type="batch_analyze", status="running", total=1, done=0)
            db.add(task)
            await db.commit()
            task_id = task.id

        await wk._fail_stalled_task(task_id)

        async with async_session() as db:
            row = await db.get(TaskQueue, task_id)
        assert row.status == "failed"
        assert "看门狗" in (row.error or "")
        assert row.next_retry_at is None


class TestHeartbeatLoop:
    async def test_cancellation_propagates(self, monkeypatch):
        async def _cancel(_worker_id):
            raise asyncio.CancelledError

        monkeypatch.setattr(wk, "_write_heartbeat", _cancel)

        with pytest.raises(asyncio.CancelledError):
            await wk._heartbeat_loop("w1")

    async def test_write_failure_is_logged_and_loop_continues(self, monkeypatch, logs):
        async def _boom(_worker_id):
            raise RuntimeError("db locked")

        calls = {"n": 0}

        async def _sleep(_seconds):
            calls["n"] += 1
            raise asyncio.CancelledError

        monkeypatch.setattr(wk, "_write_heartbeat", _boom)
        monkeypatch.setattr(wk.asyncio, "sleep", _sleep)

        with pytest.raises(asyncio.CancelledError):
            await wk._heartbeat_loop("w1")

        assert any("心跳写入失败" in line for line in logs.warnings)
        assert calls["n"] == 1  # 失败后仍进入 sleep（继续循环）


class TestWorkerLoopBoundaries:
    @staticmethod
    def _patch_monotonic(monkeypatch):
        """让 monotonic 每次 +31s：触发每轮 stale 清扫与看门狗检查。"""
        state = {"now": 0.0}

        def _monotonic():
            state["now"] += 31.0
            return state["now"]

        monkeypatch.setattr(wk.time, "monotonic", _monotonic)

    async def test_watchdog_cancels_stalled_task_and_retries_finalize(self, monkeypatch, logs):
        async def _noop():
            return None

        async def _noop_async(*_args, **_kwargs):
            return None

        async def _never(_task_id):
            await asyncio.sleep(3600)

        claims = {"n": 0}

        async def _claim(_worker_id):
            claims["n"] += 1
            return 1 if claims["n"] == 1 else None

        stalled_calls = {"n": 0}

        async def _stalled(_worker_id, _stall_seconds):
            stalled_calls["n"] += 1
            return [1] if stalled_calls["n"] >= 2 else []

        retried: list[set[int]] = []

        async def _retry(running_ids):
            retried.append(set(running_ids))

        monkeypatch.setattr(wk, "_reset_stale_tasks", _noop)
        monkeypatch.setattr(wk, "_claim_next_task", _claim)
        monkeypatch.setattr(wk, "_run_task_safe", _never)
        monkeypatch.setattr(wk, "_stalled_task_ids", _stalled)
        monkeypatch.setattr(wk, "_retry_pending_watchdog_finalize", _retry)
        monkeypatch.setattr(wk.settings, "task_stall_timeout_seconds", 60)
        monkeypatch.setitem(wk.TASK_HANDLERS, "batch_analyze", _noop_async)
        self._patch_monotonic(monkeypatch)

        sleeps = {"n": 0}

        async def _sleep(_seconds):
            sleeps["n"] += 1
            if sleeps["n"] >= 3:
                raise asyncio.CancelledError

        monkeypatch.setattr(wk.asyncio, "sleep", _sleep)

        with pytest.raises(asyncio.CancelledError):
            await wk._worker_loop("w1")

        assert 1 in wk._WATCHDOG_CANCELLED  # 已标记看门狗取消
        assert stalled_calls["n"] >= 2
        assert retried  # 每轮都重试收尾
        assert any("看门狗" in line for line in logs.errors)

    async def test_loop_exception_is_logged_and_keeps_polling(self, monkeypatch, logs):
        async def _boom():
            raise RuntimeError("db transient failure")

        sleeps = {"n": 0}

        async def _sleep(_seconds):
            sleeps["n"] += 1
            if sleeps["n"] >= 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(wk, "_reset_stale_tasks", _boom)
        self._patch_monotonic(monkeypatch)
        monkeypatch.setattr(wk.asyncio, "sleep", _sleep)

        with pytest.raises(asyncio.CancelledError):
            await wk._worker_loop("w1")

        assert any("worker 主循环异常" in line for line in logs.errors)
        assert sleeps["n"] == 2  # 异常后没有退出，继续进入下一轮
