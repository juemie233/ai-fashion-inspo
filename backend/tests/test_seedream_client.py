"""Seedream 客户端单测（不花钱）：mock Ark HTTP，覆盖 data URI/尺寸/重试/错误映射。"""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from app.services import seedream_client
from app.services.seedream_client import (
    MAX_OUTPUT_BYTES,
    SeedreamClient,
    SeedreamError,
    _data_uri,
    _mime_of,
    build_prompt,
    validate_size,
)

# 1×1 透明 PNG（魔数正确即可）
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _client(handler, api_key: str = "test-key") -> SeedreamClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SeedreamClient("test-key" if api_key else "", client=http)


def test_mime_and_data_uri():
    assert _mime_of(TINY_PNG) == "image/png"
    assert _mime_of(b"\xff\xd8\xffabc") == "image/jpeg"
    assert _mime_of(b"RIFFxxxxWEBP") == "image/webp"
    assert _mime_of(b"not-image") is None
    uri = _data_uri(TINY_PNG)
    assert uri.startswith("data:image/png;base64,")


def test_validate_size():
    assert validate_size("2K")[0] == "2K"
    assert validate_size("1536x2048")[:2] == ("1536x2048", 1536)
    with pytest.raises(SeedreamError):
        validate_size("512x512")  # 总像素不足
    with pytest.raises(SeedreamError):
        validate_size("10000x10000")  # 超过上限
    with pytest.raises(SeedreamError):
        validate_size("abc")


def test_build_prompt_merges_negative():
    text = build_prompt("保持图1人物", "模糊, 多余手指")
    assert text.startswith("保持图1人物")
    assert "避免出现：模糊, 多余手指" in text


def test_build_prompt_truncates_to_official_limit():
    """P2-10：官方中文建议 ≤300 字，超长 prompt 要截断而不是原样发出去。"""
    text = build_prompt("甲" * 400, "乙" * 100)
    assert len(text) <= 300


@pytest.mark.asyncio
async def test_generate_builds_two_data_uris_and_parses_b64():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "doubao-seedream-5-0-flash-260915",
                "data": [
                    {
                        "b64_json": base64.b64encode(TINY_PNG).decode(),
                        "size": "1536x2048",
                        "output_format": "png",
                    }
                ],
                "usage": {"input_images": 2, "generated_images": 1},
            },
            headers={"x-request-id": "req-1"},
        )

    client = _client(handler)
    result = await client.generate(
        prompt="保持图1人物，按图2姿势",
        images=[TINY_PNG, TINY_PNG],
        size="1536x2048",
    )
    await client.aclose()

    assert captured["model"] == "doubao-seedream-5-0-flash-260915"
    assert len(captured["image"]) == 2
    assert captured["image"][0].startswith("data:image/png;base64,")
    assert captured["watermark"] is False
    assert result.image == TINY_PNG
    assert result.request_id == "req-1"
    assert result.usage["generated_images"] == 1


@pytest.mark.asyncio
async def test_generate_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"code": "rate_limit", "message": "slow"}})
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(TINY_PNG).decode()}]},
        )

    client = _client(handler)
    result = await client.generate(prompt="x", images=[TINY_PNG, TINY_PNG], size="1K")
    await client.aclose()
    assert calls["n"] == 2
    assert result.image == TINY_PNG


@pytest.mark.asyncio
async def test_generate_400_is_permanent():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": "InvalidParameter", "message": "bad"}})

    client = _client(handler)
    with pytest.raises(SeedreamError) as exc:
        await client.generate(prompt="x", images=[TINY_PNG, TINY_PNG], size="1K")
    await client.aclose()
    assert exc.value.retryable is False
    assert "参数不合法" in str(exc.value)


@pytest.mark.asyncio
async def test_generate_auth_error_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "Unauthorized", "message": "no"}})

    client = _client(handler)
    with pytest.raises(SeedreamError) as exc:
        await client.generate(prompt="x", images=[TINY_PNG, TINY_PNG], size="1K")
    await client.aclose()
    assert "ARK_API_KEY" in str(exc.value)


@pytest.mark.asyncio
async def test_generate_without_key_fails_fast():
    client = SeedreamClient("")
    with pytest.raises(SeedreamError) as exc:
        await client.generate(prompt="x", images=[TINY_PNG, TINY_PNG], size="1K")
    await client.aclose()
    assert "ARK_API_KEY" in str(exc.value)


@pytest.mark.asyncio
async def test_generate_requires_two_images():
    client = SeedreamClient("key")
    with pytest.raises(SeedreamError) as exc:
        await client.generate(prompt="x", images=[TINY_PNG], size="1K")
    await client.aclose()
    assert "2~10" in str(exc.value)


# ── 2026-11-03 评审修复：下载重试语义 / 取消 / Retry-After / 审核中文 ──


@pytest.mark.asyncio
async def test_download_failure_retries_download_only(monkeypatch):
    """P1-1：生成成功但下载失败时，只重试下载，绝不重新发生成请求（避免重复计费）。"""
    monkeypatch.setattr(seedream_client, "_backoff_seconds", lambda attempt: 0.0)
    posts = {"n": 0}
    gets = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            return httpx.Response(
                200,
                json={"data": [{"url": "https://cdn.example.com/out.png"}]},
            )
        gets["n"] += 1
        return httpx.Response(500, content=b"boom")

    client = _client(handler)
    with pytest.raises(SeedreamError) as exc:
        await client.generate(prompt="x", images=[TINY_PNG, TINY_PNG], size="1K")
    await client.aclose()

    assert posts["n"] == 1, "下载失败不应触发第二次生成（会重复计费）"
    assert gets["n"] == seedream_client.DOWNLOAD_ATTEMPTS
    assert exc.value.code == "download_failed"
    assert exc.value.retryable is False
    assert "cdn.example.com" in str(exc.value)


@pytest.mark.asyncio
async def test_429_honours_retry_after_header(monkeypatch):
    """P2-8：429 带 Retry-After 时，退避时间不能短于响应头给的秒数。"""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(seedream_client, "_backoff_seconds", lambda attempt: 0.1)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "2"},
                json={"error": {"code": "rate_limit", "message": "slow"}},
            )
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(TINY_PNG).decode()}]},
        )

    client = _client(handler)
    await client.generate(prompt="x", images=[TINY_PNG, TINY_PNG], size="1K")
    await client.aclose()

    assert calls["n"] == 2
    assert any(delay >= 1.9 for delay in sleeps), sleeps


def test_error_mapping_matches_chinese_moderation_text():
    """P2-7：中文「内容审核」也要映射成审核错误，而不是落到「参数不合法」。"""
    err = seedream_client._error_from_response(
        400,
        {"error": {"code": "SensitiveContentDetected", "message": "内容审核未通过"}},
    )
    assert err.retryable is False
    assert "审核" in str(err)


@pytest.mark.asyncio
async def test_download_size_limit_rejects_oversize(monkeypatch):
    """P2-9：下载响应超过上限时直接拒绝，不把异常大文件读进内存。"""
    monkeypatch.setattr(seedream_client, "_backoff_seconds", lambda attempt: 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200, json={"data": [{"url": "https://cdn.example.com/huge.png"}]}
            )
        return httpx.Response(
            200,
            headers={
                "content-type": "image/png",
                "content-length": str(MAX_OUTPUT_BYTES + 1),
            },
            content=TINY_PNG,
        )

    client = _client(handler)
    with pytest.raises(SeedreamError) as exc:
        await client.generate(prompt="x", images=[TINY_PNG, TINY_PNG], size="1K")
    await client.aclose()
    assert exc.value.code == "download_failed"
    assert "上限" in str(exc.value)


@pytest.mark.asyncio
async def test_should_cancel_before_request_skips_http_call():
    """P1-6：任务已取消时不再发请求（避免取消后仍产生费用）。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(TINY_PNG).decode()}]},
        )

    async def cancelled() -> bool:
        return True

    client = _client(handler)
    with pytest.raises(SeedreamError) as exc:
        await client.generate(
            prompt="x", images=[TINY_PNG, TINY_PNG], size="1K", should_cancel=cancelled
        )
    await client.aclose()
    assert exc.value.code == "cancelled"
    assert calls["n"] == 0
