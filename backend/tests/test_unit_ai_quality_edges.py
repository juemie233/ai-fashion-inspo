"""AI 质量审核服务（ai_service/quality）的直连边缘单测。

既有 test_unit_ai_quality_handlers.py 覆盖路由/任务创建；本文件直接 await 服务函数，
补齐：``_ollama_vision_chat`` 的超时/连接/HTTP 状态/空内容/思考模式重试，
``_parse_ai_generated`` 阈值, ``_classifier_prefilter`` 的降级链，
``_write_quality_result`` 的 CAS/人工判定保护/generated 强制标记/自动入垃圾桶/异常，
``_precheck_image`` 三类硬性垃圾与解码失败，``_should_sample_ai_check`` 稳定抽样，
以及 ``check_image_quality`` 的完整/预检/初筛/解析失败/写回失败分支。
LLM（httpx）与向量/初筛器全部 mock，不触网。
"""

import base64
import io
import random

import httpx
import pytest
from PIL import Image

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.services.ai_service import quality as q
from app.services.vector import store as vector_store


# ═══════════════════════════════════════════════════════════════
#  _ollama_vision_chat：HTTP 分支
# ═══════════════════════════════════════════════════════════════


class _ChatResponse:
    def __init__(self, content: str | None, status: int = 200) -> None:
        self._content = content
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://ollama/api/chat")
            raise httpx.HTTPStatusError(
                "bad status",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )
        return None

    def json(self) -> dict:
        return {"message": {"content": self._content}}


class _ChatClient:
    """按顺序消费 responses/exceptions；记录每次请求体。"""

    plan: list = []
    requests: list[dict] = []
    timeouts: list = []

    def __init__(self, **kwargs) -> None:
        type(self).timeouts.append(kwargs.get("timeout"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    async def post(self, url: str, json=None):
        type(self).requests.append(json or {})
        item = type(self).plan.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def chat_http(monkeypatch):
    _ChatClient.plan = []
    _ChatClient.requests = []
    _ChatClient.timeouts = []
    monkeypatch.setattr(httpx, "AsyncClient", _ChatClient)
    return _ChatClient


_MODEL_CFG = {"think": False, "num_predict": 256, "timeout": 7, "num_ctx": 4096}


async def test_vision_chat_success_and_timeout_passed(chat_http):
    chat_http.plan = [_ChatResponse("模型原始输出")]

    raw, err = await q._ollama_vision_chat("b64", "prompt", _MODEL_CFG, temperature=0.1)

    assert (raw, err) == ("模型原始输出", None)
    assert chat_http.timeouts == [7]
    request = chat_http.requests[0]
    assert request["stream"] is False and request["think"] is False
    assert request["messages"][0]["images"] == ["b64"]
    assert request["options"]["temperature"] == 0.1


async def test_vision_chat_think_mode_retries_without_think(chat_http):
    """思考模式空内容时回退非思考模式；预算按 8192 放大。"""
    cfg = {**_MODEL_CFG, "think": True, "num_predict": 100}
    chat_http.plan = [_ChatResponse(""), _ChatResponse("有效输出")]

    raw, err = await q._ollama_vision_chat("b64", "prompt", cfg, temperature=0.0)

    assert (raw, err) == ("有效输出", None)
    assert [req["think"] for req in chat_http.requests] == [True, False]
    assert all(req["options"]["num_predict"] == 8192 for req in chat_http.requests)


async def test_vision_chat_empty_content_reports_error(chat_http):
    chat_http.plan = [_ChatResponse(""), _ChatResponse("")]
    cfg = {**_MODEL_CFG, "think": True}
    assert await q._ollama_vision_chat("b64", "p", cfg, 0.0) == ("", "模型返回空内容")


@pytest.mark.parametrize(
    "exc,expected",
    [
        (httpx.ConnectError("refused"), "无法连接 Ollama 服务"),
        (httpx.TimeoutException("t"), "调用 Ollama 超时"),
        (RuntimeError("奇怪的失败"), "调用失败: 奇怪的失败"),
    ],
)
async def test_vision_chat_transport_errors(chat_http, exc, expected):
    chat_http.plan = [exc]
    assert await q._ollama_vision_chat("b64", "p", _MODEL_CFG, 0.0) == ("", expected)


@pytest.mark.parametrize(
    "status,expected",
    [
        (429, "请求过于频繁，请稍后再试。"),
        (400, "Ollama 请求被拒绝（HTTP 400）"),
        (503, "Ollama 服务异常（HTTP 503）"),
    ],
)
async def test_vision_chat_http_status_messages(chat_http, status, expected):
    chat_http.plan = [_ChatResponse(None, status=status)]
    assert await q._ollama_vision_chat("b64", "p", _MODEL_CFG, 0.0) == ("", expected)


# ═══════════════════════════════════════════════════════════════
#  纯函数：AI 生成解析 / 抽样 / 预检
# ═══════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "parsed,threshold,expected",
    [
        ([], 0.6, False),  # 非 dict
        ({"is_ai_generated": False, "confidence": 0.99}, 0.6, False),
        ({"is_ai_generated": True, "confidence": "abc"}, 0.6, False),  # 非法置信度
        ({"is_ai_generated": True, "confidence": 0.5}, 0.6, False),  # 低于阈值
        ({"is_ai_generated": True, "confidence": 0.6}, 0.6, True),  # 等于阈值算达标
        ({"is_ai_generated": "true", "confidence": 0.9}, 0.6, True),
    ],
)
def test_parse_ai_generated_matrix(monkeypatch, parsed, threshold, expected):
    monkeypatch.setattr(settings, "ai_generated_confidence_threshold", threshold)
    assert q._parse_ai_generated(parsed) is expected


@pytest.mark.parametrize(
    "rate,expected",
    [(0, False), (-0.5, False), (1, True), (2, True)],
)
def test_should_sample_ai_check_extremes(rate, expected):
    assert q._should_sample_ai_check("any-id", rate) is expected


def test_should_sample_ai_check_is_stable_by_hash():
    import hashlib

    insp_id = "0f7c5b3a-1234-5678-9abc-def012345678"
    expected = int(hashlib.sha256(insp_id.encode()).hexdigest()[:8], 16) / 0x100000000 < 0.5
    assert q._should_sample_ai_check(insp_id, 0.5) is expected
    assert q._should_sample_ai_check(insp_id, 0.5) == q._should_sample_ai_check(insp_id, 0.5)


def _data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def test_precheck_image_rejects_too_small():
    assert q._precheck_image(_data_url(Image.new("RGB", (32, 32), "white"))) == "low_quality"


def test_precheck_image_rejects_extreme_aspect_ratio():
    assert q._precheck_image(_data_url(Image.new("RGB", (100, 1000), "white"))) == "partial"


def test_precheck_image_rejects_near_solid_color():
    assert q._precheck_image(_data_url(Image.new("RGB", (200, 200), (10, 20, 30)))) == "low_quality"


def test_precheck_image_passes_detailed_photo():
    rng = random.Random(42)
    image = Image.new("RGB", (200, 200))
    image.putdata(
        [(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)) for _ in range(200 * 200)]
    )
    assert q._precheck_image(_data_url(image)) is None


def test_precheck_image_degrades_on_bad_payload():
    assert q._precheck_image("data:image/png;base64,不是base64") is None


# ═══════════════════════════════════════════════════════════════
#  初筛器前置 / 自动入垃圾桶字段
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def classifier_env(monkeypatch):
    from app.services import quality_learner

    calls: dict = {"predict": [], "get_vector": []}

    async def _predict(vector):
        calls["predict"].append(vector)
        return (True, 0.9)

    async def _get_vector(kind, insp_id):
        calls["get_vector"].append((kind, insp_id))
        return [0.1, 0.2]

    monkeypatch.setattr(quality_learner, "predict_vector", _predict)
    monkeypatch.setattr(vector_store, "get_vector", _get_vector)
    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)
    return calls


async def test_classifier_prefilter_unavailable_when_lancedb_missing(
    classifier_env, monkeypatch
):
    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: False)
    assert await q._classifier_prefilter("id-1") is None
    assert classifier_env["predict"] == []


async def test_classifier_prefilter_skips_without_vector(classifier_env, monkeypatch):
    async def _none(_kind, _insp_id):
        return None

    monkeypatch.setattr(vector_store, "get_vector", _none)
    assert await q._classifier_prefilter("id-1") is None
    assert classifier_env["predict"] == []


async def test_classifier_prefilter_uses_provided_vector(classifier_env):
    result = await q._classifier_prefilter("id-1", vector=[0.5, 0.6])
    assert result == (True, 0.9)
    assert classifier_env["predict"] == [[0.5, 0.6]]
    assert classifier_env["get_vector"] == []  # 预取向量时不再查库


async def test_classifier_prefilter_fetches_vector_when_missing(classifier_env):
    result = await q._classifier_prefilter("id-2")
    assert result == (True, 0.9)
    assert classifier_env["get_vector"] == [("image", "id-2")]


@pytest.mark.parametrize(
    "status,enabled,expected",
    [
        ("approved", True, {}),
        ("rejected", False, {}),
        (
            "rejected",
            True,
            {"trash_reason": "质量差", "trash_source": "auto"},
        ),
    ],
)
def test_auto_trash_fields(monkeypatch, status, enabled, expected):
    monkeypatch.setattr(settings, "quality_auto_trash_rejected", enabled)
    fields = q._auto_trash_fields(status)
    assert set(fields) == ({"deleted_at", *expected} if expected else set())
    for key, value in expected.items():
        assert fields[key] == value
    if expected:
        assert fields["deleted_at"] is not None


# ═══════════════════════════════════════════════════════════════
#  写回：CAS / 人工判定保护 / generated 标记 / 异常
# ═══════════════════════════════════════════════════════════════


async def _insp(**overrides) -> str:
    data = {"file_path": "images/q.jpg", "media_type": "image"}
    data.update(overrides)
    async with async_session() as db:
        insp = Inspiration(**data)
        db.add(insp)
        await db.commit()
        return insp.id


async def _get(insp_id: str) -> Inspiration:
    async with async_session() as db:
        row = await db.get(Inspiration, insp_id)
        assert row is not None
        return row


async def test_write_quality_result_success(client, monkeypatch):
    monkeypatch.setattr(settings, "quality_auto_trash_rejected", False)
    insp_id = await _insp(quality_status="pending")

    async with async_session() as db:
        assert await q._write_quality_result(db, insp_id, "approved", None, False, False) is None

    row = await _get(insp_id)
    assert row.quality_status == "approved"
    assert row.quality_reviewed_by == "ai"
    assert row.deleted_at is None


async def test_write_quality_result_keeps_human_judgement(client):
    insp_id = await _insp(quality_status="approved", quality_reviewed_by="human")

    async with async_session() as db:
        err = await q._write_quality_result(db, insp_id, "rejected", "误判", False, True)

    assert err is not None and "人工判定" in err
    row = await _get(insp_id)
    assert row.quality_status == "approved"  # force=True 也不覆盖人工判定


async def test_write_quality_result_forces_generated_ai_flag(client):
    """本地出图（source_type=generated）即使模型未检出痕迹也强制标记 AI 生成。"""
    insp_id = await _insp(source_type="generated", quality_status="pending")

    async with async_session() as db:
        assert await q._write_quality_result(db, insp_id, "approved", None, False, False) is None

    row = await _get(insp_id)
    assert row.quality_status == "approved"
    assert row.is_ai_generated is True


async def test_write_quality_result_auto_trash_group(client, monkeypatch):
    """自动入垃圾桶：三字段整组写入（软删除不变量）。"""
    monkeypatch.setattr(settings, "quality_auto_trash_rejected", True)
    insp_id = await _insp(quality_status="pending")

    async with async_session() as db:
        assert await q._write_quality_result(db, insp_id, "rejected", "质量差", False, False) is None

    row = await _get(insp_id)
    assert row.deleted_at is not None
    assert row.trash_reason == "质量差"
    assert row.trash_source == "auto"


async def test_write_quality_result_exception_is_reported():
    class _BoomDb:
        async def execute(self, _stmt):
            raise RuntimeError("db down")

    err = await q._write_quality_result(_BoomDb(), "x", "approved", None, False, False)

    assert err is not None and err.startswith("写回失败: db down")


# ═══════════════════════════════════════════════════════════════
#  check_image_quality：编排分支
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def quality_env(monkeypatch):
    """默认：可读图、预检关闭、初筛跳过、模型返回穿搭通过。"""
    calls: dict = {"chat": [], "prefilter": 0}

    async def _read(_path):
        return "data:image/png;base64,xxx", None

    async def _no_prefilter(_insp_id, _vector=None):
        calls["prefilter"] += 1
        return None

    monkeypatch.setattr(q, "_read_image_base64", _read)
    monkeypatch.setattr(q, "_precheck_image", lambda _d: None)
    monkeypatch.setattr(q, "_classifier_prefilter", _no_prefilter)
    monkeypatch.setattr(
        q,
        "get_model_config",
        lambda _m: {"timeout": 1, "num_predict": 100, "num_ctx": 2048, "think": False},
    )
    monkeypatch.setattr(settings, "quality_precheck_enabled", False)
    monkeypatch.setattr(settings, "quality_auto_trash_rejected", False)
    monkeypatch.setattr(settings, "quality_rejected_ai_sample_rate", 0.0)
    monkeypatch.setattr(settings, "ai_generated_confidence_threshold", 0.8)

    def _chat_sequence(*replies):
        queue = list(replies)

        async def _chat(_image, prompt, _cfg, temperature):
            calls["chat"].append({"prompt": prompt, "temperature": temperature})
            return queue.pop(0)

        monkeypatch.setattr(q, "_ollama_vision_chat", _chat)
        return calls

    calls["set_chat"] = _chat_sequence
    return calls


async def test_quality_approved_path_with_ai_detection(client, quality_env):
    insp_id = await _insp(quality_status="pending")
    calls = quality_env["set_chat"](
        ('{"is_outfit": true, "reason": "完整穿搭", "reason_code": "partial"}', None),
        ('{"is_ai_generated": true, "confidence": 0.95}', None),
    )
    stats: dict = {}

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/q.jpg", stats=stats)

    assert result == ("approved", "完整穿搭", True)
    assert [c["temperature"] for c in calls["chat"]] == [0.1, 0.0]
    assert stats["prefilter"] == "skipped"
    assert stats["reason_code"] is None  # approved 一律不带拒绝原因码
    assert stats["prompt_hash"] and stats["model_name"]
    row = await _get(insp_id)
    assert row.quality_status == "approved"
    assert row.is_ai_generated is True
    assert row.quality_reason is None and row.quality_reason_code is None


async def test_quality_rejected_path_respects_sample_rate(client, quality_env):
    """被拒素材按抽样开关决定是否补做 AI 检测；原因码用模型给的合法值。"""
    insp_id = await _insp(quality_status="pending")
    calls = quality_env["set_chat"](
        ('{"is_outfit": false, "reason": "只有单品", "reason_code": "ad"}', None)
    )

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/q.jpg")

    assert result == ("rejected", "只有单品", False)
    assert len(calls["chat"]) == 1  # 抽样率 0 → 不做第二次调用
    row = await _get(insp_id)
    assert row.quality_status == "rejected"
    assert row.quality_reason == "只有单品"
    assert row.quality_reason_code == "ad"


async def test_quality_precheck_hit_short_circuits_vlm(client, quality_env, monkeypatch):
    monkeypatch.setattr(settings, "quality_precheck_enabled", True)
    insp_id = await _insp(quality_status="pending")
    monkeypatch.setattr(q, "_precheck_image", lambda _d: "partial")
    stats: dict = {}

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/q.jpg", stats=stats)

    assert result[0] == "rejected"
    assert "前置规则" in result[1]
    assert stats["prefilter"] == "precheck" and stats["reason_code"] == "partial"
    row = await _get(insp_id)
    assert row.quality_reason_code == "partial"


async def test_quality_prefilter_hit_rejects_directly(client, quality_env, monkeypatch):
    insp_id = await _insp(quality_status="pending")
    stats: dict = {}

    async def _hit(_insp_id, _vector=None):
        return (True, 0.87)

    monkeypatch.setattr(q, "_classifier_prefilter", _hit)

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/q.jpg", stats=stats)

    assert result[0] == "rejected"
    assert "初筛器判定为垃圾素材（置信度 0.87）" == result[1]
    assert stats["prefilter"] == "hit" and stats["prefilter_score"] == 0.87
    row = await _get(insp_id)
    assert row.quality_reason_code == "prefilter_reject"


async def test_quality_force_skips_prefilter(client, quality_env, monkeypatch):
    insp_id = await _insp(quality_status="pending")

    async def _should_not_run(*_a, **_k):
        raise AssertionError("force 复审不应走初筛器")

    monkeypatch.setattr(q, "_classifier_prefilter", _should_not_run)
    quality_env["set_chat"](
        ('{"is_outfit": true, "reason": "完整"}', None),
        ('{"is_ai_generated": false}', None),
    )
    stats: dict = {}

    async with async_session() as db:
        result = await q.check_image_quality(
            db, insp_id, "images/q.jpg", force=True, stats=stats
        )

    assert result[0] == "approved"
    assert stats["prefilter"] == "disabled"


async def test_quality_invalid_json_and_invalid_bool(client, quality_env):
    insp_id = await _insp(quality_status="pending")
    quality_env["set_chat"](("完全不是 JSON", None))
    async with async_session() as db:
        first = await q.check_image_quality(db, insp_id, "images/q.jpg")
    assert first[0] == "pending" and "无法解析模型输出" in first[1]

    quality_env["set_chat"](('{"is_outfit": "maybe", "reason": "?"}', None))
    async with async_session() as db:
        second = await q.check_image_quality(db, insp_id, "images/q.jpg")
    assert second[0] == "pending" and "无法判定 is_outfit" in second[1]


async def test_quality_vlm_error_keeps_pending(client, quality_env):
    insp_id = await _insp(quality_status="pending")
    quality_env["set_chat"](("", "无法连接 Ollama 服务"))

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/q.jpg")

    assert result == ("pending", "审核失败: 无法连接 Ollama 服务", False)
    assert (await _get(insp_id)).quality_status == "pending"


async def test_quality_missing_file_rejects_directly(client, quality_env, monkeypatch):
    insp_id = await _insp(quality_status="pending")

    async def _missing(_path):
        raise FileNotFoundError("图片不存在")

    monkeypatch.setattr(q, "_read_image_base64", _missing)

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/gone.jpg")

    assert result == ("rejected", "文件缺失", False)
    row = await _get(insp_id)
    assert row.quality_status == "rejected"


async def test_quality_read_error_keeps_pending(client, quality_env, monkeypatch):
    insp_id = await _insp(quality_status="pending")

    async def _boom(_path):
        raise RuntimeError("读图炸了")

    monkeypatch.setattr(q, "_read_image_base64", _boom)

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/q.jpg")

    assert result == ("pending", "审核失败: 读图炸了", False)


async def test_quality_writeback_failure_reports_pending(client, quality_env, monkeypatch):
    """写回失败（CAS 未命中/DB 异常）：返回 pending + 可读原因，避免记假成功。"""
    insp_id = await _insp(quality_status="approved", quality_reviewed_by="human")
    quality_env["set_chat"](
        ('{"is_outfit": true, "reason": "完整"}', None),
        ('{"is_ai_generated": false}', None),
    )

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/q.jpg", force=True)

    assert result[0] == "pending"
    assert "人工判定" in result[1]
    assert (await _get(insp_id)).quality_status == "approved"


async def test_quality_auto_trash_on_rejected(client, quality_env, monkeypatch):
    """审核拒绝 + 自动入垃圾桶开关：写回时三字段一起落库。"""
    monkeypatch.setattr(settings, "quality_auto_trash_rejected", True)
    insp_id = await _insp(quality_status="pending")
    quality_env["set_chat"](
        ('{"is_outfit": false, "reason": "非穿搭", "reason_code": "not_outfit"}', None)
    )

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/q.jpg")

    assert result[0] == "rejected"
    row = await _get(insp_id)
    assert row.deleted_at is not None
    assert row.trash_reason == "质量差" and row.trash_source == "auto"
    assert row.deleted_at is not None


async def test_quality_precheck_write_failure_returns_pending(client, quality_env, monkeypatch):
    """预检命中但写回失败：返回 pending + 原因（不记假拒绝）。"""
    monkeypatch.setattr(settings, "quality_precheck_enabled", True)
    insp_id = await _insp(quality_status="pending")
    monkeypatch.setattr(q, "_precheck_image", lambda _d: "low_quality")

    async def _write_fail(*_args, **_kwargs):
        return "写回失败: db locked"

    monkeypatch.setattr(q, "_write_quality_result", _write_fail)
    stats: dict = {}

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/q.jpg", stats=stats)

    assert result == ("pending", "写回失败: db locked", False)
    assert stats["prefilter"] == "precheck"


async def test_quality_missing_file_write_failure_returns_pending(
    client, quality_env, monkeypatch
):
    insp_id = await _insp(quality_status="pending")

    async def _missing(_path):
        raise FileNotFoundError("图片不存在")

    async def _write_fail(*_args, **_kwargs):
        return "写回失败: db down"

    monkeypatch.setattr(q, "_read_image_base64", _missing)
    monkeypatch.setattr(q, "_write_quality_result", _write_fail)

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/gone.jpg")

    assert result == ("pending", "写回失败: db down", False)


async def test_quality_prefilter_write_failure_returns_pending(
    client, quality_env, monkeypatch
):
    insp_id = await _insp(quality_status="pending")

    async def _hit(_insp_id, _vector=None):
        return (True, 0.8)

    async def _write_fail(*_args, **_kwargs):
        return "写回失败: db down"

    monkeypatch.setattr(q, "_classifier_prefilter", _hit)
    monkeypatch.setattr(q, "_write_quality_result", _write_fail)

    async with async_session() as db:
        result = await q.check_image_quality(db, insp_id, "images/q.jpg")

    assert result[0] == "pending" and result[1] == "写回失败: db down"
