"""AI 分析编排（单图 + 视频多帧）的测试。

这一层是「打标」的唯一入口，也是全项目最贵的一步（12.5 秒/条）。它有两个特别值得锁的点：

1. **重试阶梯**：`_call_ollama_vision` 会对「图片超出上下文窗口」「输出被 token 预算截断」
   「思考模型返回空内容」分别重试。重试错了不会报错，只会**白白多烧一轮 GPU**；
   重试漏了则整条素材直接失败，而这三种情况都可以通过加倍 num_ctx / 关掉 think 救回来。
2. **成败判定与日志**：无论成功失败都必须写一条分析日志（含 error 与处理耗时）——
   失败不写日志，用户就看不到「为什么没打上标」，也没法一键重试失败项。

Ollama 在测试里用假 HTTP 客户端替身（按调用次数返回预设响应）。
"""

import json

import httpx
import pytest

from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.services.ai_service import analyze as az


# ── 假 Ollama 客户端 ──


class _Resp:
    def __init__(self, payload=None, status: int = 200, error: str | None = None):
        self._payload = payload
        self.status_code = status
        self._error = error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "http://ollama/api/chat"),
                response=self,
            )
        return None

    def json(self):
        if self._error is not None:
            return {"error": self._error}
        return self._payload


class _FakeOllama:
    """按队列返回响应（每项是 _Resp 或异常）；记录每次请求的 payload。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.calls = 0

    async def post(self, url, json=None):
        self.calls += 1
        self.requests.append(json or {})
        item = self._responses.pop(0) if self._responses else _Resp({"message": {"content": "{}"}})
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _fake_http(monkeypatch):
    """默认返回一段合法 JSON 输出；用例可替换 holder['client']。"""
    holder = {"client": _FakeOllama([_Resp({"message": {"content": '{"items": [{"type": "针织开衫"}]}'}})])}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return holder["client"]

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(az, "get_model_config", lambda name: {
        "timeout": 5, "temperature": 0.1, "top_p": 0.9, "top_k": 40,
        "num_predict": 512, "num_ctx": 4096, "think": False,
    })
    monkeypatch.setattr(az, "get_model_prompt", lambda name: "分析这张穿搭图")

    async def _read(path):
        return "base64data", 0.5

    monkeypatch.setattr(az, "_read_image_base64", _read)
    return holder


async def _insp(insp_id: str, *, media_type: str = "image") -> str:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg",
                media_type=media_type,
                quality_status="approved",
            )
        )
        await db.commit()
    return insp_id


# ── 纯函数 ──


def test_prompt_version_is_short_hex_hash():
    v = az._prompt_version("同样的提示词")
    assert len(v) == 8 and all(c in "0123456789abcdef" for c in v)
    assert v == az._prompt_version("同样的提示词")  # 稳定，可用于版本追溯
    assert v != az._prompt_version("不同的提示词")


@pytest.mark.parametrize(
    "status,detail,size,expect",
    [
        (400, "image decode failed", 0.5, "不支持视觉功能"),
        (400, "too big", 8.0, "图片过大"),
        (400, "context length exceeded", 0.5, "超出模型上下文限制"),
        (400, "", 0.5, "请求参数错误"),
        (404, "", 0.5, "未安装"),
        (413, "", 9.0, "超过服务端限制"),
        (429, "", 0.5, "请求过于频繁"),
        (500, "boom", 0.5, "Ollama 服务内部错误"),
        (502, "", 0.5, "暂时不可用"),
        (503, "", 0.5, "暂时不可用"),
        (418, "teapot", 0.5, "HTTP 418"),
    ],
)
def test_http_error_message_maps_each_status(status, detail, size, expect):
    assert expect in az._http_error_message(status, detail, size, "m1")


# ── _call_ollama_vision：重试阶梯 ──


async def test_call_returns_content_on_success(_fake_http):
    _fake_http["client"] = _FakeOllama([_Resp({"message": {"content": "结果"}})])

    out = await az._call_ollama_vision("b64", "p", {"num_predict": 512, "num_ctx": 4096,
                                                    "temperature": 0.1, "top_p": 0.9,
                                                    "top_k": 40, "timeout": 5}, 0.5, "i1", "m1")

    assert out == "结果"
    body = _fake_http["client"].requests[0]
    assert body["model"] == "m1"
    assert body["messages"][0]["images"] == ["b64"]
    assert body["options"]["num_ctx"] == 4096


async def test_call_doubles_num_ctx_when_image_exceeds_context(_fake_http):
    """400 + context 关键词：先加倍 num_ctx 重试，第二次成功就算成功（不白失败）。"""
    _fake_http["client"] = _FakeOllama([
        _Resp(status=400, error="input is too long for context window"),
        _Resp({"message": {"content": "恢复成功"}}),
    ])

    out = await az._call_ollama_vision("b64", "p", {"num_predict": 512, "num_ctx": 4096,
                                                    "temperature": 0.1, "top_p": 0.9,
                                                    "top_k": 40, "timeout": 5}, 0.5, "i1", "m1")

    assert out == "恢复成功"
    client = _fake_http["client"]
    assert client.calls == 2
    assert client.requests[0]["options"]["num_ctx"] == 4096
    assert client.requests[1]["options"]["num_ctx"] == 8192  # 加倍


async def test_call_raises_context_error_when_still_too_small(_fake_http):
    _fake_http["client"] = _FakeOllama([
        _Resp(status=400, error="context window exceeded"),
        _Resp(status=400, error="context window exceeded"),
    ])

    with pytest.raises(RuntimeError) as exc:
        await az._call_ollama_vision("b64", "p", {"num_predict": 512, "num_ctx": 4096,
                                                  "temperature": 0.1, "top_p": 0.9,
                                                  "top_k": 40, "timeout": 5}, 0.5, "i1", "m1")

    assert "上下文窗口" in str(exc.value)


async def test_call_retries_when_output_truncated(_fake_http):
    """done_reason=length：残缺输出要丢弃并用更大窗口重试（不能把半截 JSON 当结果）。"""
    _fake_http["client"] = _FakeOllama([
        _Resp({"message": {"content": '{"items": ['}, "done_reason": "length"}),
        _Resp({"message": {"content": '{"items": [{"type": "针织开衫"}]}'}, "done_reason": "stop"}),
    ])

    out = await az._call_ollama_vision("b64", "p", {"num_predict": 512, "num_ctx": 4096,
                                                    "temperature": 0.1, "top_p": 0.9,
                                                    "top_k": 40, "timeout": 5}, 0.5, "i1", "m1")

    assert out == '{"items": [{"type": "针织开衫"}]}'
    assert _fake_http["client"].calls == 2


async def test_call_raises_truncated_error_when_always_incomplete(_fake_http):
    _fake_http["client"] = _FakeOllama([
        _Resp({"message": {"content": "{"}, "done_reason": "length"}),
        _Resp({"message": {"content": "{"}, "done_reason": "length"}),
    ])

    with pytest.raises(RuntimeError) as exc:
        await az._call_ollama_vision("b64", "p", {"num_predict": 512, "num_ctx": 4096,
                                                  "temperature": 0.1, "top_p": 0.9,
                                                  "top_k": 40, "timeout": 5}, 0.5, "i1", "m1")

    assert "截断" in str(exc.value)


async def test_call_falls_back_to_non_think_when_thinking_returns_empty(_fake_http, monkeypatch):
    """思考模型思维链耗尽预算会返回空内容：要自动关掉 think 再试一次。"""
    monkeypatch.setattr(az, "get_model_config", lambda name: {
        "timeout": 5, "temperature": 0.1, "top_p": 0.9, "top_k": 40,
        "num_predict": 512, "num_ctx": 4096, "think": True,
    })
    _fake_http["client"] = _FakeOllama([
        _Resp({"message": {"content": ""}}),
        _Resp({"message": {"content": "非思考结果"}}),
    ])

    out = await az._call_ollama_vision("b64", "p", {"num_predict": 512, "num_ctx": 4096,
                                                    "temperature": 0.1, "top_p": 0.9,
                                                    "top_k": 40, "timeout": 5,
                                                    "think": True}, 0.5, "i1", "m1")

    assert out == "非思考结果"
    client = _fake_http["client"]
    assert client.requests[0]["think"] is True
    assert client.requests[1]["think"] is False
    assert client.requests[0]["options"]["num_predict"] >= 8192  # 思考模式放大预算


@pytest.mark.parametrize(
    "exc,expect",
    [
        (httpx.ConnectError("refused"), "无法连接 Ollama"),
        (httpx.TimeoutException("slow"), "超时"),
    ],
)
async def test_call_maps_network_failures(_fake_http, exc, expect):
    _fake_http["client"] = _FakeOllama([exc])

    with pytest.raises(RuntimeError) as error:
        await az._call_ollama_vision("b64", "p", {"num_predict": 512, "num_ctx": 4096,
                                                  "temperature": 0.1, "top_p": 0.9,
                                                  "top_k": 40, "timeout": 5}, 0.5, "i1", "m1")

    assert expect in str(error.value)


async def test_call_rejects_malformed_response(_fake_http):
    _fake_http["client"] = _FakeOllama([_Resp({"unexpected": True})])

    with pytest.raises(RuntimeError) as exc:
        await az._call_ollama_vision("b64", "p", {"num_predict": 512, "num_ctx": 4096,
                                                  "temperature": 0.1, "top_p": 0.9,
                                                  "top_k": 40, "timeout": 5}, 0.5, "i1", "m1")

    assert "格式异常" in str(exc.value)


async def test_call_raises_when_ollama_returns_empty(_fake_http):
    # 空内容不是截断也不是上下文超限 → 会按「加倍 num_ctx」再试一轮，共两次请求
    _fake_http["client"] = _FakeOllama([
        _Resp({"message": {"content": ""}}),
        _Resp({"message": {"content": ""}}),
    ])

    with pytest.raises(RuntimeError) as exc:
        await az._call_ollama_vision("b64", "p", {"num_predict": 512, "num_ctx": 4096,
                                                  "temperature": 0.1, "top_p": 0.9,
                                                  "top_k": 40, "timeout": 5}, 0.5, "i1", "m1")

    assert "空内容" in str(exc.value)


# ── analyze_image ──


async def _logs(insp_id: str) -> list[AIAnalysisLog]:
    async with async_session() as db:
        rows = (
            await db.execute(
                __import__("sqlalchemy").select(AIAnalysisLog).where(
                    AIAnalysisLog.inspiration_id == insp_id
                )
            )
        ).scalars().all()
    return list(rows)


async def test_analyze_image_success_writes_log_and_tags(_fake_http):
    await _insp("ai1")
    _fake_http["client"] = _FakeOllama([
        _Resp(
            {
                "message": {
                    "content": json.dumps(
                        {
                            "style": ["极简"],
                            "items": [{"type": "针织开衫", "color": "米色"}],
                            "dominant_colors": ["#ffffff // 主色（明显）", "（忽略）"],
                        },
                        ensure_ascii=False,
                    )
                }
            }
        )
    ])

    async with async_session() as db:
        ok = await az.analyze_image(db, "ai1", "images/x/ai1.jpg")

    assert ok is True
    logs = await _logs("ai1")
    assert len(logs) == 1
    assert logs[0].error is None
    assert logs[0].prompt_version  # 记录 prompt 版本，便于回溯
    async with async_session() as db:
        insp = await db.get(Inspiration, "ai1")
    # 主色调清洗：去掉 // 注释与括号描述，空值丢弃
    assert json.loads(insp.dominant_colors) == ["#ffffff"]


async def test_analyze_image_apply_tags_false_keeps_material_tags(_fake_http):
    """多模型组合分析：只写日志与快照，不改素材正式标签。"""
    await _insp("ai2")
    _fake_http["client"] = _FakeOllama([
        _Resp({"message": {"content": json.dumps({"items": [{"type": "阔腿裤"}]}, ensure_ascii=False)}})
    ])

    async with async_session() as db:
        ok = await az.analyze_image(db, "ai2", "images/x/ai2.jpg", apply_tags=False)

    assert ok is True
    async with async_session() as db:
        from app.models.tag import InspirationTag

        links = (
            await db.execute(
                __import__("sqlalchemy").select(InspirationTag).where(
                    InspirationTag.inspiration_id == "ai2"
                )
            )
        ).scalars().all()
    assert links == []  # 素材标签保持不变


async def test_analyze_image_failure_records_error_log(_fake_http):
    """失败必须留日志（用户要能看到原因并一键重试）。"""
    await _insp("ai3")
    _fake_http["client"] = _FakeOllama([httpx.ConnectError("refused")])

    async with async_session() as db:
        ok = await az.analyze_image(db, "ai3", "images/x/ai3.jpg")

    assert ok is False
    logs = await _logs("ai3")
    assert len(logs) == 1
    assert "无法连接" in (logs[0].error or "")


async def test_analyze_image_unparsable_output_records_error(_fake_http):
    await _insp("ai4")
    _fake_http["client"] = _FakeOllama([_Resp({"message": {"content": "这不是 JSON"}})])

    async with async_session() as db:
        ok = await az.analyze_image(db, "ai4", "images/x/ai4.jpg")

    assert ok is False
    logs = await _logs("ai4")
    assert "无法解析" in (logs[0].error or "") or "截断" in (logs[0].error or "")


async def test_analyze_image_vector_rebuild_failure_is_swallowed(_fake_http, monkeypatch):
    """向量重建失败不能影响分析结果（分析已成功落库）。"""
    await _insp("ai5")
    _fake_http["client"] = _FakeOllama([
        _Resp({"message": {"content": json.dumps({"items": [{"type": "针织开衫"}]}, ensure_ascii=False)}})
    ])

    import app.services.vector_service as vector_service

    async def _boom(db, insp_id):
        raise RuntimeError("lance 挂了")

    monkeypatch.setattr(vector_service, "rebuild_inspiration_vectors", _boom)

    async with async_session() as db:
        ok = await az.analyze_image(db, "ai5", "images/x/ai5.jpg")

    assert ok is True


# ── analyze_video：多帧融合 ──


async def test_analyze_video_without_frames_returns_false():
    async with async_session() as db:
        assert await az.analyze_video(db, "v0", []) is False


async def test_analyze_video_fuses_tags_by_max_confidence(_fake_http):
    """多帧同名标签取最高置信度；主色调取首个有值的帧；全程只写一条日志。"""
    await _insp("v1", media_type="video")
    _fake_http["client"] = _FakeOllama([
        _Resp({"message": {"content": json.dumps({"style": ["极简"]}, ensure_ascii=False)}}),
        _Resp({"message": {"content": json.dumps({"items": [{"type": "针织开衫"}], "dominant_colors": ["#000000"]}, ensure_ascii=False)}}),
    ])

    async with async_session() as db:
        ok = await az.analyze_video(db, "v1", ["kf/1.jpg", "kf/2.jpg"])

    assert ok is True
    logs = await _logs("v1")
    assert len(logs) == 1  # 整段视频只有一条日志
    assert logs[0].raw_response and "[帧 1]" in logs[0].raw_response
    async with async_session() as db:
        insp = await db.get(Inspiration, "v1")
    assert json.loads(insp.dominant_colors) == ["#000000"]


async def test_analyze_video_tolerates_partial_frame_failures(_fake_http):
    """一帧失败不影响其余帧（视频分析按帧独立，失败只记该帧）。"""
    await _insp("v2", media_type="video")
    _fake_http["client"] = _FakeOllama([
        httpx.ConnectError("boom"),
        _Resp({"message": {"content": json.dumps({"items": [{"type": "阔腿裤"}]}, ensure_ascii=False)}}),
    ])

    async with async_session() as db:
        ok = await az.analyze_video(db, "v2", ["kf/1.jpg", "kf/2.jpg"])

    assert ok is True


async def test_analyze_video_all_frames_fail_returns_false(_fake_http):
    await _insp("v3", media_type="video")
    _fake_http["client"] = _FakeOllama([httpx.ConnectError("boom"), httpx.ConnectError("boom")])

    async with async_session() as db:
        ok = await az.analyze_video(db, "v3", ["kf/1.jpg", "kf/2.jpg"])

    assert ok is False
    logs = await _logs("v3")
    assert "全部关键帧分析失败" in (logs[0].error or "")


async def test_analyze_video_zero_tags_is_success_no_content(_fake_http):
    """帧都解析成功但零标签 = 模型判定「没有穿搭内容」：算成功，不再算失败。

    回归（2026-10）：原先按失败计 → 这类素材永远挂在「未分析」里（实测 144 个未分析
    里 10 个是这种：蛋糕照/风景照）。
    """
    await _insp("v4", media_type="video")
    _fake_http["client"] = _FakeOllama([
        _Resp({"message": {"content": json.dumps({"items": []}, ensure_ascii=False)}}),
    ])

    async with async_session() as db:
        outcome = await az.analyze_video_detail(db, "v4", ["kf/1.jpg"])

    assert outcome.success is True
    assert outcome.empty_payload is True
    logs = await _logs("v4")
    assert logs[0].error is None  # 成功日志：素材从此离开「未分析」

