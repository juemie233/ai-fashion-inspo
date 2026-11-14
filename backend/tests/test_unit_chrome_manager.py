"""采集专用 Chrome 生命周期管理（`services/chrome_manager.py`）测试。

这是「自动拉起调试模式 Chrome」的全部逻辑，原覆盖率 22.3%。它没有被单测覆盖时，
坏掉的后果都是**采集整体不可用**且难以定位：

- 端口被别的 Chrome 占用 → 必须报 `port_conflict` 而不是假装启动成功；
- 启动超时 → 要返回明确原因（路径配置错），不能让前端一直转；
- 崩溃自动重启要有上限（否则死循环拉起），空闲超时要先查 DB 里的活动任务
  （多 worker / API 重启后不能把正在跑采集的 Chrome 关掉）；
- 停止必须级联杀进程树（Chrome 派生大量子进程），失败只告警。

本文件用假进程 / 假探测覆盖这些分支，不起真实 Chrome。
"""

from types import SimpleNamespace

import pytest

from app.services import chrome_manager as cm


@pytest.fixture
def manager(monkeypatch):
    manager = cm.ChromeManager()
    monkeypatch.setattr(cm.settings, "chrome_debug_port", 9222)
    monkeypatch.setattr(cm.settings, "chrome_executable", "C:/chrome.exe")
    monkeypatch.setattr(cm.settings, "chrome_user_data_dir", "C:/tmp/chrome-profile")
    monkeypatch.setattr(cm.settings, "chrome_startup_timeout", 10)
    monkeypatch.setattr(cm.settings, "chrome_auto_restart_limit", 2)
    monkeypatch.setattr(cm.settings, "chrome_idle_timeout", 0)
    monkeypatch.setattr(cm.scraper_service, "_scraper_pids", {})
    return manager


def _proc(pid=4321, poll_result=None):
    return SimpleNamespace(pid=pid, poll=lambda: poll_result)


class TestStart:
    def test_already_running_returns_status_with_pid(self, manager, monkeypatch):
        monkeypatch.setattr(manager, "_check", lambda _port: (True, "已运行", True))
        manager._proc = _proc(pid=111)

        assert manager.start() == {"state": "running", "detail": "已运行", "pid": 111}

    def test_already_running_external_has_no_pid(self, manager, monkeypatch):
        monkeypatch.setattr(manager, "_check", lambda _port: (True, "已运行", True))

        assert manager.start()["pid"] is None

    def test_port_conflict_before_launch(self, manager, monkeypatch):
        monkeypatch.setattr(manager, "_check", lambda _port: (True, "360 极速浏览器", False))
        calls: list = []
        monkeypatch.setattr(cm.subprocess, "Popen", lambda *_a, **_k: calls.append(1))

        result = manager.start()

        assert result == {"state": "port_conflict", "detail": "360 极速浏览器", "pid": None}
        assert calls == []  # 端口被占：不能启动新 Chrome

    def test_popen_failure_returns_not_started(self, manager, monkeypatch):
        monkeypatch.setattr(manager, "_check", lambda _port: (False, "未启动", False))
        monkeypatch.setattr(
            cm.subprocess, "Popen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("找不到 chrome.exe"))
        )

        result = manager.start()

        assert result["state"] == "not_started"
        assert "启动 Chrome 失败" in result["detail"]

    def test_successful_start_clears_flag_and_starts_monitor(self, manager, monkeypatch):
        checks = iter([(False, "未启动", False), (True, "ok", True)])
        monkeypatch.setattr(manager, "_check", lambda _port: next(checks))
        monkeypatch.setattr(cm.subprocess, "Popen", lambda cmd: _proc(pid=555))
        monkeypatch.setattr(cm.time, "sleep", lambda _s: None)
        started: list[bool] = []
        monkeypatch.setattr(manager, "_start_monitor", lambda: started.append(True))
        manager._stop_flag.set()
        manager._restart_count = 9

        result = manager.start()

        assert result == {"state": "running", "detail": "ok", "pid": 555}
        assert started == [True]
        assert manager._stop_flag.is_set() is False  # 启动前已清空
        assert manager._restart_count == 0

    def test_port_conflict_during_startup_reports_owner_pid(self, manager, monkeypatch):
        checks = iter([(False, "未启动", False), (True, "别的浏览器", False)])
        monkeypatch.setattr(manager, "_check", lambda _port: next(checks))
        monkeypatch.setattr(cm.subprocess, "Popen", lambda cmd: _proc(pid=777))
        monkeypatch.setattr(cm.time, "sleep", lambda _s: None)

        result = manager.start()

        assert result == {"state": "port_conflict", "detail": "别的浏览器", "pid": 777}

    def test_startup_timeout_reports_reason(self, manager, monkeypatch):
        monkeypatch.setattr(manager, "_check", lambda _port: (False, "未启动", False))
        monkeypatch.setattr(cm.subprocess, "Popen", lambda cmd: _proc(pid=888))
        monkeypatch.setattr(cm.time, "sleep", lambda _s: None)
        # 第一次 time.time 用于算 deadline，之后立刻越过 deadline
        clock = iter([100.0, 50.0, 200.0, 200.0])
        monkeypatch.setattr(cm.time, "time", lambda: next(clock))

        result = manager.start()

        assert result["state"] == "not_started"
        assert "启动超时" in result["detail"]
        assert result["pid"] == 888


class TestStop:
    def test_stopping_external_chrome_is_noop(self, manager):
        result = manager.stop()

        assert result == {
            "state": "not_started",
            "detail": "Chrome 非本服务启动，无需停止",
            "pid": None,
        }
        assert manager._stop_flag.is_set() is True  # 监控线程要能感知停止

    def test_stop_kills_process_tree_and_resets(self, manager, monkeypatch):
        killed: list = []
        monkeypatch.setattr(
            cm.subprocess, "run", lambda args, **_k: killed.append(args) or SimpleNamespace(returncode=0)
        )
        manager._proc = _proc(pid=123)
        manager._restart_count = 2

        result = manager.stop()

        assert killed == [["taskkill", "/F", "/T", "/PID", "123"]]  # 级联杀进程树
        assert result == {"state": "not_started", "detail": "Chrome 已停止", "pid": 123}
        assert manager._proc is None
        assert manager._restart_count == 0

    def test_taskkill_failure_is_logged_but_proc_cleared(self, manager, monkeypatch):
        def _boom(*_a, **_k):
            raise OSError("拒绝访问")

        monkeypatch.setattr(cm.subprocess, "run", _boom)
        manager._proc = _proc(pid=9)

        result = manager.stop()  # 不抛

        assert result["pid"] == 9
        assert manager._proc is None


class TestStatus:
    def test_self_managed_process_alive(self, manager, monkeypatch):
        monkeypatch.setattr(manager, "_check", lambda _port: (True, "ok", True))
        manager._proc = _proc(pid=31, poll_result=None)

        assert manager.status() == {"state": "running", "detail": "ok", "pid": 31}

    def test_exited_process_falls_back_to_external_probe(self, manager, monkeypatch):
        monkeypatch.setattr(manager, "_check", lambda _port: (True, "ok", True))
        manager._proc = _proc(pid=31, poll_result=1)

        assert manager.status() == {"state": "running", "detail": "ok（外部启动）", "pid": None}

    def test_port_conflict_and_not_started(self, manager, monkeypatch):
        monkeypatch.setattr(manager, "_check", lambda _port: (True, "360", False))
        assert manager.status()["state"] == "port_conflict"

        monkeypatch.setattr(manager, "_check", lambda _port: (False, "未启动", False))
        assert manager.status() == {
            "state": "not_started",
            "detail": "Chrome 未启动",
            "pid": None,
        }


class TestHelpers:
    def test_check_delegates_to_scraper_service(self, manager, monkeypatch):
        monkeypatch.setattr(cm.scraper_service, "_check_cdp", lambda port: (True, f"port={port}", True))

        assert manager._check(9333) == (True, "port=9333", True)

    def test_build_cmd_uses_settings(self, manager):
        assert manager._build_cmd() == [
            "C:/chrome.exe",
            "--remote-debugging-port=9222",
            "--user-data-dir=C:/tmp/chrome-profile",
        ]

    def test_start_monitor_reuses_alive_thread(self, manager, monkeypatch):
        alive = SimpleNamespace(is_alive=lambda: True, start=lambda: pytest.fail("不应重启"))
        manager._monitor_thread = alive

        manager._start_monitor()

        assert manager._monitor_thread is alive

    def test_start_monitor_starts_daemon_thread(self, manager, monkeypatch):
        created: list[SimpleNamespace] = []

        class _FakeThread:
            def __init__(self, target, daemon, name):
                self.target = target
                self.daemon = daemon
                self.name = name
                self.started = False
                created.append(self)

            def is_alive(self):
                return False

            def start(self):
                self.started = True

        monkeypatch.setattr(cm.threading, "Thread", _FakeThread)

        manager._start_monitor()

        assert created and created[0].started is True
        assert created[0].daemon is True
        assert created[0].name == "chrome-monitor"


class TestMonitor:
    """直接在调用线程里跑 `_monitor`：用假 sleep 推进循环（不起真线程）。"""

    def _run(self, manager, monkeypatch, *, sleep_hook, poll_result=None, check=(False, "", False)):
        monkeypatch.setattr(cm.time, "sleep", sleep_hook)
        monkeypatch.setattr(cm.time, "time", lambda: 1000.0)
        monkeypatch.setattr(manager, "_check", lambda _port: check)
        manager._proc = _proc(pid=1, poll_result=poll_result)
        manager._monitor()

    def test_no_process_exits_immediately(self, manager, monkeypatch):
        manager._proc = None
        monkeypatch.setattr(cm.time, "sleep", lambda _s: None)

        manager._monitor()  # 不阻塞、不崩

    def test_exited_process_but_port_still_served_by_chrome(self, manager, monkeypatch):
        def _sleep(_s):
            return None

        self._run(manager, monkeypatch, sleep_hook=_sleep, poll_result=1, check=(True, "ok", True))

        assert manager._proc is None  # 视为存活：丢掉已退出句柄

    def test_crashed_process_is_restarted_under_limit(self, manager, monkeypatch):
        pops: list[list[str]] = []

        calls = {"n": 0}

        def _sleep(_s):
            calls["n"] += 1
            if calls["n"] >= 2:  # 第一次 sleep 后要完成重启，第二次再退出循环
                manager._stop_flag.set()

        def _popen(cmd):
            pops.append(cmd)
            return _proc(pid=2)

        monkeypatch.setattr(cm.subprocess, "Popen", _popen)
        self._run(manager, monkeypatch, sleep_hook=_sleep, poll_result=1)

        assert manager._restart_count == 1
        assert pops and pops[0] == manager._build_cmd()

    def test_restart_limit_reached_clears_proc(self, manager, monkeypatch):
        manager._restart_count = 2  # 已达上限（limit=2）

        def _sleep(_s):
            return None

        monkeypatch.setattr(cm.subprocess, "Popen", lambda *_a: pytest.fail("不应再重启"))
        self._run(manager, monkeypatch, sleep_hook=_sleep, poll_result=1)

        assert manager._proc is None
        assert manager._restart_count == 2

    def test_idle_timeout_stops_chrome_when_no_active_tasks(self, manager, monkeypatch):
        monkeypatch.setattr(cm.settings, "chrome_idle_timeout", 1)
        # idle_since = 1000；time.time 固定 1000 → 差值 0 < 1，先进入「活动」判断。
        # 为了让空闲判定成立，让第二次 time.time 返回 1000+2。
        clock = iter([1000.0, 1000.0, 1002.0, 1002.0, 1002.0])
        monkeypatch.setattr(cm.time, "time", lambda: next(clock))
        stopped: list[bool] = []
        monkeypatch.setattr(manager, "stop", lambda: stopped.append(True))

        async def _has_active():
            return False

        monkeypatch.setattr(cm.scraper_service, "has_active_scraper_tasks", _has_active)

        def _sleep(_s):
            return None

        monkeypatch.setattr(cm.time, "sleep", _sleep)
        monkeypatch.setattr(manager, "_check", lambda _port: (False, "", False))
        manager._proc = _proc(pid=1, poll_result=None)

        manager._monitor()

        assert stopped == [True]

    def test_db_query_failure_is_treated_as_active(self, manager, monkeypatch):
        """查 DB 失败时按「有活动」处理：宁可多开一会儿也不能打断采集。"""
        monkeypatch.setattr(cm.settings, "chrome_idle_timeout", 1)

        async def _boom():
            raise RuntimeError("db locked")

        monkeypatch.setattr(cm.scraper_service, "has_active_scraper_tasks", _boom)
        stopped: list[bool] = []
        monkeypatch.setattr(manager, "stop", lambda: stopped.append(True))

        calls = {"n": 0}

        def _sleep(_s):
            calls["n"] += 1
            if calls["n"] >= 2:
                manager._stop_flag.set()

        monkeypatch.setattr(cm.time, "sleep", _sleep)
        monkeypatch.setattr(cm.time, "time", lambda: 2000.0)
        monkeypatch.setattr(manager, "_check", lambda _port: (False, "", False))
        manager._proc = _proc(pid=1, poll_result=None)

        manager._monitor()

        assert stopped == []  # 查询失败 → 视为有活动，不关闭

    def test_in_process_pids_skip_db_query(self, manager, monkeypatch):
        monkeypatch.setattr(cm.settings, "chrome_idle_timeout", 1)
        monkeypatch.setattr(cm.scraper_service, "_scraper_pids", {1: "task"})
        called: list[bool] = []

        async def _has_active():
            called.append(True)
            return False

        monkeypatch.setattr(cm.scraper_service, "has_active_scraper_tasks", _has_active)
        stopped: list[bool] = []
        monkeypatch.setattr(manager, "stop", lambda: stopped.append(True))

        calls = {"n": 0}

        def _sleep(_s):
            calls["n"] += 1
            if calls["n"] >= 2:
                manager._stop_flag.set()

        monkeypatch.setattr(cm.time, "sleep", _sleep)
        monkeypatch.setattr(cm.time, "time", lambda: 2000.0)
        monkeypatch.setattr(manager, "_check", lambda _port: (False, "", False))
        manager._proc = _proc(pid=1, poll_result=None)

        manager._monitor()

        assert called == []  # 进程内有采集 pid：无需查库
        assert stopped == []
