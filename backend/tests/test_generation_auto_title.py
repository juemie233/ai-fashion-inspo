"""P2-C3：自动命名的清洗规则、模型成功路径与兜底。

命名是「锦上添花」的功能，**失败绝不能让接口 500**：
模型离线/超时/回空 → 回退「描述前 20 字」并标记 fallback，前端照常可用。
"""

from __future__ import annotations

import asyncio

import pytest

from app.database import async_session
from app.models.generation import GenerationRun
from app.services import generation_service


def _seed_run(prompt: str) -> int:
    async def _do() -> int:
        async with async_session() as db:
            run = GenerationRun(
                kind="i2i",
                status="done",
                prompt=prompt,
                params_json="{}",
                model_key="qwen_2_1_uc_i2i",
                workflow_name="qwen_image_i2i",
                total=1,
                done=1,
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)
            return run.id

    return asyncio.run(_do())


def _cleanup(run_id: int) -> None:
    async def _do() -> None:
        async with async_session() as db:
            row = await db.get(GenerationRun, run_id)
            if row is not None:
                await db.delete(row)
            await db.commit()

    asyncio.run(_do())


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"message": {"role": "assistant", "content": self._content}}


class _FakeClient:
    def __init__(self, content: str | None = None, exc: Exception | None = None) -> None:
        self._content = content
        self._exc = exc
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def post(self, url: str, json: dict) -> _FakeResponse:  # noqa: A002 —— 模拟 httpx 参数名
        self.calls.append((url, json))
        if self._exc:
            raise self._exc
        return _FakeResponse(self._content or "")


def test_clean_title_strips_quotes_and_truncates():
    clean = generation_service._clean_title
    assert clean("  “通勤白衬衫”  ") == "通勤白衬衫"
    assert clean("- 米色针织开衫") == "米色针织开衫"
    assert clean("带\n换行 和 空格") == "带换行和空格"
    assert len(clean("很" * 50)) == generation_service.AUTO_TITLE_MAX_CHARS


def test_fallback_title_is_first_20_chars():
    prompt = "  米色针织开衫配白色阔腿裤，极简工作室背景  "
    title = generation_service.fallback_title(prompt)
    # 去掉首尾空白后取前 20 个字（含全角逗号）
    assert title == "".join(prompt.split())[:20]
    assert len(title) == 20
    assert generation_service.fallback_title("") == "未命名批次"


def test_auto_title_uses_model_output(client, monkeypatch):
    rid = _seed_run("法式复古碎花连衣裙，浅蓝色底配白色小碎花")
    fake = _FakeClient(content="“法式碎花连衣裙”")
    monkeypatch.setattr(
        generation_service.httpx, "AsyncClient", lambda *a, **kw: fake
    )
    try:
        body = client.post(f"/api/generate/runs/{rid}/auto-title").json()
        assert body == {"title": "法式碎花连衣裙", "source": "ai"}
        # 请求要打到 Ollama 的 /api/chat，并带上当前配置的模型
        from app.config import settings

        url, payload = fake.calls[0]
        assert url.endswith("/api/chat")
        assert payload["model"] == settings.ollama_vision_model
    finally:
        _cleanup(rid)


@pytest.mark.parametrize(
    "fake_case",
    [
        ("empty", _FakeClient(content="")),
        ("exception", _FakeClient(exc=RuntimeError("ollama 离线"))),
    ],
)
def test_auto_title_falls_back(client, monkeypatch, fake_case):
    name, fake = fake_case
    rid = _seed_run("米色针织开衫配白色阔腿裤")
    monkeypatch.setattr(
        generation_service.httpx, "AsyncClient", lambda *a, **kw: fake
    )
    try:
        body = client.post(f"/api/generate/runs/{rid}/auto-title").json()
        assert body["source"] == "fallback", name
        assert body["title"] == "米色针织开衫配白色阔腿裤"
    finally:
        _cleanup(rid)


def test_auto_title_404_for_missing_run(client):
    assert client.post("/api/generate/runs/999999/auto-title").status_code == 404


def test_auto_title_does_not_write_to_db(client, monkeypatch):
    """只返回建议，不落库（标题是用户可改的元数据，写入只走 PATCH）。"""
    rid = _seed_run("烟灰色西装外套")
    fake = _FakeClient(content="烟灰西装")
    monkeypatch.setattr(generation_service.httpx, "AsyncClient", lambda *a, **kw: fake)
    try:
        client.post(f"/api/generate/runs/{rid}/auto-title")

        async def _title() -> str | None:
            async with async_session() as db:
                row = await db.get(GenerationRun, rid)
                return row.title

        assert asyncio.run(_title()) is None
    finally:
        _cleanup(rid)
