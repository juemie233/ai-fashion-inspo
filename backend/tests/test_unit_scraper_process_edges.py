"""采集进程管理补充测试：子进程拉起/回收、启动失败降级、续采异常分支。

覆盖 ``app/services/scraper/process.py`` 既有 test_scraper_process.py 之外的
分支：`_launch_scraper_process`（子进程参数、句柄回收、退出码分流）、
`_safe_launch`（拉起失败置 failed）、`_maybe_auto_retry` 的加载失败/已拉起/
拉起再失败/标记失败异常分支，以及 `_check_cdp` 的超时与通用异常。

不启动真实子进程、不触碰真实文件：Popen / Thread / open / Path 全部打桩。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.services.scraper import process


@pytest.fixture(autouse=True)
def _clean_process_state():
    """清理模块级进程映射与续采计数，保证用例相互隔离。"""
    yield
    process._scraper_pids.clear()
    process._scraper_retry_count.clear()


class _FakePath:
    """最小 Path 替身：只支持 ``/``、``parent``、``mkdir``（避免真实建目录）。"""

    def __init__(self, parts: tuple[str, ...] = ()):
        self._parts = parts

    @property
    def parent(self) -> "_FakePath":
        return _FakePath(self._parts[:-1])

    def __truediv__(self, other: object) -> "_FakePath":
        return _FakePath((*self._parts, str(other)))

    def mkdir(self, **_kw: object) -> None:
        return None


class _FakeLogFile:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    """假子进程：wait() 返回预设退出码。"""

    def __init__(self, pid: int, returncode: int) -> None:
        self.pid = pid
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


class _ThreadCollector:
    """假 Thread：记录 target 但不真正启动线程。"""

    instances: list["_ThreadCollector"] = []

    def __init__(self, target=None, daemon=None, **_kw: object) -> None:
        self.target = target
        self.daemon = daemon
        _ThreadCollector.instances.append(self)

    def start(self) -> None:
        return None


def _patch_launch_env(monkeypatch, proc: _FakeProc, log_f: _FakeLogFile):
    """打桩 Popen / Thread / open / Path，返回记录容器。"""
    _ThreadCollector.instances = []
    recorded: dict = {"popens": [], "opens": []}

    def _fake_popen(args, **kwargs):
        recorded["popens"].append((args, kwargs))
        return proc

    def _fake_open(path, *args, **kwargs):
        recorded["opens"].append((path, args, kwargs))
        return log_f

    monkeypatch.setattr(process, "Path", _FakePath)
    monkeypatch.setattr(process, "open", _fake_open, raising=False)
    monkeypatch.setattr(process.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(process.threading, "Thread", _ThreadCollector)
    return recorded


def test_launch_arguments_and_reap_success(monkeypatch):
    """正常退出（0）：登记/回收 pid，并重置续采计数。"""
    proc = _FakeProc(pid=4321, returncode=0)
    log_f = _FakeLogFile()
    recorded = _patch_launch_env(monkeypatch, proc, log_f)
    retried: list[int] = []
    monkeypatch.setattr(process, "_maybe_auto_retry", retried.append)
    process._scraper_retry_count[7] = 2

    process._launch_scraper_process(7)

    args, kwargs = recorded["popens"][0]
    assert args[1:] == ["-u", "-m", "scripts.run_scraper", "7"]
    assert kwargs["stdout"] is log_f
    assert kwargs["stderr"] is process.subprocess.STDOUT
    assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
    assert process._scraper_pids[7] == 4321
    assert recorded["opens"][0][1] == ("w",)

    thread = _ThreadCollector.instances[0]
    assert thread.daemon is True
    thread.target()  # 手动触发回收回调（不真正起线程）

    assert log_f.closed is True
    assert 7 not in process._scraper_pids
    assert 7 not in process._scraper_retry_count
    assert retried == []


def test_reap_abnormal_exit_triggers_auto_retry(monkeypatch):
    """非 0 退出：调用 _maybe_auto_retry，保留 pid 映射清理。"""
    proc = _FakeProc(pid=9, returncode=3)
    _patch_launch_env(monkeypatch, proc, _FakeLogFile())
    seen: list[int] = []
    monkeypatch.setattr(process, "_maybe_auto_retry", seen.append)

    process._launch_scraper_process(11)
    _ThreadCollector.instances[0].target()

    assert seen == [11]
    assert 11 not in process._scraper_pids


def test_safe_launch_marks_task_failed_on_error(monkeypatch):
    """子进程拉起失败：任务置 failed 并落错误原因，不能永久停留 pending。"""

    def _boom(_task_id: int) -> None:
        raise OSError("no python")

    class _FakeDb:
        def __init__(self) -> None:
            self.commits = 0

        async def commit(self) -> None:
            self.commits += 1

    db = _FakeDb()
    task = SimpleNamespace(id=5, status="pending", error=None)
    monkeypatch.setattr(process, "_launch_scraper_process", _boom)

    asyncio.run(process._safe_launch(db, task))

    assert task.status == "failed"
    assert "采集进程启动失败" in task.error and "no python" in task.error
    assert db.commits == 1


def test_safe_launch_success_keeps_task_untouched(monkeypatch):
    """拉起成功：不改任务状态、不额外提交。"""
    launched: list[int] = []
    monkeypatch.setattr(process, "_launch_scraper_process", launched.append)

    class _FakeDb:
        def __init__(self) -> None:
            self.commits = 0

        async def commit(self) -> None:
            self.commits += 1

    db = _FakeDb()
    task = SimpleNamespace(id=6, status="pending", error=None)

    asyncio.run(process._safe_launch(db, task))

    assert launched == [6]
    assert task.status == "pending" and db.commits == 0


def test_auto_retry_load_failure_aborts(monkeypatch):
    """加载任务失败（DB 不可用）：放弃本次续采并记录告警。"""

    def _boomed(coro):
        coro.close()
        raise RuntimeError("db down")

    launched: list[int] = []
    monkeypatch.setattr(process.asyncio, "run", _boomed)
    monkeypatch.setattr(process, "_launch_scraper_process", launched.append)

    process._maybe_auto_retry(1)

    assert launched == []


def test_auto_retry_skips_when_task_missing(monkeypatch):
    """任务已被删除（task is None）：不续采。"""
    def _closed_run(coro):
        coro.close()
        return None

    monkeypatch.setattr(process.asyncio, "run", _closed_run)
    launched: list[int] = []
    monkeypatch.setattr(process, "_launch_scraper_process", launched.append)

    process._maybe_auto_retry(2)

    assert launched == []


def test_auto_retry_skips_when_already_running(monkeypatch):
    """等待期间已被其它入口重新拉起（pid 在映射里）：不重复启动，但计数已累计。"""
    task = SimpleNamespace(id=3, status="running")

    def _run_task(coro):
        coro.close()
        return task

    monkeypatch.setattr(process.asyncio, "run", _run_task)
    monkeypatch.setattr(process.time, "sleep", lambda *_a: None)
    launched: list[int] = []
    monkeypatch.setattr(process, "_launch_scraper_process", launched.append)
    process._scraper_pids[3] = 123

    process._maybe_auto_retry(3)

    assert launched == []
    assert process._scraper_retry_count[3] == 1


def test_auto_retry_relaunch_failure_is_logged(monkeypatch):
    """续采再次拉起失败：异常被吞掉（只记日志），不向上抛。"""
    task = SimpleNamespace(id=4, status="running")

    def _run_task(coro):
        coro.close()
        return task

    monkeypatch.setattr(process.asyncio, "run", _run_task)
    monkeypatch.setattr(process.time, "sleep", lambda *_a: None)

    def _boom(_task_id: int) -> None:
        raise OSError("spawn fail")

    monkeypatch.setattr(process, "_launch_scraper_process", _boom)

    process._maybe_auto_retry(4)  # 不应抛异常

    assert process._scraper_retry_count[4] == 1


def test_auto_retry_mark_failed_exception_is_swallowed(monkeypatch):
    """预算耗尽后标记失败本身失败：只记告警，不影响调用方。"""
    from app.config import settings

    task = SimpleNamespace(id=8, status="running")
    calls: list[int] = []

    def _fake_run(coro):
        calls.append(1)
        if len(calls) == 1:
            coro.close()
            return task
        coro.close()
        raise RuntimeError("db gone")

    monkeypatch.setattr(process.asyncio, "run", _fake_run)
    monkeypatch.setattr(settings, "scraper_task_auto_retry", 0)
    launched: list[int] = []
    monkeypatch.setattr(process, "_launch_scraper_process", launched.append)

    process._maybe_auto_retry(8)

    assert launched == []
    assert len(calls) == 2  # 第二次是 _mark_failed（抛错被吞）


class _FakeSockTimeout:
    def settimeout(self, _timeout):
        pass

    def connect_ex(self, _addr):
        raise TimeoutError("timed out")

    def close(self):
        pass


class _FakeSockError:
    def settimeout(self, _timeout):
        pass

    def connect_ex(self, _addr):
        raise RuntimeError("socket exploded")

    def close(self):
        pass


def test_check_cdp_timeout_branch(monkeypatch):
    """socket 连接超时：返回不可用并给出超时说明。"""
    monkeypatch.setattr(process.socket, "socket", lambda *_a, **_k: _FakeSockTimeout())

    ok, detail, is_chrome = process._check_cdp(9333)

    assert ok is False and is_chrome is False
    assert "连接超时" in detail


def test_check_cdp_unexpected_error_branch(monkeypatch):
    """socket 层意外异常：降级为不可用，信息带原始错误。"""
    monkeypatch.setattr(process.socket, "socket", lambda *_a, **_k: _FakeSockError())

    ok, detail, is_chrome = process._check_cdp(9333)

    assert ok is False and is_chrome is False
    assert "端口检测异常" in detail and "socket exploded" in detail
