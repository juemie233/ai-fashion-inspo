"""服务健康采集（`services/health_service.py`）单测：探测降级、心跳判活、告警阈值。

这段代码是「运维面板」与守护脚本的数据源，三个语义错了界面就会误报：

1. **探测失败必须降级**：前端/ComfyUI 没起时接口要返回 `status: "down"`，
   不能抛 500 把整个运维页面拖死（超时 2 秒的前提就是为这个）；
2. **worker 判活看心跳新鲜度**：只有 `last_heartbeat_at` 在 30 秒内的才算活着；
   心跳为 `None`（从没上报过）不能算活，过期的也不能算；
3. **告警阈值**：磁盘/内存 ≥90% 才告警，`memory` 拿不到时跳过而不是崩。
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.services import health_service as hs
from app.utils.time import utcnow


class _FakeHttpClient:
    """httpx.AsyncClient 的最小替身（异步上下文管理器 + get）。"""

    def __init__(self, *, response=None, error=None):
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, _url):
        if self._error is not None:
            raise self._error
        return self._response


class TestProbeFrontend:
    async def test_ok_returns_latency_and_status(self, monkeypatch):
        monkeypatch.setattr(
            hs.httpx,
            "AsyncClient",
            lambda **_kw: _FakeHttpClient(response=SimpleNamespace(status_code=200)),
        )

        result = await hs._probe_frontend()

        assert result["status"] == "ok"
        assert result["http_status"] == 200
        assert isinstance(result["latency_ms"], int)

    async def test_failure_degrades_without_raising(self, monkeypatch):
        monkeypatch.setattr(
            hs.httpx,
            "AsyncClient",
            lambda **_kw: _FakeHttpClient(error=ConnectionError("x" * 400)),
        )

        result = await hs._probe_frontend()

        assert result["status"] == "down"
        assert result["latency_ms"] is None
        assert len(result["detail"]) <= 120  # 错误原文截断，避免把长栈塞进响应


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _FakeExecuteResult(self._rows)


def _heartbeat(service_id: str, age_seconds: float | None):
    at = None if age_seconds is None else utcnow() - timedelta(seconds=age_seconds)
    return SimpleNamespace(service_id=service_id, last_heartbeat_at=at)


class TestWorkerStatus:
    async def test_fresh_heartbeats_are_alive(self):
        db = _FakeDb(
            [
                _heartbeat("worker-a", 5),
                _heartbeat("worker-b", 12),
                _heartbeat("worker-stale", 120),  # 超 30 秒：判死
                _heartbeat("worker-never", None),  # 从没上报：判死
            ]
        )

        result = await hs._worker_status(db)

        assert result["status"] == "ok"
        assert result["count"] == 2
        assert sorted(result["workers"]) == ["worker-a", "worker-b"]
        assert result["last_heartbeat_at"] is not None  # 带时区后缀的 ISO 串

    async def test_no_fresh_heartbeat_is_down(self):
        db = _FakeDb([_heartbeat("worker-stale", 300), _heartbeat("worker-never", None)])

        result = await hs._worker_status(db)

        assert result == {"status": "down", "count": 0, "last_heartbeat_at": None, "workers": []}

    async def test_empty_table_is_down(self):
        assert (await hs._worker_status(_FakeDb([])))["status"] == "down"


class TestLogsStats:
    @pytest.fixture
    def logs_dir(self, tmp_path, monkeypatch):
        """把 storage_root 指到临时目录，_logs_dir() 就是 tmp/logs。"""
        monkeypatch.setattr(hs.settings, "storage_root", tmp_path)
        target = tmp_path / "logs"
        target.mkdir()
        return target

    def test_missing_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hs.settings, "storage_root", tmp_path / "根本没有")

        result = hs._logs_stats()

        assert result["total_bytes"] == 0
        assert result["files"] == []

    def test_sizes_sorted_desc(self, logs_dir):
        (logs_dir / "big.log").write_bytes(b"x" * 300)
        (logs_dir / "small.log").write_bytes(b"y" * 10)
        nested = logs_dir / "sub"
        nested.mkdir()
        (nested / "nested.log").write_bytes(b"z" * 50)

        result = hs._logs_stats()

        assert result["total_bytes"] == 360
        assert [f["name"] for f in result["files"]] == ["big.log", "nested.log", "small.log"]
        assert result["files"][0]["size_bytes"] == 300

    def test_unreadable_file_is_skipped(self, logs_dir, monkeypatch):
        """stat 报错（文件被占用/权限）只跳过该文件，不能让整个统计失败。"""
        (logs_dir / "locked.log").write_bytes(b"x" * 10)
        (logs_dir / "ok.log").write_bytes(b"y" * 20)

        real_stat = hs.Path.stat

        def _stat(self, *args, **kwargs):
            if self.name == "locked.log":
                raise OSError("被占用")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(hs.Path, "stat", _stat)

        result = hs._logs_stats()

        assert [f["name"] for f in result["files"]] == ["ok.log"]
        assert result["total_bytes"] == 20


class TestComputeAlerts:
    _ok_front = {"status": "ok"}
    _ok_worker = {"status": "ok"}

    def test_all_healthy_has_no_alerts(self):
        assert hs._compute_alerts(self._ok_front, self._ok_worker, {"used_percent": 10}, {"used_percent": 20}) == []

    def test_down_services_are_reported(self):
        alerts = hs._compute_alerts(
            {"status": "down"},
            {"status": "down"},
            {"used_percent": 10},
            {"used_percent": 20},
        )
        assert "前端服务未响应" in alerts
        assert "worker 未运行或心跳超时" in alerts

    def test_resource_thresholds(self):
        alerts = hs._compute_alerts(
            self._ok_front, self._ok_worker, {"used_percent": 95}, {"used_percent": 91}
        )
        assert any("磁盘使用率" in a for a in alerts)
        assert any("内存使用率" in a for a in alerts)

    def test_threshold_boundary(self):
        """恰好 90% 要告警（>= 阈值），89.9% 不告警。"""
        at = hs._compute_alerts(self._ok_front, self._ok_worker, {"used_percent": 90}, None)
        below = hs._compute_alerts(self._ok_front, self._ok_worker, {"used_percent": 89.9}, None)
        assert any("磁盘使用率" in a for a in at)
        assert below == []

    def test_missing_memory_is_skipped(self):
        """非 Windows 或采集失败时 memory 为 None：跳过内存判断，不能崩。"""
        assert hs._compute_alerts(self._ok_front, self._ok_worker, {"used_percent": 1}, None) == []


class TestCollectHealth:
    async def test_aggregates_sections_and_alerts(self, monkeypatch):
        async def _front():
            return {"status": "ok", "latency_ms": 5, "http_status": 200}

        async def _worker(_db):
            return {"status": "ok", "count": 1, "last_heartbeat_at": "t", "workers": ["w"]}

        monkeypatch.setattr(hs, "_probe_frontend", _front)
        monkeypatch.setattr(hs, "_worker_status", _worker)
        monkeypatch.setattr(hs, "disk_usage", lambda _p: {"used_percent": 10.0})
        monkeypatch.setattr(hs, "memory_usage", lambda: {"used_percent": 20.0})
        monkeypatch.setattr(hs, "_logs_stats", lambda: {"dir": "x", "total_bytes": 0, "files": []})

        result = await hs.collect_health(db=None)

        assert result["services"]["backend"] == {"status": "ok"}
        assert result["services"]["frontend"]["status"] == "ok"
        assert result["services"]["worker"]["count"] == 1
        assert result["resources"]["disk"]["used_percent"] == 10.0
        assert result["resources"]["logs"]["total_bytes"] == 0
        assert result["alerts"] == []
        assert result["checked_at"].endswith("Z")  # API 时间格式契约

    async def test_alerts_survive_degraded_probes(self, monkeypatch):
        async def _front():
            return {"status": "down", "latency_ms": None, "detail": "refused"}

        async def _worker(_db):
            return {"status": "down", "count": 0, "last_heartbeat_at": None, "workers": []}

        monkeypatch.setattr(hs, "_probe_frontend", _front)
        monkeypatch.setattr(hs, "_worker_status", _worker)
        monkeypatch.setattr(hs, "disk_usage", lambda _p: {"used_percent": 99.0})
        monkeypatch.setattr(hs, "memory_usage", lambda: None)
        monkeypatch.setattr(hs, "_logs_stats", lambda: {"dir": "x", "total_bytes": 0, "files": []})

        result = await hs.collect_health(db=None)

        assert "前端服务未响应" in result["alerts"]
        assert "worker 未运行或心跳超时" in result["alerts"]
        assert any("磁盘使用率" in a for a in result["alerts"])
