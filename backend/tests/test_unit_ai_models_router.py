"""AI 模型管理路由（routers/ai_models）的单测。

覆盖率的坑（写在这里提醒后来者）：这些用例**直接调用 handler**，不走 TestClient。
TestClient 的请求跑在 anyio 的 portal 线程里，pytest-cov 追踪不到——用 HTTP 写用例
会让路由代码显示 0% 覆盖。

这组接口把 Ollama 的管理能力透给前端，出错时的**错误码语义**是关键：
- 模型未安装 → 404（前端提示「去下载」）；Ollama 连不上 → 503（提示「检查服务」）；
- 拉取/更新 → SSE 流（下载进度），失败在流里给 error 事件而不是中断；
- 删除当前活跃模型 → 400 拦下（否则界面立刻失去视觉模型，且配置指向已删模型）；
- 统计接口的平均标签数只按**该模型成功日志的结构化快照**算，不混人工补打。
"""

import json

import httpx
import pytest
from fastapi import HTTPException

from app.config import settings
from app.database import async_session
from app.models.inspiration import AIAnalysisLog, AIAnalysisTag, Inspiration
from app.routers import ai_models as rt


class _Resp:
    def __init__(self, *, payload=None, status: int = 200, text: str = "", lines=None):
        self._payload = payload
        self.status_code = status
        self.text = text
        self._lines = lines or []
        self.request = httpx.Request("GET", "http://ollama/api")

    def json(self):
        return self._payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request, response=self
            )

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeClient:
    """按 method+path 返回预设响应；stream() 返回带 aiter_lines 的响应。"""

    def __init__(self, router):
        self._router = router

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _next(self, key):
        item = self._router.get(key)
        if isinstance(item, Exception):
            raise item
        return item or _Resp(payload={})

    async def get(self, url):
        return self._next(("GET", url))

    async def post(self, url, json=None):
        self._router.setdefault("payload", []).append(json)
        return self._next(("POST", url))

    async def delete(self, url, json=None):
        self._router.setdefault("payload", []).append(json)
        return self._next(("DELETE", url))

    def stream(self, method, url, json=None):
        return self._next(("STREAM", url))


@pytest.fixture
def ollama(monkeypatch):
    """替换 httpx.AsyncClient：用例通过返回的 dict 预置「按 URL 的响应」。"""
    router: dict = {}

    def _factory(**kwargs):
        return _FakeClient(router)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return router


@pytest.fixture(autouse=True)
def _restore_settings(monkeypatch):
    """切换模型会改全局 settings + 写 .env：测试里必须隔离（否则污染本机配置）。"""
    monkeypatch.setattr(settings, "ollama_vision_model", "qwen3-vl:8b")
    monkeypatch.setattr(settings, "ollama_embedding_model", "all-minilm")

    async def _no_env_write(_values):
        return None

    monkeypatch.setattr(rt, "_update_env_file", _no_env_write)


# ── 名称规范化 ──


def test_normalize_model_name_adds_latest_tag():
    assert rt._normalize_model_name("all-minilm") == "all-minilm:latest"
    assert rt._normalize_model_name("gemma3:4b") == "gemma3:4b"
    assert rt._normalize_model_name("") == ""


# ── 列表 ──


async def test_list_models_marks_active_embedding_and_vram(ollama):
    base = settings.ollama_base_url
    ollama[("GET", f"{base}/api/tags")] = _Resp(
        payload={
            "models": [
                {"name": "qwen3-vl:8b", "size": 5 * 1024**3, "modified_at": "2026-10-01"},
                {"name": "all-minilm:latest", "size": 46 * 1024**2},
            ]
        }
    )
    ollama[("GET", f"{base}/api/ps")] = _Resp(
        payload={"models": [{"name": "qwen3-vl:8b", "size_vram": 7 * 1024**3}]}
    )

    data = await rt.list_models()

    names = [m["name"] for m in data["models"]]
    assert names == ["qwen3-vl:8b", "all-minilm:latest"]
    # 配置里是 all-minilm（无 tag），也要能标出来
    assert data["models"][1]["is_embedding"] is True
    assert data["models"][0]["is_active"] is True
    assert data["models"][0]["vram_used"] == 7 * 1024**3
    assert data["models"][0]["loaded"] is True
    assert data["models"][1]["loaded"] is False
    assert data["models"][0]["size_display"]


async def test_list_models_survives_ps_failure(ollama):
    """GPU 信息拿不到（/api/ps 挂了）不影响列表：显存字段为 0，不整体 503。"""
    base = settings.ollama_base_url
    ollama[("GET", f"{base}/api/tags")] = _Resp(payload={"models": [{"name": "m:1"}]})
    ollama[("GET", f"{base}/api/ps")] = _Resp(status=500)

    data = await rt.list_models()

    assert data["models"][0]["vram_used"] == 0
    assert data["models"][0]["loaded"] is False


async def test_list_models_returns_503_when_ollama_unreachable(ollama):
    ollama[("GET", f"{settings.ollama_base_url}/api/tags")] = httpx.ConnectError("refused")

    with pytest.raises(HTTPException) as exc:
        await rt.list_models()

    assert exc.value.status_code == 503


# ── 状态 ──


async def test_ai_status_reports_connected_and_version(ollama, monkeypatch):
    base = settings.ollama_base_url
    ollama[("GET", f"{base}/api/version")] = _Resp(payload={"version": "0.12.3"})

    async def _uptime():
        return 3600

    monkeypatch.setattr(rt, "_get_ollama_uptime_seconds", _uptime)

    data = await rt.ai_status()

    assert data["ollama_connected"] is True
    assert data["ollama_version"] == "0.12.3"
    assert data["ollama_uptime_seconds"] == 3600
    assert data["active_model"] == "qwen3-vl:8b"


async def test_ai_status_disconnected_skips_uptime(ollama, monkeypatch):
    ollama[("GET", f"{settings.ollama_base_url}/api/version")] = httpx.ConnectError("refused")
    called = {"n": 0}

    async def _uptime():
        called["n"] += 1
        return 1

    monkeypatch.setattr(rt, "_get_ollama_uptime_seconds", _uptime)

    data = await rt.ai_status()

    assert data["ollama_connected"] is False
    assert data["ollama_uptime_seconds"] is None
    assert called["n"] == 0  # 没连上就别去查进程运行时长


# ── 安装校验 ──


async def test_ensure_installed_accepts_missing_tag(ollama):
    ollama[("GET", f"{settings.ollama_base_url}/api/tags")] = _Resp(
        payload={"models": [{"name": "all-minilm:latest"}]}
    )
    await rt._ensure_model_installed("all-minilm")  # 不应抛错


async def test_ensure_installed_raises_404_when_absent(ollama):
    ollama[("GET", f"{settings.ollama_base_url}/api/tags")] = _Resp(payload={"models": []})

    with pytest.raises(HTTPException) as exc:
        await rt._ensure_model_installed("不存在:1")

    assert exc.value.status_code == 404


async def test_ensure_installed_raises_503_when_unreachable(ollama):
    ollama[("GET", f"{settings.ollama_base_url}/api/tags")] = httpx.ConnectError("refused")

    with pytest.raises(HTTPException) as exc:
        await rt._ensure_model_installed("m:1")

    assert exc.value.status_code == 503


# ── 详情 / 复制 / 删除 ──


async def test_model_detail_maps_ollama_fields(ollama):
    ollama[("POST", f"{settings.ollama_base_url}/api/show")] = _Resp(
        payload={
            "details": {
                "parameter_size": "8B",
                "quantization_level": "Q4_K_M",
                "family": "qwen3vl",
                "families": ["qwen3vl"],
                "format": "gguf",
            },
            "model_info": {"general.architecture": "qwen3vl"},
            "template": "{{ .Prompt }}",
            "license": "Apache-2.0",
        }
    )

    data = await rt.model_detail("qwen3-vl:8b")

    assert data["parameter_size"] == "8B"
    assert data["quantization_level"] == "Q4_K_M"
    assert data["model_info"]["general.architecture"] == "qwen3vl"


async def test_model_detail_404_when_model_missing(ollama):
    ollama[("POST", f"{settings.ollama_base_url}/api/show")] = _Resp(status=404)

    with pytest.raises(HTTPException) as exc:
        await rt.model_detail("没有:1")

    assert exc.value.status_code == 404


async def test_copy_model_rejects_blank_destination():
    with pytest.raises(HTTPException) as exc:
        await rt.copy_model(source="a:1", destination="   ")
    assert exc.value.status_code == 400


async def test_copy_model_404_when_source_missing(ollama):
    ollama[("POST", f"{settings.ollama_base_url}/api/copy")] = _Resp(status=404)

    with pytest.raises(HTTPException) as exc:
        await rt.copy_model(source="没有:1", destination="new:1")

    assert exc.value.status_code == 404


async def test_copy_model_ok(ollama):
    ollama[("POST", f"{settings.ollama_base_url}/api/copy")] = _Resp(status=200)

    out = await rt.copy_model(source="a:1", destination="b:1")
    assert "已复制" in out["message"]


async def test_delete_model_refuses_active_model():
    """删当前活跃模型会让界面立刻失去视觉模型、配置还指向它——必须拦下。"""
    with pytest.raises(HTTPException) as exc:
        await rt.delete_model(settings.ollama_vision_model)

    assert exc.value.status_code == 400
    assert "正在使用" in exc.value.detail


async def test_delete_model_ok_and_404(ollama):
    base = settings.ollama_base_url
    ollama[("DELETE", f"{base}/api/delete")] = _Resp(status=200)
    out = await rt.delete_model("old:1")
    assert "已删除" in out["message"]

    ollama[("DELETE", f"{base}/api/delete")] = _Resp(status=404)
    with pytest.raises(HTTPException) as exc:
        await rt.delete_model("missing:1")
    assert exc.value.status_code == 404


# ── 切换模型 ──


async def test_set_active_model_checks_installed_then_persists(ollama, monkeypatch):
    ollama[("GET", f"{settings.ollama_base_url}/api/tags")] = _Resp(
        payload={"models": [{"name": "gemma3:4b"}]}
    )
    written: list[dict] = []

    async def _record(values):
        written.append(values)

    monkeypatch.setattr(rt, "_update_env_file", _record)

    out = await rt.set_active_model("gemma3:4b")

    assert settings.ollama_vision_model == "gemma3:4b"
    assert written == [{"OLLAMA_VISION_MODEL": "gemma3:4b"}]
    assert out["active_model"] == "gemma3:4b"


async def test_set_active_model_rejects_uninstalled(ollama, monkeypatch):
    """未安装就切换会让后续分析全部失败——必须先校验。"""
    ollama[("GET", f"{settings.ollama_base_url}/api/tags")] = _Resp(payload={"models": []})
    written: list[dict] = []

    async def _record(values):  # pragma: no cover - 不该被调用
        written.append(values)

    monkeypatch.setattr(rt, "_update_env_file", _record)

    with pytest.raises(HTTPException) as exc:
        await rt.set_active_model("没装:1")

    assert exc.value.status_code == 404
    assert written == []
    assert settings.ollama_vision_model == "qwen3-vl:8b"  # 配置没被改坏


async def test_set_embedding_model_persists(ollama, monkeypatch):
    ollama[("GET", f"{settings.ollama_base_url}/api/tags")] = _Resp(
        payload={"models": [{"name": "bge-m3:latest"}]}
    )
    written: list[dict] = []

    async def _record(values):
        written.append(values)

    monkeypatch.setattr(rt, "_update_env_file", _record)

    out = await rt.set_embedding_model("bge-m3")

    assert written == [{"OLLAMA_EMBEDDING_MODEL": "bge-m3"}]
    assert out["embedding_model"] == "bge-m3"


# ── GPU / 卸载 ──


async def test_gpu_stats_delegates_to_service(monkeypatch):
    async def _collect():
        return {"available": True, "devices": []}

    import app.services.gpu_service as gpu_service

    monkeypatch.setattr(gpu_service, "collect_gpu_stats", _collect)
    assert await rt.gpu_stats() == {"available": True, "devices": []}


async def test_unload_model_maps_upstream_status(ollama):
    base = settings.ollama_base_url
    ollama[("POST", f"{base}/api/generate")] = _Resp(status=200)
    assert "已发送卸载请求" in (await rt.unload_model("m:1"))["message"]

    # 上游 5xx → 502（不是把 500 原样透给前端）
    ollama[("POST", f"{base}/api/generate")] = _Resp(status=500)
    with pytest.raises(HTTPException) as exc:
        await rt.unload_model("m:1")
    assert exc.value.status_code == 502

    # 上游 404 → 原样透出
    ollama[("POST", f"{base}/api/generate")] = _Resp(status=404)
    with pytest.raises(HTTPException) as exc2:
        await rt.unload_model("m:1")
    assert exc2.value.status_code == 404


async def test_unload_model_503_when_unreachable(ollama):
    ollama[("POST", f"{settings.ollama_base_url}/api/generate")] = httpx.ConnectError("refused")

    with pytest.raises(HTTPException) as exc:
        await rt.unload_model("m:1")

    assert exc.value.status_code == 503


# ── SSE 拉取流 ──


async def test_pull_stream_emits_progress_and_done(ollama):
    ollama[("STREAM", f"{settings.ollama_base_url}/api/pull")] = _Resp(
        lines=[
            json.dumps({"status": "pulling manifest"}),
            json.dumps({"status": "downloading", "total": 100, "completed": 40}),
            json.dumps({"status": "success"}),
        ],
        payload={},
    )

    resp = await rt.pull_model("gemma3:4b")
    frames = [frame async for frame in resp.body_iterator]

    kinds = [json.loads(f[len("data: ") :])["type"] for f in frames]
    # success 那行也带 status 字段，所以先发一条 progress 再发 done（前端以 done 收尾）
    assert kinds == ["progress", "progress", "progress", "done"]
    dl = json.loads(frames[1][len("data: ") :])
    assert dl["total"] == 100 and dl["completed"] == 40


async def test_pull_stream_reports_error_frame(ollama):
    """上游中途报错：要给 error 事件并结束流（不能静默断流让前端一直转圈）。"""
    ollama[("STREAM", f"{settings.ollama_base_url}/api/pull")] = _Resp(
        lines=[json.dumps({"error": "manifest unknown"})], payload={}
    )

    resp = await rt.pull_model("bad:1")
    frames = [frame async for frame in resp.body_iterator]

    assert len(frames) == 1
    assert json.loads(frames[0][len("data: ") :])["type"] == "error"


async def test_pull_stream_maps_connection_failure_to_error_frame(ollama):
    ollama[("STREAM", f"{settings.ollama_base_url}/api/pull")] = httpx.ConnectError("refused")

    resp = await rt.pull_model("m:1")
    frames = [frame async for frame in resp.body_iterator]

    assert json.loads(frames[0][len("data: ") :])["type"] == "error"


async def test_update_model_requires_installed_then_streams(ollama):
    base = settings.ollama_base_url
    ollama[("GET", f"{base}/api/tags")] = _Resp(payload={"models": []})

    with pytest.raises(HTTPException) as exc:
        await rt.update_model("没装:1")
    assert exc.value.status_code == 404

    ollama[("GET", f"{base}/api/tags")] = _Resp(payload={"models": [{"name": "m:1"}]})
    ollama[("STREAM", f"{base}/api/pull")] = _Resp(lines=[json.dumps({"status": "success"})])
    resp = await rt.update_model("m:1")
    assert [f async for f in resp.body_iterator]


# ── 模型统计 ──


async def _tag(name: str) -> int:
    from app.models.tag import Tag

    async with async_session() as db:
        t = Tag(name=name, category="单品", source="manual")
        db.add(t)
        await db.commit()
        return t.id


async def _log(model: str, *, error: str | None = None, ms: int = 1000, tags: int = 1) -> int:
    tag_ids = [await _tag(f"{model}-标签{ms}-{i}") for i in range(tags)]

    async with async_session() as db:
        db.add(
            Inspiration(
                id=f"insp-{model}-{ms}-{tags}",
                source_type="douyin",
                file_path="a.jpg",
                media_type="image",
            )
        )
        log = AIAnalysisLog(
            inspiration_id=f"insp-{model}-{ms}-{tags}",
            model_name=model,
            log_type="analysis",
            processing_time_ms=ms,
            error=error,
        )
        db.add(log)
        await db.commit()
        for tag_id in tag_ids:
            db.add(AIAnalysisTag(log_id=log.id, tag_id=tag_id, confidence=0.9))
        await db.commit()
        return log.id


async def test_model_stats_aggregates_per_model_and_global_summary():
    await _log("m-a", ms=1000, tags=2)
    await _log("m-a", ms=3000, tags=4)
    await _log("m-b", ms=2000, tags=0)
    await _log("m-b", error="超时", ms=500, tags=0)

    async with async_session() as db:
        data = await rt.model_stats(db)

    # 第一行是全局汇总
    assert data["models"][0]["model_name"] == "（全部模型汇总）"
    assert data["total_analyses"] == 4
    assert data["models"][0]["success_count"] == 3
    assert data["models"][0]["failure_count"] == 1
    assert data["models"][0]["success_rate"] == 75.0

    by_name = {m["model_name"]: m for m in data["models"][1:]}
    assert by_name["m-a"]["total_analyses"] == 2
    assert by_name["m-a"]["avg_time_ms"] == 2000  # (1000+3000)/2
    assert by_name["m-a"]["avg_tags"] == 3.0  # 6 个快照 / 2 次成功
    assert by_name["m-b"]["failure_count"] == 1
    assert by_name["m-b"]["avg_tags"] == 0.0


async def test_model_stats_empty_returns_summary_placeholder():
    async with async_session() as db:
        data = await rt.model_stats(db)

    assert data["total_analyses"] == 0
    assert len(data["models"]) == 1
    assert data["models"][0]["success_rate"] == 0
    assert data["models"][0]["last_used"] == ""
