"""supervisor worker 心跳监控（2026-11-02 假死事故加固）。

supervisor 原先对 worker 只判「进程存活」，进程活着但事件循环卡死时不会重启
（事故现场停摆 20 分钟）。现在读任务库 ``service_heartbeats`` 判断心跳年龄。

这里直接加载 supervisor.py（脚本不是包），只测纯函数：心跳年龄读取与 pid 过滤。
"""

import importlib.util
import json
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_PATH = REPO_ROOT / "scripts" / "ops" / "supervisor.py"


@pytest.fixture(scope="module")
def supervisor():
    """按文件路径加载 supervisor 模块（不执行 run()，__name__ 不是 __main__）。"""
    spec = importlib.util.spec_from_file_location("ops_supervisor_under_test", SUPERVISOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def heartbeat_db(tmp_path, supervisor, monkeypatch):
    """临时任务库：只建 service_heartbeats 表，供心跳读取函数使用。"""
    db_path = tmp_path / "fashion_inspo.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE service_heartbeats ("
        " service_id TEXT PRIMARY KEY, service_type TEXT, pid INTEGER,"
        " started_at TEXT, last_heartbeat_at TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(supervisor, "_database_path", lambda: db_path)
    return db_path


def _insert(heartbeat_db: Path, *, pid: int, age_seconds: int) -> None:
    from datetime import UTC, datetime

    stamp = (datetime.now(UTC) - timedelta(seconds=age_seconds)).replace(tzinfo=None)
    conn = sqlite3.connect(heartbeat_db)
    conn.execute(
        "INSERT INTO service_heartbeats "
        "(service_id, service_type, pid, started_at, last_heartbeat_at) "
        "VALUES (?, 'worker', ?, ?, ?)",
        (f"worker-{pid}-x", pid, stamp.isoformat(sep=" "), stamp.isoformat(sep=" ")),
    )
    conn.commit()
    conn.close()


def test_read_worker_heartbeat_age_返回秒数(heartbeat_db, supervisor):
    """读得到心跳：返回与当前时间的差（允许几秒误差）。"""
    _insert(heartbeat_db, pid=9948, age_seconds=42)
    age = supervisor.read_worker_heartbeat_age(9948)
    assert age is not None and 40 <= age <= 60


def test_read_worker_heartbeat_age_按_pid_过滤(heartbeat_db, supervisor):
    """多 worker：只读目标 pid 的心跳，读不到返回 None（不误杀别的进程）。"""
    _insert(heartbeat_db, pid=111, age_seconds=5)
    assert supervisor.read_worker_heartbeat_age(111) is not None
    assert supervisor.read_worker_heartbeat_age(222) is None


def test_read_worker_heartbeat_age_库不存在返回_none(tmp_path, supervisor, monkeypatch):
    """库不存在/pid 为空：返回 None（调用方按启动宽限处理，不触发重启）。"""
    monkeypatch.setattr(supervisor, "_database_path", lambda: tmp_path / "missing.db")
    assert supervisor.read_worker_heartbeat_age(9948) is None
    assert supervisor.read_worker_heartbeat_age(None) is None


def test_env_float_非法值回退默认(supervisor, monkeypatch):
    """WORKER_HEARTBEAT_STALE_SECONDS 配错不应让 supervisor 崩。"""
    monkeypatch.setenv("WORKER_HEARTBEAT_STALE_SECONDS", "not-a-number")
    assert supervisor._env_float("WORKER_HEARTBEAT_STALE_SECONDS", 90.0) == 90.0
    monkeypatch.setenv("WORKER_HEARTBEAT_STALE_SECONDS", "45")
    assert supervisor._env_float("WORKER_HEARTBEAT_STALE_SECONDS", 90.0) == 45.0


def _make_worker_service(supervisor, tmp_path: Path, monkeypatch, *, started_ago: float):
    """构造一个「进程存在」的 worker ManagedService（不真正起进程）。"""
    log = supervisor.RotatingFileWriter(tmp_path / "worker.log", 1024, 1)
    svc = supervisor.ManagedService(
        "worker",
        {"cmd": [], "cwd": tmp_path, "health_url": None, "startup_grace": 5.0},
        log,
    )
    svc.proc = SimpleNamespace(pid=12345, poll=lambda: None)
    svc.started_at = time.time() - started_ago
    return svc, log


def test_worker_健康判定看心跳年龄(supervisor, tmp_path, monkeypatch):
    """进程活着但心跳过期 → 不健康（触发既有 fail_streak 重启链路）。"""
    svc, log = _make_worker_service(supervisor, tmp_path, monkeypatch, started_ago=200)
    try:
        monkeypatch.setattr(supervisor, "read_worker_heartbeat_age", lambda _pid: 10.0)
        assert svc.healthy() is True

        monkeypatch.setattr(supervisor, "read_worker_heartbeat_age", lambda _pid: 500.0)
        assert svc.healthy() is False

        # 读不到心跳（库被锁/还没写过）：启动宽限内视为健康，过了 grace+阈值不健康
        monkeypatch.setattr(supervisor, "read_worker_heartbeat_age", lambda _pid: None)
        assert svc.healthy() is False  # 已运行 200s > 5+90
        svc.started_at = time.time()
        assert svc.healthy() is True  # 刚启动，给宽限
    finally:
        log.close()


def test_状态文件带心跳年龄(supervisor, tmp_path, monkeypatch):
    """service_status.json 的 worker 条目要带 heartbeat_age_seconds（排障证据）。"""
    svc, log = _make_worker_service(supervisor, tmp_path, monkeypatch, started_ago=200)
    status_file = tmp_path / "service_status.json"
    monkeypatch.setattr(supervisor, "STATUS_FILE", status_file)
    monkeypatch.setattr(supervisor, "read_worker_heartbeat_age", lambda _pid: 12.3)
    try:
        supervisor.write_status_file({"worker": svc}, [])
    finally:
        log.close()

    payload = json.loads(status_file.read_text(encoding="utf-8"))
    entry = payload["services"]["worker"]
    assert entry["heartbeat_age_seconds"] == 12.3
    assert entry["pid"] == 12345
