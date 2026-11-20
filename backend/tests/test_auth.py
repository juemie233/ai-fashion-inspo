"""破坏性接口 API Key 认证（安全加固回归测试）。"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import settings
from app.utils.auth import (
    NO_API_KEY_LOOPBACK_ONLY_DETAIL,
    is_loopback_host,
    require_api_key,
)

TEST_KEY = "pytest-secret-key"

# 模拟「来自局域网」的 TestClient：只换 ASGI 的 client 地址，不重复触发 lifespan。
_REMOTE_CLIENT = ("203.0.113.7", 40000)


class _RemoteClientASGI:
    """在 ASGI scope 层把 client 地址改成非回环，兼容新旧 Starlette。

    为什么不用 ``TestClient(..., client=...)``：该参数只存在于较新的
    Starlette；CI 按 ``requirements.txt`` 安装的 FastAPI 0.115.6 / Starlette
    0.4x 不支持它。包一层 ASGI app 注入 scope["client"] 后，
    TestClient 在本地与 CI 锁定版本上行为一致。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": _REMOTE_CLIENT}
        await self.app(scope, receive, send)


def _remote_client() -> TestClient:
    """构造来源为非回环地址的 TestClient（复用会话级 app，不进入 context manager）。"""
    from app.main import app

    return TestClient(_RemoteClientASGI(app))


def _request_from(host: str) -> Request:
    """构造只带 TCP 对端地址的最小 Starlette Request，用于直接测依赖。"""
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": (host, 40000),
            "server": ("testserver", 80),
        }
    )


def test_destructive_requires_key(client, monkeypatch):
    """配置 API_KEY 后：破坏性接口无 key 401、错 key 403、正确 key 通过。"""
    monkeypatch.setattr(settings, "api_key", TEST_KEY)

    # 无 key → 401
    r = client.delete("/api/inspirations/trash")
    assert r.status_code == 401
    assert "X-API-Key" in r.json()["detail"]

    # 错误 key → 403
    r = client.delete("/api/inspirations/trash", headers={"X-API-Key": "wrong"})
    assert r.status_code == 403

    # 正确 key → 通过（空库清空垃圾桶返回 200）
    r = client.delete("/api/inspirations/trash", headers={"X-API-Key": TEST_KEY})
    assert r.status_code == 200


def test_read_endpoints_unaffected(client, monkeypatch):
    """读接口不受认证影响。"""
    monkeypatch.setattr(settings, "api_key", TEST_KEY)
    assert client.get("/api/inspirations").status_code == 200
    assert client.get("/api/tags").status_code == 200
    assert client.get("/api/bloggers").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_recoverable_writes_unaffected(client, monkeypatch, upload):
    """可恢复写操作（移入垃圾桶）不受认证影响：素材不存在返回 404 而非 401。"""
    monkeypatch.setattr(settings, "api_key", TEST_KEY)
    r = client.post("/api/inspirations/no-such-id/trash", json={})
    assert r.status_code == 404


def test_other_destructive_endpoints(client, monkeypatch):
    """抽查其他破坏性端点（标签批量删除/合并、人物删除）→ 401。"""
    monkeypatch.setattr(settings, "api_key", TEST_KEY)

    r = client.post("/api/tags/batch-delete", json={"tag_ids": [1]})
    assert r.status_code == 401

    r = client.post("/api/tags/merge", json={"source_tag_id": 1, "target_tag_id": 2})
    assert r.status_code == 401

    r = client.delete("/api/bloggers/1")
    assert r.status_code == 401

    r = client.delete("/api/ai/history/999")
    assert r.status_code == 401

    # 任务队列历史记录删除（破坏性接口）
    r = client.delete("/api/tasks/1")
    assert r.status_code == 401


def test_tag_advanced_destructive_endpoints(client, monkeypatch):
    """高级标签的批量编辑/聚类应用/历史回滚 → 401（修复：此前未纳入清单）。"""
    monkeypatch.setattr(settings, "api_key", TEST_KEY)

    r = client.post("/api/tags/batch-edit", json={"dry_run": False, "rules": []})
    assert r.status_code == 401

    r = client.post("/api/tags/clusters/apply", json={"groups": []})
    assert r.status_code == 401

    r = client.post("/api/tags/history/1/rollback")
    assert r.status_code == 401


def test_destructive_without_key_allows_loopback_only(client):
    """未配置 API_KEY：回环来源（本机 / TestClient 的 testclient）照常放行。"""
    assert settings.api_key == ""  # conftest 已清空
    r = client.delete("/api/bloggers/999")
    assert r.status_code == 404  # 人物不存在（而非 401）
    r = client.delete("/api/inspirations/trash")
    assert r.status_code == 200


def test_same_effect_routes_are_covered(client, monkeypatch):
    """同效旁路必须一起进清单：PATCH 批量改名/改类别 与 不可逆的裁剪「应用」。

    2026-10-20 审查发现：`POST /api/tags/batch-edit` 已在清单里，但做同一件事的
    `PATCH /api/tags/batch-rename` 与 `PATCH /api/tags/batch-category` 不在——
    同一破坏效果换个方法就绕过了 X-API-Key。裁剪 apply 会不可逆改写原图，同样补入。
    """
    monkeypatch.setattr(settings, "api_key", TEST_KEY)

    assert (
        client.patch("/api/tags/batch-rename", json={"items": []}).status_code == 401
    )
    assert (
        client.patch("/api/tags/batch-category", json={"items": [], "category": "free"})
        .status_code
        == 401
    )
    assert (
        client.post("/api/admin/crop-phone-screenshots/apply", json={"ids": []}).status_code
        == 401
    )


# ── R1（task-7）：未配置 API_KEY 时只允许回环访问 ──


class TestNoApiKeyLoopbackOnlyHTTP:
    """HTTP 中间件口径：未配 API_KEY 时所有非回环请求 401（读接口也一样）。"""

    def test_non_loopback_destructive_rejected_with_401(self, client):
        remote = _remote_client()
        response = remote.delete("/api/inspirations/trash")
        assert response.status_code == 401
        assert response.json()["detail"] == NO_API_KEY_LOOPBACK_ONLY_DETAIL

    def test_non_loopback_reads_and_health_rejected_with_401(self, client):
        """读接口/健康检查同样 401：无 API_KEY 时边界是「仅本机」，不是「仅写」。"""
        remote = _remote_client()
        for path in ("/api/health", "/api/inspirations", "/api/tags"):
            response = remote.get(path)
            assert response.status_code == 401, path
            assert response.json()["detail"] == NO_API_KEY_LOOPBACK_ONLY_DETAIL

    def test_loopback_reads_and_health_allowed(self, client):
        """回环来源（TestClient 的 testclient 主机）读写都放行。"""
        assert settings.api_key == ""
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/inspirations").status_code == 200

    def test_more_destructive_endpoints_rejected_from_remote(self, client):
        remote = _remote_client()
        assert (
            remote.post(
                "/api/tags/merge", json={"source_tag_id": 1, "target_tag_id": 2}
            ).status_code
            == 401
        )
        assert remote.delete("/api/ai/history/999").status_code == 401
        assert remote.post("/api/admin/batch-delete", json={"ids": []}).status_code == 401

    def test_x_forwarded_for_is_not_trusted(self, client):
        """伪造 X-Forwarded-For 不能把远端伪装成本机，也不能把本机变成远端。"""
        remote = _remote_client()
        spoofed = remote.delete(
            "/api/inspirations/trash",
            headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"},
        )
        assert spoofed.status_code == 401
        assert spoofed.json()["detail"] == NO_API_KEY_LOOPBACK_ONLY_DETAIL

        loopback = client.delete(
            "/api/inspirations/trash",
            headers={"X-Forwarded-For": "203.0.113.7"},
        )
        assert loopback.status_code == 200

    def test_configured_key_still_works_from_remote(self, client, monkeypatch):
        """配置 API_KEY 后：非回环请求放行，破坏性接口按密钥校验。"""
        monkeypatch.setattr(settings, "api_key", TEST_KEY)
        remote = _remote_client()
        # 读接口不限来源
        assert remote.get("/api/health").status_code == 200
        assert remote.get("/api/inspirations").status_code == 200
        # 破坏性接口仍要求正确密钥
        assert remote.delete("/api/inspirations/trash").status_code == 401
        assert (
            remote.delete(
                "/api/inspirations/trash", headers={"X-API-Key": TEST_KEY}
            ).status_code
            == 200
        )


class TestNoApiKeyLoopbackOnlyDependency:
    """require_api_key 依赖口径：与中间件一致（覆盖清单外显式鉴权的接口）。"""

    async def test_loopback_hosts_pass_without_key(self):
        assert settings.api_key == ""
        for host in (
            "127.0.0.1",
            "127.1.2.3",
            "::1",
            "::ffff:127.0.0.1",
            "localhost",
            "testclient",
        ):
            assert await require_api_key(_request_from(host), api_key=None) is None

    async def test_remote_host_gets_401_without_key(self):
        assert settings.api_key == ""
        with pytest.raises(HTTPException) as excinfo:
            await require_api_key(_request_from("192.168.1.50"), api_key=None)
        assert excinfo.value.status_code == 401
        assert excinfo.value.detail == NO_API_KEY_LOOPBACK_ONLY_DETAIL


class TestIsLoopbackHost:
    """is_loopback_host 的边界：IPv4/IPv6/IPv4-mapped/主机名/非法值。"""

    def test_loopback_values(self):
        for host in (
            "127.0.0.1",
            "127.1.2.3",
            "::1",
            "::ffff:127.0.0.1",
            "localhost",
            "TESTCLIENT",
        ):
            assert is_loopback_host(host), host

    def test_non_loopback_values(self):
        for host in ("192.168.1.50", "203.0.113.7", "8.8.8.8", "example.com", "", None):
            assert not is_loopback_host(host), host

