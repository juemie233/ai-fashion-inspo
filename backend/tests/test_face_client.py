"""人脸识别子服务客户端测试：批量 embed 请求构造、超时放大、连接复用、未配置降级。

不依赖真实子服务：通过替换实例方法 _request 捕获请求参数；连接复用用真实
httpx.AsyncClient 验证（构造不发起网络请求）。
"""

import pytest

from app.services.face_client import FaceRecognitionClient, FaceServiceUnavailableError


def _client(base_url: str = "http://face.test") -> FaceRecognitionClient:
    return FaceRecognitionClient(base_url=base_url, timeout=30.0)


async def test_embed_batch_builds_multipart(monkeypatch):
    """批量请求：路径/files 字段名/文件名/内容正确，超时按张数放大。"""
    captured: dict = {}

    async def fake_request(method: str, path: str, *, timeout=None, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["timeout"] = timeout
        captured["files"] = kwargs.get("files")
        return {"items": [], "failed": 0}

    c = _client()
    monkeypatch.setattr(c, "_request", fake_request)
    result = await c.embed_batch([b"img-a", b"img-b"], filenames=["a.jpg", "b.jpg"])

    assert result == {"items": [], "failed": 0}
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/face/embed-batch"
    # 超时 = 基础 30s + 每张 2s 预算
    assert captured["timeout"] == 34.0
    files = captured["files"]
    assert [name for name, _ in files] == ["files", "files"]
    assert [filename for _, (filename, _, _) in files] == ["a.jpg", "b.jpg"]
    assert [data for _, (_, data, _) in files] == [b"img-a", b"img-b"]


async def test_embed_batch_default_filenames(monkeypatch):
    """未传文件名时自动生成 image_{i}.jpg。"""
    captured: dict = {}

    async def fake_request(method: str, path: str, *, timeout=None, **kwargs):
        captured["files"] = kwargs.get("files")
        return {}

    c = _client()
    monkeypatch.setattr(c, "_request", fake_request)
    await c.embed_batch([b"x", b"y", b"z"])
    filenames = [filename for _, (filename, _, _) in captured["files"]]
    assert filenames == ["image_0.jpg", "image_1.jpg", "image_2.jpg"]


async def test_client_reused_across_requests():
    """连接复用：多次请求共用同一 AsyncClient 实例（keep-alive）。"""
    c = _client()
    first = await c._get_client()
    second = await c._get_client()
    assert first is second
    assert not first.is_closed


async def test_disabled_client_raises():
    """未配置子服务 URL 时调用抛出 FaceServiceUnavailableError。"""
    c = _client()
    c.base_url = ""  # 模拟 FACE_SERVICE_URL 未配置
    assert c.enabled is False
    with pytest.raises(FaceServiceUnavailableError, match="未配置"):
        await c.embed(b"image-bytes")


async def test_http_error_wrapped(monkeypatch):
    """子服务返回非 2xx 时包装为 FaceServiceHttpError（含状态码与业务消息）。"""
    import httpx

    from app.services.face_client import FaceServiceHttpError

    class _FakeClient:
        """假 httpx client：构造不联网，request 恒抛 HTTPStatusError(404)。"""

        def __init__(self, *args, **kwargs):
            pass

        @property
        def is_closed(self) -> bool:
            return False

        async def request(self, method: str, url: str, timeout=None, **kwargs):
            request = httpx.Request(method, url)
            response = httpx.Response(404, request=request, json={"detail": "未检测到人脸"})
            raise httpx.HTTPStatusError("404", request=request, response=response)

    monkeypatch.setattr("app.services.face_client.httpx.AsyncClient", _FakeClient)
    c = _client()
    with pytest.raises(FaceServiceHttpError) as exc:
        await c.embed(b"x")
    assert exc.value.status_code == 404
    assert "未检测到人脸" in str(exc.value)
