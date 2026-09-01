"""健康检查与 schema 版本握手。"""

import pytest


@pytest.fixture(autouse=True)
def _no_frontend_probe(monkeypatch):
    """健康检查探测前端 dev server（127.0.0.1:17777）——测试环境前端未运行，
    Windows 下连接未监听端口常不立即拒绝而是等到 2s 超时，无谓拖慢用例。
    测试只校验返回结构（frontend/worker 键存在），把探测替换为即时 down。"""
    async def _fake_probe():
        return {"status": "down", "latency_ms": None, "detail": "测试环境不探测前端"}

    import app.services.health_service as hs

    monkeypatch.setattr(hs, "_probe_frontend", _fake_probe)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["app"] == "Fashion Inspo"
    assert data["schema_version"]  # 前后端契约握手值


def test_health_schema_version_format(client):
    """schema_version 应为 {db_hash}-{api_contract_version} 形式。"""
    version = client.get("/api/health").json()["schema_version"]
    assert "-" in version
    head, tail = version.rsplit("-", 1)
    assert len(head) == 8  # SHA-256 前 8 位
    assert tail.isdigit()


def test_health_services(client):
    """服务健康端点：返回 services/resources/alerts 结构。"""
    r = client.get("/api/health/services")
    assert r.status_code == 200
    data = r.json()

    # 后端自身健康（能响应即 ok）
    assert data["services"]["backend"]["status"] == "ok"
    # 前端 / worker 探测与心跳
    assert "frontend" in data["services"]
    assert "worker" in data["services"]
    # 资源与告警结构
    assert "disk" in data["resources"]
    assert "logs" in data["resources"]
    assert isinstance(data["alerts"], list)
    assert data["checked_at"]
