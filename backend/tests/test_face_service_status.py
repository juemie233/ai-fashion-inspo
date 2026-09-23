"""人脸子服务状态接口测试。

该接口让前端**事前**显示「子服务未启动/离线」，而不是点了按钮才拿到 503。
覆盖三种状态：未配置（enabled=false）、不可达（reachable=false）、正常
（reachable=true）；并固定探测超时——状态接口被前端轮询，不能被子服务
卡死拖满默认 30s。
"""

from app.routers.face_scan import FACE_SERVICE_PROBE_TIMEOUT
from app.services.face_client import FaceServiceUnavailableError

STATUS_URL = "/api/face-scan/service-status"


def test_service_status_disabled(client, monkeypatch):
    """未配置 FACE_SERVICE_URL：enabled=false，接口仍返回 200（是查询结果非错误）。"""
    monkeypatch.setattr("app.services.face_client.face_client.base_url", "")
    r = client.get(STATUS_URL)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["reachable"] is False
    assert "未配置" in body["message"]


def test_service_status_unreachable(client, monkeypatch):
    """已配置但探不通：enabled=true / reachable=false，附原因且不抛 503。"""
    monkeypatch.setattr(
        "app.services.face_client.face_client.base_url", "http://127.0.0.1:18889"
    )

    async def boom(method: str, path: str, *, timeout=None, **kwargs):
        raise FaceServiceUnavailableError("连接被拒绝")

    monkeypatch.setattr("app.services.face_client.face_client._request", boom)
    r = client.get(STATUS_URL)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["reachable"] is False
    assert "未启动" in body["message"] or "不可达" in body["message"]


def test_service_status_reachable_with_short_probe_timeout(client, monkeypatch):
    """探通：reachable=true 并带子服务详情；探测用短超时而非默认 30s。"""
    monkeypatch.setattr(
        "app.services.face_client.face_client.base_url", "http://127.0.0.1:18889"
    )
    captured: dict = {}

    async def fake_health(timeout=None):
        captured["timeout"] = timeout
        return {"status": "ok", "model_loaded": True, "registered": 3}

    monkeypatch.setattr("app.services.face_client.face_client.health", fake_health)
    r = client.get(STATUS_URL)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["reachable"] is True
    assert body["detail"]["registered"] == 3
    assert captured["timeout"] == FACE_SERVICE_PROBE_TIMEOUT
