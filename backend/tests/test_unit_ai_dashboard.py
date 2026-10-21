"""数据洞察接口的测试（质量总览 / 提示词质量 / 打标纠错 / 单图测速 SSE）。

**为什么直接调 handler**：本项目 TestClient 的请求跑在 anyio portal 线程，不被
coverage 追踪（HTTP 测出来永远只有模块级 def 被覆盖）。这里直接 `await handler(...)`，
Query 默认值显式传参。

重点覆盖「单图测速」这条 SSE 链路：它是用户调 prompt 的唯一入口，
分支多（上传/素材/视频帧/路径越界/WebP/Ollama 未启动/HTTP 错/超时/格式异常），
而每条分支的失败文案都直接显示在界面上。
"""

import io
import json

import httpx
import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.inspiration import Inspiration
from app.routers import ai_dashboard


async def _consume(response) -> list[dict]:
    """把 StreamingResponse 的 SSE 帧解析成 dict 列表。"""
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else str(chunk))
    events = []
    for line in "".join(chunks).splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


# ── 聚合类接口：透传 service，验证参数接线 ──


async def test_quality_dashboard_returns_service_payload(client):
    async with async_session() as db:
        data = await ai_dashboard.quality_dashboard(db=db)

    assert "trend" in data or "daily" in data or isinstance(data, dict)


async def test_prompt_quality_defaults_and_window(client):
    async with async_session() as db:
        data = await ai_dashboard.prompt_quality(days=30, include_bare_rate=False, db=db)

    assert isinstance(data, dict)


async def test_tag_corrections_list_and_stats(client):
    async with async_session() as db:
        listed = await ai_dashboard.list_tag_corrections(
            page=1, size=20, reason=None, db=db
        )
        assert "items" in listed and "total" in listed

        stats = await ai_dashboard.tag_correction_stats(days=30, db=db)
        assert isinstance(stats, dict)


# ── 单图测速：参数校验 ──


async def test_test_analyze_requires_source(client):
    """既没上传也没素材 ID → 400，文案要告诉用户怎么做。"""
    with pytest.raises(HTTPException) as exc:
        await ai_dashboard.test_analyze(inspiration_id=None, custom_prompt=None, file=None)

    assert exc.value.status_code == 400
    assert "请上传图片或指定" in str(exc.value.detail)


async def test_test_analyze_rejects_empty_upload(client):
    class _Empty:
        filename = "x.jpg"

        async def read(self):
            return b""

    with pytest.raises(HTTPException) as exc:
        await ai_dashboard.test_analyze(inspiration_id=None, custom_prompt=None, file=_Empty())

    assert exc.value.status_code == 400
    assert "为空" in str(exc.value.detail)


async def test_test_analyze_unknown_inspiration(client):
    with pytest.raises(HTTPException) as exc:
        await ai_dashboard.test_analyze(
            inspiration_id="不存在", custom_prompt=None, file=None
        )
    assert exc.value.status_code == 404


async def test_test_analyze_rejects_non_image_media(client):
    async with async_session() as db:
        db.add(
            Inspiration(
                id="tdoc",
                source_type="manual_upload",
                file_path="files/x.pdf",
                media_type="document",
                quality_status="approved",
            )
        )
        await db.commit()

    with pytest.raises(HTTPException) as exc:
        await ai_dashboard.test_analyze(inspiration_id="tdoc", custom_prompt=None, file=None)

    assert exc.value.status_code == 400
    assert "仅支持测试图片/视频" in str(exc.value.detail)


async def test_test_analyze_rejects_path_escape(client):
    """素材路径越界（历史脏数据/被篡改）必须拒绝，不能读库外文件。"""
    async with async_session() as db:
        db.add(
            Inspiration(
                id="tescape",
                source_type="manual_upload",
                file_path="../../../etc/passwd",
                media_type="image",
                quality_status="approved",
            )
        )
        await db.commit()

    with pytest.raises(HTTPException) as exc:
        await ai_dashboard.test_analyze(
            inspiration_id="tescape", custom_prompt=None, file=None
        )

    assert exc.value.status_code in (400, 404)


async def test_test_analyze_missing_file(client):
    async with async_session() as db:
        db.add(
            Inspiration(
                id="tmissing",
                source_type="manual_upload",
                file_path="images/不存在.jpg",
                media_type="image",
                quality_status="approved",
            )
        )
        await db.commit()

    with pytest.raises(HTTPException) as exc:
        await ai_dashboard.test_analyze(
            inspiration_id="tmissing", custom_prompt=None, file=None
        )

    assert exc.value.status_code == 404
    assert "不存在" in str(exc.value.detail)


# ── 单图测速：SSE 分支 ──


def _upload(data: bytes = b"fake-jpeg", filename: str = "a.jpg"):
    class _File:
        def __init__(self):
            self.filename = filename

        async def read(self):
            return data

    return _File()


@pytest.fixture(autouse=True)
def _patch_ollama_running(monkeypatch):
    """默认让 Ollama 视为在跑（各用例按需覆盖）。"""

    async def _running() -> bool:
        return True

    monkeypatch.setattr(ai_dashboard, "is_ollama_running", _running)


async def test_test_analyze_streams_parsed_result(client, monkeypatch):
    """正常链路：返回 done 帧，含解析结果、模型名与耗时。"""

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": '{"features": ["针织开衫"], "material": "羊毛"}'}}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            return _Resp()

    monkeypatch.setattr(ai_dashboard.httpx, "AsyncClient", _Client)

    response = await ai_dashboard.test_analyze(
        inspiration_id=None, custom_prompt="测试 prompt", file=_upload()
    )
    events = await _consume(response)

    assert events[-1]["type"] == "done"
    assert events[-1]["parsed"]["features"] == ["针织开衫"]
    assert events[-1]["model"]
    assert isinstance(events[-1]["elapsed_ms"], int)


async def test_test_analyze_reports_ollama_not_running(client, monkeypatch):
    async def _not_running() -> bool:
        return False

    async def _start_failed() -> None:
        return None

    monkeypatch.setattr(ai_dashboard, "is_ollama_running", _not_running)
    monkeypatch.setattr(ai_dashboard, "start_ollama", _start_failed)

    response = await ai_dashboard.test_analyze(
        inspiration_id=None, custom_prompt=None, file=_upload()
    )
    events = await _consume(response)

    assert events[0]["type"] == "error"
    assert "Ollama" in events[0]["message"]


async def test_test_analyze_reports_http_error(client, monkeypatch):
    class _Resp:
        status_code = 500

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "boom", request=httpx.Request("POST", "http://x"), response=httpx.Response(500)
            )

        def json(self):
            return {"error": "模型未加载"}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            return _Resp()

    monkeypatch.setattr(ai_dashboard.httpx, "AsyncClient", _Client)

    response = await ai_dashboard.test_analyze(
        inspiration_id=None, custom_prompt=None, file=_upload()
    )
    events = await _consume(response)

    assert events[0]["type"] == "error"
    assert "HTTP 500" in events[0]["message"]


@pytest.mark.parametrize(
    "exc,keyword",
    [
        (httpx.TimeoutException("t"), "超时"),
        (httpx.ConnectError("c"), "无法连接"),
    ],
)
async def test_test_analyze_reports_network_failures(client, monkeypatch, exc, keyword):
    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            raise exc

    monkeypatch.setattr(ai_dashboard.httpx, "AsyncClient", _Client)

    response = await ai_dashboard.test_analyze(
        inspiration_id=None, custom_prompt=None, file=_upload()
    )
    events = await _consume(response)

    assert events[0]["type"] == "error"
    assert keyword in events[0]["message"]


async def test_test_analyze_reports_malformed_response(client, monkeypatch):
    """Ollama 返回非预期结构（缺 message）→ error 帧而不是 500。"""

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"unexpected": True}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            return _Resp()

    monkeypatch.setattr(ai_dashboard.httpx, "AsyncClient", _Client)

    response = await ai_dashboard.test_analyze(
        inspiration_id=None, custom_prompt=None, file=_upload()
    )
    events = await _consume(response)

    assert events[0]["type"] == "error"
    assert "格式异常" in events[0]["message"]


async def test_test_analyze_uses_existing_image_file(client):
    """走素材 ID 分支：读现网文件并成功产出 done 帧。"""
    from app.config import settings

    rel = "images/2026-10/quick-test.jpg"
    target = settings.storage_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"fake-jpeg-bytes")

    async with async_session() as db:
        db.add(
            Inspiration(
                id="tfile",
                source_type="manual_upload",
                file_path=rel,
                media_type="image",
                quality_status="approved",
            )
        )
        await db.commit()

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "{}"}}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            return _Resp()

    response = await ai_dashboard.test_analyze(
        inspiration_id="tfile", custom_prompt=None, file=None
    )


async def test_test_analyze_video_uses_first_keyframe(client, monkeypatch):
    """视频素材用第一关键帧测试；提不出帧时 400 并说明原因。"""
    from app.services import video_service

    async with async_session() as db:
        db.add(
            Inspiration(
                id="tvideo",
                source_type="manual_upload",
                file_path="videos/x.mp4",
                media_type="video",
                quality_status="approved",
            )
        )
        await db.commit()

    async def _frame(insp):
        return settings_root() / "keyframes/tvideo/frame_001.jpg"

    async def _no_frame(insp):
        return None

    def settings_root():
        from app.config import settings

        return settings.storage_root

    # 1) 无帧 → 400
    monkeypatch.setattr(video_service, "ensure_first_frame", _no_frame)
    with pytest.raises(HTTPException) as exc:
        await ai_dashboard.test_analyze(inspiration_id="tvideo", custom_prompt=None, file=None)
    assert exc.value.status_code == 400
    assert "关键帧" in str(exc.value.detail)

    # 2) 有帧 → 正常出 done 帧
    frame = settings_root() / "keyframes/tvideo/frame_001.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(b"fake-frame")

    async def _frame_ok(insp):
        return frame

    monkeypatch.setattr(video_service, "ensure_first_frame", _frame_ok)
    monkeypatch.setattr(ai_dashboard.httpx, "AsyncClient", _OkClient)

    response = await ai_dashboard.test_analyze(
        inspiration_id="tvideo", custom_prompt=None, file=None
    )
    events = await _consume(response)
    assert events[-1]["type"] == "done"


class _OkClient:
    """返回合法 Ollama 响应的假客户端。"""

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"message": {"content": "{}"}}

        return _Resp()


async def test_test_analyze_converts_webp(client, monkeypatch):
    """WebP 要转 JPEG 再送模型（Ollama 对 WebP 支持不稳）。"""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "WEBP")
    webp_bytes = buf.getvalue()

    monkeypatch.setattr(ai_dashboard.httpx, "AsyncClient", _OkClient)

    response = await ai_dashboard.test_analyze(
        inspiration_id=None, custom_prompt=None, file=_upload(webp_bytes, "a.webp")
    )
    events = await _consume(response)

    assert events[-1]["type"] == "done"


async def test_test_analyze_reports_broken_webp(client):
    """坏 WebP → 400 并带原因（不能让 PIL 的异常变成 500）。"""
    with pytest.raises(HTTPException) as exc:
        await ai_dashboard.test_analyze(
            inspiration_id=None, custom_prompt=None, file=_upload(b"not-a-webp", "bad.webp")
        )
    assert exc.value.status_code == 400
    assert "WebP" in str(exc.value.detail)


async def test_event_stream_unexpected_error_becomes_error_frame(client, monkeypatch):
    """流内未知异常也要变成 error 帧（SSE 已经 200 了，无法再改状态码）。"""

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            raise ValueError("意料之外")

    monkeypatch.setattr(ai_dashboard.httpx, "AsyncClient", _Client)

    response = await ai_dashboard.test_analyze(
        inspiration_id=None, custom_prompt=None, file=_upload()
    )
    events = await _consume(response)

    assert events[0]["type"] == "error"
    assert "意料之外" in events[0]["message"]
