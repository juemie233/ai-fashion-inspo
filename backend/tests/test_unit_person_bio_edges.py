"""人物简介生成（`services/person_bio.py`）分支补测。

既有 `tests/test_person_bio.py` 走接口主路径（成功 / 连接失败）；这里补纯函数的
格式化与清洗、Prompt 模板占位符错误、Ollama 状态码/未知异常、过短/超长截断等分支。
全部替换 httpx.AsyncClient 为假实现，不发真实请求。
"""

from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.services import person_bio as pb
from app.services.person_bio import (
    BioGenerationError,
    PersonBioInputs,
    _clean_model_output,
    _format_category_summary,
    _format_top_tags,
    generate_person_bio,
)

_TEMPLATE = (
    "为{kind}「{name}」写简介。平台：{platform}；属地：{ip_location}。"
    "标签：{top_tags}；类别：{category_summary}"
)


# ── 纯函数：标签 / 类别 / 输出清洗 ──


def test_format_top_tags_empty_and_blank_names():
    assert _format_top_tags([]) == "（暂无标签）"
    assert _format_top_tags([{"name": "  "}, {"name": ""}, {}]) == "（暂无标签）"


def test_format_top_tags_count_and_limit():
    tags = [{"name": "法式", "count": 3}, {"name": "连衣裙", "count": 1}]
    tags += [{"name": f"标签{i}"} for i in range(20)]
    text = _format_top_tags(tags)
    parts = text.split("、")
    assert parts[0] == "法式×3"
    assert parts[1] == "连衣裙"  # count<=1 不带频次
    # 默认最多 12 个标签
    assert len(parts) == 12


def test_format_category_summary():
    assert _format_category_summary({}) == "（暂无类别数据）"
    assert _format_category_summary({"风格": 5, "颜色": 9, "上装": 2}) == "颜色 9；风格 5；上装 2"
    assert _format_category_summary({"": 9, "风格": 2}) == "风格 2"
    ordered = _format_category_summary({f"类别{i}": i + 1 for i in range(10)}).split("；")
    assert len(ordered) == 8  # 最多 8 个类别
    assert ordered[0] == "类别9 10"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("   ", ""),
        ("简介正文", "简介正文"),
        ('"简介正文"', "简介正文"),
        ("“简介正文”", "简介正文"),
        ("- 简介正文", "简介正文"),
        ("## 简介正文", "简介正文"),
        ('```json\n{"bio": "x"}\n```', '{"bio": "x"}'),
        ("多   个    空格", "多 个 空格"),
        ("行1\n\n\n\n行2", "行1\n\n行2"),
    ],
)
def test_clean_model_output(raw, expected):
    assert _clean_model_output(raw) == expected


# ── generate_person_bio：假 Ollama 客户端 ──


class _FakeResponse:
    def __init__(self, status_code: int = 200, content: str = ""):
        self.status_code = status_code
        self._content = content

    def json(self):
        return {"message": {"content": self._content}}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "http error",
                request=httpx.Request("POST", "http://fake/api/chat"),
                response=httpx.Response(self.status_code),
            )


class _FakeOllamaClient:
    """可编程的 httpx.AsyncClient 替身：记录请求、可注入异常/响应。"""

    calls: list[tuple[str, dict]] = []
    init_kwargs: dict = {}
    content = "法式穿搭博主，偏爱连衣裙与白色系。"
    status_code = 200
    error: Exception | None = None

    def __init__(self, *args, **kwargs):
        type(self).init_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json=None, **kwargs):  # noqa: ANN001
        type(self).calls.append((url, json))
        if type(self).error is not None:
            raise type(self).error
        return _FakeResponse(type(self).status_code, type(self).content)


@pytest.fixture
def fake_ollama(monkeypatch):
    monkeypatch.setattr(pb.httpx, "AsyncClient", _FakeOllamaClient)
    monkeypatch.setattr(pb, "get_person_bio_prompt", lambda: _TEMPLATE)
    _FakeOllamaClient.calls = []
    _FakeOllamaClient.init_kwargs = {}
    _FakeOllamaClient.content = "法式穿搭博主，偏爱连衣裙与白色系。"
    _FakeOllamaClient.status_code = 200
    _FakeOllamaClient.error = None
    return _FakeOllamaClient


def _inputs(**overrides) -> PersonBioInputs:
    base = {
        "kind_label": "穿搭博主",
        "name": "小葵",
        "platform_label": "抖音",
        "ip_location": None,
        "top_tags": [{"name": "法式", "count": 5}],
        "by_category": {"风格": 5},
    }
    base.update(overrides)
    return PersonBioInputs(**base)


async def test_generate_success_payload_and_prompt(fake_ollama):
    bio = await generate_person_bio(_inputs())

    assert bio == "法式穿搭博主，偏爱连衣裙与白色系。"
    assert len(fake_ollama.calls) == 1
    url, payload = fake_ollama.calls[0]
    assert url == f"{settings.ollama_base_url}/api/chat"
    assert payload["model"] == settings.ollama_vision_model
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"] == {
        "temperature": 0.6,
        "top_p": 0.85,
        "num_predict": 512,
    }
    prompt = payload["messages"][0]["content"]
    assert "「小葵」" in prompt
    assert "属地：未知" in prompt  # ip_location 为空 → 未知
    assert "法式×5" in prompt
    assert "风格 5" in prompt
    assert "timeout" in fake_ollama.init_kwargs


async def test_generate_empty_platform_falls_back_to_other(fake_ollama):
    await generate_person_bio(_inputs(platform_label=""))
    prompt = fake_ollama.calls[0][1]["messages"][0]["content"]
    assert "平台：其他" in prompt


async def test_generate_template_unknown_placeholder_raises(fake_ollama, monkeypatch):
    monkeypatch.setattr(pb, "get_person_bio_prompt", lambda: "模板 {unknown_field}")
    with pytest.raises(BioGenerationError) as exc:
        await generate_person_bio(_inputs())
    assert "未知占位符" in str(exc.value)
    assert fake_ollama.calls == []  # 模板错误不调模型


async def test_generate_connect_error_message(fake_ollama):
    fake_ollama.error = httpx.ConnectError("connection refused")
    with pytest.raises(BioGenerationError) as exc:
        await generate_person_bio(_inputs())
    assert "无法连接本地 Ollama" in str(exc.value)
    assert settings.ollama_base_url in str(exc.value)


async def test_generate_http_status_error_message(fake_ollama):
    fake_ollama.status_code = 500
    with pytest.raises(BioGenerationError) as exc:
        await generate_person_bio(_inputs())
    assert "Ollama 返回错误状态 500" in str(exc.value)


async def test_generate_unexpected_error_wrapped(fake_ollama):
    fake_ollama.error = RuntimeError("socket 挂了")
    with pytest.raises(BioGenerationError) as exc:
        await generate_person_bio(_inputs())
    assert "调用本地模型失败" in str(exc.value)
    assert "socket 挂了" in str(exc.value)


async def test_generate_too_short_result_raises(fake_ollama):
    fake_ollama.content = "短"
    with pytest.raises(BioGenerationError) as exc:
        await generate_person_bio(_inputs())
    assert "为空或过短" in str(exc.value)


async def test_generate_long_result_truncated_at_sentence(fake_ollama):
    """超过 300 字：截断到最近的句号，避免半句。"""
    fake_ollama.content = "好" * 50 + "。" + "啊" * 400
    bio = await generate_person_bio(_inputs())
    assert len(bio) == 51
    assert bio.endswith("。")
    assert bio == "好" * 50 + "。"


async def test_generate_long_result_without_sentence_boundary(fake_ollama):
    fake_ollama.content = "啊" * 400
    bio = await generate_person_bio(_inputs())
    assert len(bio) == 300
    assert bio == "啊" * 300
