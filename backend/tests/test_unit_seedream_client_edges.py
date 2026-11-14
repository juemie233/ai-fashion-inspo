"""Seedream（方舟云端出图）客户端的错误/边界路径测试。

`test_seedream_client.py` 已覆盖主链路（b64 解析、429 重试、鉴权、取消前置检查），
本文件补**异常映射与下载/等待的边角**——它们出错的表现是「云端已扣费但本地拿不到图」
或「看起来永久失败其实值得重试」：

- 尺寸/参考图格式校验的每种失败都要给用户可读原因且标记是否可重试；
- HTTP 错误映射：401/403、404、审核拦截、429（带 Retry-After）、5xx、其余 400；
- 生成成功但响应结构异常（非 JSON / 无 data / b64 坏 / 无 url）时不能把结果丢掉；
- 下载：网络抖动可重试、HTTP 错误可重试、**超大响应拒绝保存**、content-type 非图片拒绝；
- 取消轮询：等待响应期间每 0.5s 检查取消，取消后关闭请求并抛永久取消错误；
- 连接池懒建与 aclose 的所有权（注入的 client 不能被关）。
"""

import asyncio
import base64

import httpx
import pytest

from app.services import seedream_client as sc
from app.services.seedream_client import SeedreamClient, SeedreamError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


class _Resp:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload=None,
        content: bytes = b"",
        headers: dict | None = None,
        json_raises: bool = False,
    ):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = headers or {}
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """可注入的假 httpx.AsyncClient：按队列返回响应或抛异常。"""

    def __init__(self, posts=(), gets=()):
        self._posts = list(posts)
        self._gets = list(gets)
        self.post_urls: list[tuple] = []
        self.get_urls: list[str] = []
        self.closed = False

    async def post(self, url, **kwargs):
        self.post_urls.append((url, kwargs))
        item = self._posts.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def get(self, url, **_kwargs):
        self.get_urls.append(url)
        item = self._gets.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _instant_retry(monkeypatch):
    monkeypatch.setattr(sc, "_backoff_seconds", lambda _attempt: 0.0)


def _png_b64() -> str:
    return base64.b64encode(PNG).decode()


class TestDataUriAndSize:
    def test_data_uri_rejects_oversize_and_unknown_format(self):
        with pytest.raises(SeedreamError) as exc_info:
            sc._data_uri(b"\x00" * (sc.MAX_INPUT_BYTES + 1))
        assert not exc_info.value.retryable
        assert "上限" in str(exc_info.value)

        with pytest.raises(SeedreamError) as exc_info:
            sc._data_uri(b"GIFF" + b"\x00" * 10)
        assert not exc_info.value.retryable
        assert "格式不支持" in str(exc_info.value)

    def test_data_uri_mimes(self):
        assert sc._data_uri(PNG).startswith("data:image/png;base64,")
        assert sc._data_uri(b"\xff\xd8\xff" + b"\x00").startswith("data:image/jpeg;base64,")
        webp = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00"
        assert sc._data_uri(webp).startswith("data:image/webp;base64,")

    @pytest.mark.parametrize(
        ("raw", "normalized"),
        [
            ("1.5k", "1.5K"),
            ("1536", "1.5K"),
            ("2k", "2K"),
            ("2048", "2K"),
            ("1024", "1K"),
            ("1024x1024", "1024x1024"),
        ],
    )
    def test_validate_size_aliases(self, raw, normalized):
        assert sc.validate_size(raw)[0] == normalized

    @pytest.mark.parametrize(
        ("raw", "keyword"),
        [
            ("abc", "尺寸需为"),  # 没有 x
            ("axb", "格式错误"),  # 非数字
            ("10x1000", "过小"),  # 宽 <= 14
            ("100x100", "像素数"),  # 总像素不足
            ("12000x100", "宽高比"),  # 像素够但宽高比 120 > 16
        ],
    )
    def test_validate_size_errors(self, raw, keyword):
        with pytest.raises(SeedreamError) as exc_info:
            sc.validate_size(raw)
        assert keyword in str(exc_info.value)
        assert not exc_info.value.retryable


class TestErrorMapping:
    def test_retry_after_parsing(self):
        assert sc._retry_after_seconds(None) is None
        assert sc._retry_after_seconds("  ") is None
        assert sc._retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") is None  # HTTP 日期不解析
        assert sc._retry_after_seconds("120") == 60.0  # 上限 60 秒
        assert sc._retry_after_seconds("-5") == 0.0

    def test_not_found_and_server_errors(self):
        not_found = sc._error_from_response(404, {"error": {"code": "NotFound"}})
        assert not not_found.retryable
        assert "模型不存在或未开通" in str(not_found)

        server = sc._error_from_response(503, None)
        assert server.retryable
        assert "服务异常（HTTP 503）" in str(server)

        bad_req = sc._error_from_response(400, {"error": {"message": "bad param"}})
        assert not bad_req.retryable
        assert "bad param" in str(bad_req)

        no_detail = sc._error_from_response(400, None)
        assert "云端参数不合法" in str(no_detail)

    def test_moderation_chinese_and_429(self):
        blocked = sc._error_from_response(400, {"error": {"message": "内容审核未通过"}})
        assert not blocked.retryable
        assert "云端审核未通过" in str(blocked)

        limited = sc._error_from_response(
            429, {"error": {"message": "rate limited"}}, retry_after=5.0
        )
        assert limited.retryable
        assert limited.retry_after == 5.0


class TestClientLifecycle:
    async def test_aclose_only_closes_owned_client(self):
        injected = _FakeClient()
        client = SeedreamClient("key", client=injected)
        await client.aclose()
        assert injected.closed is False  # 注入的 client 由调用方负责

        own = SeedreamClient("key")
        created: list = []
        original = httpx.AsyncClient

        class _Closeable:
            def __init__(self):
                created.append(self)
                self.closed = False

            async def aclose(self):
                self.closed = True

        sc.httpx.AsyncClient = lambda **_kwargs: _Closeable()  # type: ignore[assignment]
        try:
            await own._get_client()
            await own.aclose()
        finally:
            sc.httpx.AsyncClient = original  # type: ignore[assignment]
        assert created and created[0].closed is True

    async def test_get_client_returns_injected_and_caches_lazy(self):
        injected = _FakeClient()
        client = SeedreamClient("key", client=injected)
        assert await client._get_client() is injected

        lazy = SeedreamClient("key")
        original = sc.httpx.AsyncClient
        created: list = []

        class _Created:
            def __init__(self):
                created.append(self)

        sc.httpx.AsyncClient = lambda **_kwargs: _Created()  # type: ignore[assignment]
        try:
            first = await lazy._get_client()
            second = await lazy._get_client()
        finally:
            sc.httpx.AsyncClient = original  # type: ignore[assignment]
        assert first is second
        assert len(created) == 1  # 懒建并复用


class TestGenerateErrors:
    async def test_invalid_reference_image_format(self):
        client = SeedreamClient("key", client=_FakeClient())

        with pytest.raises(SeedreamError) as exc_info:
            await client.generate(
                prompt="x",
                images=[b"not-an-image", b"not-an-image"],
                size="1K",
            )
        assert "格式不支持" in str(exc_info.value)

    async def test_result_content_not_an_image(self):
        post = _Resp(
            payload={"data": [{"b64_json": base64.b64encode(b"hello").decode()}]},
            headers={"x-request-id": "r1"},
        )
        client = SeedreamClient("key", client=_FakeClient(posts=[post]))

        with pytest.raises(SeedreamError) as exc_info:
            await client.generate(prompt="x", images=[PNG, PNG], size="1K")
        assert "不是有效 PNG/JPEG" in str(exc_info.value)

    async def test_transport_error_is_retried_then_succeeds(self):
        post = _Resp(payload={"data": [{"b64_json": _png_b64()}]})
        fake = _FakeClient(posts=[httpx.ConnectError("boom"), post])
        client = SeedreamClient("key", client=fake)

        result = await client.generate(
            prompt="x", images=[PNG, PNG], size="1K", max_attempts=2
        )

        assert len(fake.post_urls) == 2  # 网络错误后重试
        assert result.image == PNG

    async def test_transport_error_last_attempt_raises_readable(self):
        fake = _FakeClient(posts=[httpx.ConnectError("boom")])
        client = SeedreamClient("key", client=fake)

        with pytest.raises(SeedreamError) as exc_info:
            await client.generate(prompt="x", images=[PNG, PNG], size="1K", max_attempts=1)
        assert exc_info.value.retryable
        assert "连接方舟失败" in str(exc_info.value)

    async def test_cancelled_code_is_not_retried(self, monkeypatch):
        """取消类错误是永久错误：重试循环必须直接抛，不能继续发请求。"""
        client = SeedreamClient("key", client=_FakeClient())
        calls = {"n": 0}

        async def _cancelled(_body, *, should_cancel=None):
            calls["n"] += 1
            raise SeedreamError("云端请求已取消", retryable=False, code="cancelled")

        monkeypatch.setattr(client, "_request_once", _cancelled)

        with pytest.raises(SeedreamError) as exc_info:
            await client._generate_with_retry({"size": "1K"}, 3, None)

        assert exc_info.value.code == "cancelled"
        assert calls["n"] == 1  # 只发了一次


class TestRequestOnceEdges:
    async def test_error_body_not_json_uses_none_payload(self):
        fake = _FakeClient(posts=[_Resp(status_code=500, json_raises=True)])
        client = SeedreamClient("key", client=fake)

        with pytest.raises(SeedreamError) as exc_info:
            await client._request_once({"model": "m", "size": "1K"})
        assert exc_info.value.retryable
        assert "HTTP 500" in str(exc_info.value)

    async def test_success_response_not_json_is_retryable(self):
        fake = _FakeClient(posts=[_Resp(json_raises=True)])
        client = SeedreamClient("key", client=fake)

        with pytest.raises(SeedreamError) as exc_info:
            await client._request_once({"model": "m"})
        assert exc_info.value.retryable
        assert "非 JSON" in str(exc_info.value)

    async def test_missing_data_and_bad_b64(self):
        client = SeedreamClient("key", client=_FakeClient(posts=[_Resp(payload={})]))
        with pytest.raises(SeedreamError) as exc_info:
            await client._request_once({})
        assert "未返回图片数据" in str(exc_info.value)

        bad = _Resp(payload={"data": [{"b64_json": "这不是base64"}]})
        client = SeedreamClient("key", client=_FakeClient(posts=[bad]))
        with pytest.raises(SeedreamError) as exc_info:
            await client._request_once({})
        assert not exc_info.value.retryable
        assert "无法解码" in str(exc_info.value)

    async def test_no_url_and_no_b64_is_retryable(self):
        client = SeedreamClient("key", client=_FakeClient(posts=[_Resp(payload={"data": [{}]})]))
        with pytest.raises(SeedreamError) as exc_info:
            await client._request_once({})
        assert exc_info.value.retryable
        assert "缺 url/b64_json" in str(exc_info.value)


class TestDownloadEdges:
    async def test_missing_url_is_permanent(self):
        client = SeedreamClient("key", client=_FakeClient())
        with pytest.raises(SeedreamError) as exc_info:
            await client._download_with_retry("", None)
        assert exc_info.value.code == "download_failed"
        assert not exc_info.value.retryable

    async def test_transport_error_retried_then_succeeds(self, monkeypatch):
        ok = _Resp(content=PNG, headers={"content-type": "image/png"})
        fake = _FakeClient(gets=[httpx.ConnectError("boom"), ok])
        client = SeedreamClient("key", client=fake)

        content = await client._download_with_retry("https://x/img.png", None)

        assert content == PNG
        assert len(fake.get_urls) == 2  # 只重试下载，不重新生成

    async def test_download_failure_message_includes_url(self):
        fake = _FakeClient(gets=[_Resp(status_code=500)] * sc.DOWNLOAD_ATTEMPTS)
        client = SeedreamClient("key", client=fake)

        with pytest.raises(SeedreamError) as exc_info:
            await client._download_with_retry("https://x/img.png", None)
        assert exc_info.value.code == "download_failed"
        assert "https://x/img.png" in str(exc_info.value)

    async def test_non_image_content_type_rejected(self):
        resp = _Resp(content=b"<html>", headers={"content-type": "text/html"})
        client = SeedreamClient("key", client=_FakeClient(gets=[resp]))

        with pytest.raises(SeedreamError) as exc_info:
            await client._download("https://x/img.png")
        assert exc_info.value.retryable
        assert "不是图片" in str(exc_info.value)

    async def test_oversize_by_content_length_is_permanent(self):
        resp = _Resp(
            content=PNG,
            headers={
                "content-type": "image/png",
                "content-length": str(sc.MAX_OUTPUT_BYTES + 1),
            },
        )
        client = SeedreamClient("key", client=_FakeClient(gets=[resp]))

        with pytest.raises(SeedreamError) as exc_info:
            await client._download("https://x/img.png")
        assert not exc_info.value.retryable
        assert "上限" in str(exc_info.value)

    async def test_oversize_by_actual_content_is_permanent(self, monkeypatch):
        monkeypatch.setattr(sc, "MAX_OUTPUT_BYTES", 8)  # 让 21 字节 PNG 超限
        resp = _Resp(content=PNG, headers={"content-type": "image/png"})
        client = SeedreamClient("key", client=_FakeClient(gets=[resp]))

        with pytest.raises(SeedreamError) as exc_info:
            await client._download("https://x/img.png")
        assert not exc_info.value.retryable


class TestAwaitCancellable:
    async def test_normal_completion_returns_response(self):
        client = SeedreamClient("key", client=_FakeClient())
        resp = _Resp(payload={"ok": True})

        async def _request():
            await asyncio.sleep(0)
            return resp

        async def _not_cancelled() -> bool:
            return False

        assert await client._await_cancellable(_request(), _not_cancelled) is resp

    async def test_cancel_during_wait_raises_permanent_error(self, monkeypatch):
        monkeypatch.setattr(sc, "CANCEL_POLL_SECONDS", 0.01)
        client = SeedreamClient("key", client=_FakeClient())

        async def _slow():
            await asyncio.sleep(10)
            return _Resp()

        async def _cancelled() -> bool:
            return True

        with pytest.raises(SeedreamError) as exc_info:
            await client._await_cancellable(_slow(), _cancelled)

        assert exc_info.value.code == "cancelled"
        assert not exc_info.value.retryable
