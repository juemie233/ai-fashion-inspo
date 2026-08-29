"""采集进程管理测试：CDP 端口检测 / 活动任务判定 / 异常退出自动续采。

app/services/scraper/process.py（此前无测试覆盖）。
socket 与 HTTP 探测全部打桩，不发真实网络请求；DB 相关用例通过 API 造数。
"""

import asyncio
import json

import pytest

from app.config import settings
from app.services.scraper import process


@pytest.fixture(autouse=True)
def _no_subprocess(monkeypatch):
    """进程管理测试不拉起真实采集子进程。"""
    monkeypatch.setattr(process, "_launch_scraper_process", lambda task_id: None)


@pytest.fixture(autouse=True)
def _clean_process_state():
    """清理模块级进程映射与续采计数，保证用例相互隔离。"""
    yield
    process._scraper_pids.clear()
    process._scraper_retry_count.clear()


@pytest.fixture
def fresh_session(monkeypatch):
    """用 NullPool 独立会话替换共享会话工厂。

    ``_maybe_auto_retry`` 内部用 ``asyncio.run`` 自建事件循环，而共享 engine
    的连接池可能持有 TestClient 事件循环创建的连接——跨 loop 复用会间歇性
    抛 "attached to a different loop"。NullPool 每次新建连接，规避该问题。
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import settings

    def _factory():
        engine = create_async_engine(
            settings.database_url,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()

    monkeypatch.setattr(process, "async_session", _factory)


def _sqlite_conn():
    """直连测试库（同步 sqlite3，与 conftest.clean_state 同一入口）。"""
    import sqlite3

    from app.config import settings

    return sqlite3.connect(str(settings.storage_root.parent / "fashion_inspo.db"))


def _set_task_status(task_id: int, status: str) -> None:
    """同步 sqlite3 直接改任务状态（绕过 API 的状态机校验）。"""
    conn = _sqlite_conn()
    try:
        conn.execute("UPDATE scraper_tasks SET status = ? WHERE id = ?", (status, task_id))
        conn.commit()
    finally:
        conn.close()


def _get_task(task_id: int) -> dict:
    import sqlite3

    conn = _sqlite_conn()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM scraper_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  CDP 端口检测
# ═══════════════════════════════════════════════════════════════


class _FakeSock:
    """假 socket：connect_ex 返回预设结果。"""

    result = 0

    def settimeout(self, _t):
        pass

    def connect_ex(self, _addr):
        return self.result

    def close(self):
        pass


class _FakeCdpResp:
    """假 CDP /json/version 响应。"""

    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self):
        return self._payload


def _patch_cdp(monkeypatch, sock_result: int, browser: str | None, broken=False):
    """打桩 socket 连接与 CDP HTTP 探测。broken=True 时 HTTP 请求抛异常。"""
    monkeypatch.setattr("socket.socket", lambda *a, **k: _FakeSock())
    _FakeSock.result = sock_result
    if browser is None:
        fake_urlopen = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("http err"))
    else:
        fake_urlopen = lambda *_a, **_k: _FakeCdpResp(
            json.dumps({"Browser": browser}).encode()
        )
    if broken:
        fake_urlopen = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("http err"))
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_check_cdp_google_chrome_ok(monkeypatch):
    _patch_cdp(monkeypatch, 0, "Chrome/120.0.0.0")
    ok, detail, is_chrome = process._check_cdp(9222)
    assert ok is True and is_chrome is True
    assert "Chrome" in detail


def test_check_cdp_non_chrome_browser_detected(monkeypatch):
    """端口被 360 等非 Google Chrome 占用：可用但不标记为 Chrome（前端据此警示）。"""
    _patch_cdp(monkeypatch, 0, "360Chrome/20.0")
    ok, detail, is_chrome = process._check_cdp(9222)
    assert ok is True and is_chrome is False
    assert "Google Chrome" in detail


def test_check_cdp_http_probe_failure(monkeypatch):
    """端口可达但调试协议未确认：保守返回非 Chrome，避免误用。"""
    _patch_cdp(monkeypatch, 0, "Chrome/120", broken=True)
    ok, detail, is_chrome = process._check_cdp(9222)
    assert ok is True and is_chrome is False
    assert "未能确认" in detail


def test_check_cdp_port_closed(monkeypatch):
    _patch_cdp(monkeypatch, -1, None)
    ok, detail, is_chrome = process._check_cdp(9222)
    assert ok is False and is_chrome is False
    assert "无响应" in detail


def test_check_cdp_startup_command_assembled(monkeypatch):
    """check_cdp 组装启动命令模板（前端「复制启动命令」依赖）。

    注意：此处不通过 TestClient 调接口——全局打桩 socket.socket 会与
    TestClient portal（Proactor 事件循环自唤醒管道）死锁；接口层仅是
    薄转发，_check_cdp 的探测逻辑已由上方用例覆盖。
    """
    def _fake_check(port, timeout=2.0):
        return True, f"已连接 Chrome (端口 {port})", True

    monkeypatch.setattr(process, "_check_cdp", _fake_check)
    out = asyncio.run(process.check_cdp(9222))
    assert out["available"] is True
    assert out["is_google_chrome"] is True
    assert "--remote-debugging-port=9222" in out["startup_command"]
    assert settings.chrome_executable in out["startup_command"]


# ═══════════════════════════════════════════════════════════════
#  活动任务判定（跨进程权威状态）
# ═══════════════════════════════════════════════════════════════


def _create_task(client, platform="douyin") -> int:
    r = client.post(
        "/api/scraper/tasks",
        json={"platform": platform, "keywords": ["穿搭"], "max_count": 5},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_has_active_tasks_true_for_pending(client, fresh_session):
    task_id = _create_task(client)
    assert asyncio.run(process.has_active_scraper_tasks()) is True
    _set_task_status(task_id, "completed")


def test_has_active_tasks_false_when_all_done(client, fresh_session):
    task_id = _create_task(client)
    _set_task_status(task_id, "completed")
    assert asyncio.run(process.has_active_scraper_tasks()) is False


def test_has_active_tasks_counts_running(client, fresh_session):
    task_id = _create_task(client)
    _set_task_status(task_id, "running")
    assert asyncio.run(process.has_active_scraper_tasks()) is True
    _set_task_status(task_id, "failed")
    assert asyncio.run(process.has_active_scraper_tasks()) is False


# ═══════════════════════════════════════════════════════════════
#  异常退出自动续采
# ═══════════════════════════════════════════════════════════════


def test_auto_retry_skips_cancelled_task(client, fresh_session, monkeypatch):
    """用户已取消的任务不自动续采。"""
    task_id = _create_task(client)
    _set_task_status(task_id, "cancelled")

    launched = []
    monkeypatch.setattr(process, "_launch_scraper_process", lambda tid: launched.append(tid))
    monkeypatch.setattr(process.time, "sleep", lambda *_a: None)

    process._maybe_auto_retry(task_id)
    assert launched == []
    assert task_id not in process._scraper_retry_count


def test_auto_retry_marks_failed_when_exhausted(client, fresh_session, monkeypatch):
    """续采次数用尽：任务标记失败并写入原因，不再永久停留排队中。"""
    from app.config import settings

    task_id = _create_task(client)
    _set_task_status(task_id, "running")
    monkeypatch.setattr(settings, "scraper_task_auto_retry", 0)

    launched = []
    monkeypatch.setattr(process, "_launch_scraper_process", lambda tid: launched.append(tid))
    monkeypatch.setattr(process.time, "sleep", lambda *_a: None)

    process._maybe_auto_retry(task_id)
    assert launched == []
    task = _get_task(task_id)
    assert task["status"] == "failed"
    assert "续采" in task["error"]


def test_auto_retry_relaunches_within_budget(client, fresh_session, monkeypatch):
    """预算内异常退出：重新拉起子进程并累计续采计数。"""
    from app.config import settings

    task_id = _create_task(client)
    _set_task_status(task_id, "running")
    monkeypatch.setattr(settings, "scraper_task_auto_retry", 2)

    launched = []
    monkeypatch.setattr(process, "_launch_scraper_process", lambda tid: launched.append(tid))
    monkeypatch.setattr(process.time, "sleep", lambda *_a: None)

    process._maybe_auto_retry(task_id)
    assert launched == [task_id]
    assert process._scraper_retry_count[task_id] == 1

    # 第二次异常退出：仍在预算内，继续续采
    process._maybe_auto_retry(task_id)
    assert launched == [task_id, task_id]
    assert process._scraper_retry_count[task_id] == 2
