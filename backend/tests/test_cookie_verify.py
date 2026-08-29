"""Cookie 真实有效性校验测试：前置检查 / 探测响应解释 / 缓存 / 任务前置拦截。

app/services/scraper/cookie_verify.py（TODO「Cookie 真实有效性校验」方案落地）。
HTTP 探测全部打桩（替换 httpx.AsyncClient），不发真实网络请求；
集成用例通过 API 导入 Cookie 文件并验证任务创建拦截。
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.config import settings
from app.services.scraper import cookie_verify as cv


def _remove_cookie_files() -> None:
    cookie_dir = Path(settings.storage_root) / "cookies"
    if cookie_dir.exists():
        for f in cookie_dir.glob("*_cookies.json"):
            f.unlink()


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前后清空校验缓存与 Cookie 文件目录。

    conftest.clean_state 不清理 storage/cookies——残留文件会让本模块之外
    的用例（如 test_scraper.py 建任务）触发真实网络探测，必须前后都清理。
    """
    cv._verify_cache.clear()
    _remove_cookie_files()
    yield
    cv._verify_cache.clear()
    _remove_cookie_files()


def _write_cookies(platform: str, data) -> Path:
    cookie_dir = Path(settings.storage_root) / "cookies"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    f = cookie_dir / f"{platform}_cookies.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return f


# ── 假 httpx 客户端 ──


class _FakeResp:
    def __init__(self, status=200, payload=None, broken_json=False):
        self.status_code = status
        self._payload = payload
        self._broken = broken_json

    def json(self):
        if self._broken:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """假 AsyncClient：记录请求头并返回预设响应或抛网络异常。"""

    last_headers: dict | None = None
    resp: _FakeResp | None = None
    network_error: Exception | None = None

    def __init__(self, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, _url, headers=None):
        _FakeClient.last_headers = headers
        if _FakeClient.network_error:
            raise _FakeClient.network_error
        return _FakeClient.resp


def _patch_probe(monkeypatch, resp=None, network_error=None):
    _FakeClient.resp = resp
    _FakeClient.network_error = network_error
    _FakeClient.last_headers = None
    monkeypatch.setattr(cv.httpx, "AsyncClient", lambda **kw: _FakeClient(**kw))


# ═══════════════════════════════════════════════════════════════
#  前置检查（无网络）
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_verify_no_file():
    out = await cv.verify_platform_cookie("xiaohongshu")
    assert out["state"] == "no_file"


@pytest.mark.asyncio
async def test_verify_broken_json_invalid():
    """损坏的 Cookie 文件：确定性证据，直接判 invalid。"""
    cookie_dir = Path(settings.storage_root) / "cookies"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    (cookie_dir / "xiaohongshu_cookies.json").write_text("%%%not-json%%%", encoding="utf-8")
    out = await cv.verify_platform_cookie("xiaohongshu")
    assert out["state"] == "invalid"
    assert "解析失败" in out["detail"]


def test_verify_missing_session_cookie_invalid():
    """无会话字段（web_session/sessionid）：登录态不可能有效，无需探测。"""
    _write_cookies("xiaohongshu", [{"name": "foo", "value": "x", "domain": ".xiaohongshu.com"}])
    out = asyncio.run(cv.verify_platform_cookie("xiaohongshu"))
    assert out["state"] == "invalid"
    assert "会话字段" in out["detail"]


@pytest.mark.asyncio
async def test_verify_wrong_domain_cookies_invalid():
    """Cookie 全部属于其他域名：拼不出请求头，判 invalid。"""
    _write_cookies("xiaohongshu", [
        {"name": "web_session", "value": "x", "domain": ".example.com"},
    ])
    out = await cv.verify_platform_cookie("xiaohongshu")
    assert out["state"] == "invalid"


# ═══════════════════════════════════════════════════════════════
#  真实探测（打桩 httpx）
# ═══════════════════════════════════════════════════════════════


_VALID_XHS_COOKIES = [
    {"name": "web_session", "value": "sess-abc", "domain": ".xiaohongshu.com"},
    {"name": "a1", "value": "tok", "domain": ".xiaohongshu.com"},
    {"name": "unrelated", "value": "1", "domain": ".other.com"},  # 其他域名应被过滤
]


def test_xhs_cookie_header_filters_other_domains(monkeypatch):
    """Cookie 请求头只带平台域名 Cookie。"""
    _write_cookies("xiaohongshu", _VALID_XHS_COOKIES)
    _patch_probe(monkeypatch, _FakeResp(200, {"success": True, "data": {"user_id": "u1"}}))
    out = asyncio.run(cv.verify_platform_cookie("xiaohongshu"))
    assert out["state"] == "valid"
    header = _FakeClient.last_headers["Cookie"]
    assert "web_session=sess-abc" in header and "unrelated" not in header


def test_verify_xhs_logged_in(monkeypatch):
    _write_cookies("xiaohongshu", _VALID_XHS_COOKIES)
    _patch_probe(monkeypatch, _FakeResp(200, {"success": True, "data": {"user_id": "u1"}}))
    out = asyncio.run(cv.verify_platform_cookie("xiaohongshu"))
    assert out["state"] == "valid"


def test_verify_xhs_not_logged_in(monkeypatch):
    """服务端明确返回未登录：invalid。"""
    _write_cookies("xiaohongshu", _VALID_XHS_COOKIES)
    _patch_probe(monkeypatch, _FakeResp(200, {"success": False, "msg": "未登录"}))
    out = asyncio.run(cv.verify_platform_cookie("xiaohongshu"))
    assert out["state"] == "invalid"


def test_verify_xhs_http_401_invalid(monkeypatch):
    _write_cookies("xiaohongshu", _VALID_XHS_COOKIES)
    _patch_probe(monkeypatch, _FakeResp(461, None))
    out = asyncio.run(cv.verify_platform_cookie("xiaohongshu"))
    assert out["state"] == "invalid"


def test_verify_douyin_logged_in(monkeypatch):
    """抖音 account/info：data.user_id 非空非 0 为已登录。"""
    _write_cookies("douyin", [
        {"name": "sessionid", "value": "dy-sess", "domain": ".douyin.com"},
        {"name": "ttwid", "value": "t", "domain": ".douyin.com"},
    ])
    _patch_probe(monkeypatch, _FakeResp(200, {"data": {"user_id": "12345", "user_name": "x"}}))
    out = asyncio.run(cv.verify_platform_cookie("douyin"))
    assert out["state"] == "valid"


def test_verify_douyin_guest_user_id_zero(monkeypatch):
    """user_id 为 0（游客态）：视为未登录。"""
    _write_cookies("douyin", [{"name": "sessionid", "value": "s", "domain": ".douyin.com"}])
    _patch_probe(monkeypatch, _FakeResp(200, {"data": {"user_id": 0}, "status_code": 8}))
    out = asyncio.run(cv.verify_platform_cookie("douyin"))
    assert out["state"] == "invalid"


def test_verify_network_error_unknown(monkeypatch):
    """网络不可达 / 风控：unknown，绝不误判 invalid。"""
    _write_cookies("xiaohongshu", _VALID_XHS_COOKIES)
    _patch_probe(monkeypatch, network_error=RuntimeError("connect timeout"))
    out = asyncio.run(cv.verify_platform_cookie("xiaohongshu"))
    assert out["state"] == "unknown"


def test_verify_html_response_unknown(monkeypatch):
    """响应非 JSON（如被风控出 HTML 验证页）：unknown。"""
    _write_cookies("xiaohongshu", _VALID_XHS_COOKIES)
    _patch_probe(monkeypatch, _FakeResp(200, None, broken_json=True))
    out = asyncio.run(cv.verify_platform_cookie("xiaohongshu"))
    assert out["state"] == "unknown"


def test_verify_unknown_platform_rejected():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        asyncio.run(cv.verify_platform_cookie("weibo"))
    assert ei.value.status_code == 400


# ═══════════════════════════════════════════════════════════════
#  缓存
# ═══════════════════════════════════════════════════════════════


def test_verify_cached_within_ttl(monkeypatch):
    """TTL 内重复校验复用缓存（不发真实请求）。"""
    _write_cookies("xiaohongshu", _VALID_XHS_COOKIES)
    _patch_probe(monkeypatch, _FakeResp(200, {"success": True}))

    first = asyncio.run(cv.verify_platform_cookie("xiaohongshu"))
    assert first["cached"] is False

    # 换一个会判 invalid 的响应：命中缓存则结果不变
    _FakeClient.resp = _FakeResp(200, {"success": False, "msg": "未登录"})
    second = asyncio.run(cv.verify_platform_cookie("xiaohongshu"))
    assert second["cached"] is True
    assert second["state"] == "valid"

    # force=True 绕过缓存
    forced = asyncio.run(cv.verify_platform_cookie("xiaohongshu", force=True))
    assert forced["cached"] is False
    assert forced["state"] == "invalid"


def test_verify_cache_invalidated_by_file_change(monkeypatch):
    """Cookie 文件变化（重新导入）后缓存按 mtime 失效。"""
    _write_cookies("xiaohongshu", _VALID_XHS_COOKIES)
    _patch_probe(monkeypatch, _FakeResp(200, {"success": True}))
    assert asyncio.run(cv.verify_platform_cookie("xiaohongshu"))["state"] == "valid"

    _FakeClient.resp = _FakeResp(200, {"success": False, "msg": "未登录"})
    import os
    import time as _t

    f = Path(settings.storage_root) / "cookies" / "xiaohongshu_cookies.json"
    os.utime(f, (_t.time() + 5, _t.time() + 5))  # 模拟重新导入（mtime 变化）

    out = asyncio.run(cv.verify_platform_cookie("xiaohongshu"))
    assert out["cached"] is False
    assert out["state"] == "invalid"


def test_peek_verification(monkeypatch):
    assert cv.peek_verification("douyin") is None
    _write_cookies("douyin", [{"name": "sessionid", "value": "s", "domain": ".douyin.com"}])
    _patch_probe(monkeypatch, _FakeResp(200, {"data": {"user_id": "9"}}))
    asyncio.run(cv.verify_platform_cookie("douyin"))
    assert cv.peek_verification("douyin")["state"] == "valid"


# ═══════════════════════════════════════════════════════════════
#  API 集成：校验接口 / 状态附带校验结果 / 任务创建拦截
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def no_scraper_subprocess(monkeypatch):
    from app.services.scraper import process

    monkeypatch.setattr(process, "_launch_scraper_process", lambda task_id: None)


def test_cookie_verify_endpoint_and_status(client, monkeypatch):
    """POST /cookie-verify 强制探测；GET /cookie-status 附带最近校验结果。"""
    _write_cookies("xiaohongshu", _VALID_XHS_COOKIES)
    _patch_probe(monkeypatch, _FakeResp(200, {"success": True}))

    r = client.post("/api/scraper/cookie-verify/xiaohongshu")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "valid"

    status = client.get("/api/scraper/cookie-status", params={"platform": "xiaohongshu"}).json()
    assert status["verify"]["state"] == "valid"


def test_cookie_status_unverified_is_null(client):
    status = client.get("/api/scraper/cookie-status", params={"platform": "douyin"}).json()
    assert status["verify"] is None


def test_create_task_blocked_on_invalid_cookie(client, monkeypatch):
    """验收标准：Cookie 文件替换为失效内容后，新建任务被拦截并提示。"""
    _write_cookies("xiaohongshu", [{"name": "tt", "value": "x", "domain": ".xiaohongshu.com"}])

    r = client.post(
        "/api/scraper/tasks",
        json={"platform": "xiaohongshu", "keywords": ["穿搭"], "max_count": 5},
    )
    assert r.status_code == 400, r.text
    assert "Cookie 已失效" in r.json()["detail"]["error"]


def test_create_task_allowed_without_cookie_file(client):
    """无 Cookie 文件不拦截（CDP Chrome 可能已有登录态，采集端会等待扫码）。"""
    r = client.post(
        "/api/scraper/tasks",
        json={"platform": "xiaohongshu", "keywords": ["穿搭"], "max_count": 5},
    )
    assert r.status_code == 201, r.text


def test_create_task_allowed_with_valid_cookie(client, monkeypatch):
    """校验为有效的 Cookie 不拦截任务创建。"""
    _write_cookies("xiaohongshu", _VALID_XHS_COOKIES)
    _patch_probe(monkeypatch, _FakeResp(200, {"success": True}))

    r = client.post(
        "/api/scraper/tasks",
        json={"platform": "xiaohongshu", "keywords": ["穿搭"], "max_count": 5},
    )
    assert r.status_code == 201, r.text
