"""ComfyUI 就绪探测（`services/comfyui/probe.py`）单测。

「运行」按钮该不该亮取决于这里，探测错了有两种坏结果：**误报就绪**（用户点下去才在
ComfyUI 里炸）与**误报不可用**（明明能用却灰着）。所以核心是「每种失败都要被归类，
且 ready 的判定口径唯一」：

- ComfyUI 没启动 → `reason` 给可执行的启动提示，不抛 500；
- 读节点信息失败 / 模板缺失 / 节点缺失 / 模型未就位 → 各自的状态码写进 `models`，
  `reason` 汇总，便于用户知道该修哪一层；
- 云端档位（provider=ark）只看配置，不依赖 ComfyUI 权重；
- `_object_info` 有 TTL 缓存（几 MB 的节点表，不能被每次刷新重复拉）。
"""

from types import SimpleNamespace

import pytest

from app.services.comfyui import probe as pr
from app.services.comfyui.client import ComfyUIError


class _FakeClient:
    """ComfyUIClient 最小替身：可配置 system_stats / object_info 的行为。"""

    base_url = "http://127.0.0.1:8188"

    def __init__(self, *, stats=None, stats_error=None, info=None, info_error=None):
        self._stats = stats
        self._stats_error = stats_error
        self._info = info
        self._info_error = info_error
        self.aclosed = False
        self.info_calls = 0

    async def system_stats(self):
        if self._stats_error is not None:
            raise self._stats_error
        return self._stats

    async def object_info(self, _node=None):
        self.info_calls += 1
        if self._info_error is not None:
            raise self._info_error
        return self._info

    async def aclose(self):
        self.aclosed = True


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """每个用例独立：清空节点表缓存、关闭云端档位（避免依赖本机 .env）。"""
    monkeypatch.setattr(pr, "_object_info_cache", {})
    monkeypatch.setattr(pr.settings, "ark_enabled", False)
    monkeypatch.setattr(pr.settings, "ark_api_key", "")
    monkeypatch.setattr(pr.settings, "seedream_model", "seedream-test")
    monkeypatch.setattr(pr.settings, "ark_base_url", "https://ark.test")
    monkeypatch.setattr(pr, "MODEL_SPECS", {"comfy": {"workflow": "wf.json"}})


class _FakeHttpx:
    def __init__(self, *, response=None, error=None):
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, _url):
        if self._error is not None:
            raise self._error
        return self._response


class TestVram:
    def test_extracts_gib_from_first_device(self):
        stats = {"devices": [{"vram_total": 16 * 2**30, "vram_free": 8 * 2**30}]}
        assert pr._vram_gib(stats) == (16.0, 8.0)

    def test_no_devices_is_zero(self):
        assert pr._vram_gib({}) == (0.0, 0.0)


class TestOllamaResident:
    async def test_parses_resident_models(self, monkeypatch):
        payload = {
            "models": [
                {"name": "qwen3-vl:8b", "size_vram": 2 * 2**30},
                {"model": "all-minilm", "size": 1 * 2**30},
                {"size": 0},  # 没有 name/model：用 "?" 占位，不能让整段失败
            ]
        }
        monkeypatch.setattr(
            pr.httpx,
            "AsyncClient",
            lambda **_kw: _FakeHttpx(response=SimpleNamespace(status_code=200, json=lambda: payload)),
        )

        result = await pr.ollama_resident()

        assert result[0] == {"name": "qwen3-vl:8b", "size_gib": 2.0}
        assert result[1] == {"name": "all-minilm", "size_gib": 1.0}
        assert result[2]["name"] == "?"

    async def test_non_200_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            pr.httpx,
            "AsyncClient",
            lambda **_kw: _FakeHttpx(response=SimpleNamespace(status_code=500, json=lambda: {})),
        )
        assert await pr.ollama_resident() == []

    async def test_connection_error_returns_empty(self, monkeypatch):
        """Ollama 没起只是诊断信息缺失，不能影响出图判断。"""
        monkeypatch.setattr(
            pr.httpx,
            "AsyncClient",
            lambda **_kw: _FakeHttpx(error=ConnectionError("refused")),
        )
        assert await pr.ollama_resident() == []


class TestObjectInfoCache:
    async def test_second_call_within_ttl_uses_cache(self):
        client = _FakeClient(info={"node_a": {}})

        first = await pr._object_info_cached(client)
        second = await pr._object_info_cached(client)

        assert first == second == {"node_a": {}}
        assert client.info_calls == 1  # TTL 内只拉一次

    async def test_expired_ttl_refetches(self, monkeypatch):
        monkeypatch.setattr(pr, "_OBJECT_INFO_TTL", -1.0)  # 立即过期
        client = _FakeClient(info={"node_a": {}})

        await pr._object_info_cached(client)
        await pr._object_info_cached(client)

        assert client.info_calls == 2

    async def test_cache_is_keyed_by_base_url(self):
        a = _FakeClient(info={"a": {}})
        b = _FakeClient(info={"b": {}})
        b.base_url = "http://127.0.0.1:8189"

        assert await pr._object_info_cached(a) == {"a": {}}
        assert await pr._object_info_cached(b) == {"b": {}}
        assert a.info_calls == 1 and b.info_calls == 1


class TestArkStatus:
    def test_enabled_with_key_is_ready(self, monkeypatch):
        monkeypatch.setattr(pr.settings, "ark_enabled", True)
        monkeypatch.setattr(pr.settings, "ark_api_key", "sk-test")

        status = pr._ark_probe_status()

        assert status["ready"] is True
        assert status["reason"] is None
        assert status["model"] == "seedream-test"

    def test_disabled_reports_reason(self):
        status = pr._ark_probe_status()
        assert status["ready"] is False
        assert "云端出图未启用" in status["reason"]

    def test_missing_key_reports_reason(self, monkeypatch):
        monkeypatch.setattr(pr.settings, "ark_enabled", True)
        monkeypatch.setattr(pr.settings, "ark_api_key", "")

        status = pr._ark_probe_status()

        assert status["ready"] is False
        assert "ARK_API_KEY" in status["reason"]


class TestProbedStatusSerialization:
    def test_to_dict_has_all_frontend_fields(self):
        status = pr.ProbedStatus(ready=True, reason=None)
        data = status.to_dict()
        assert set(data) == {"ready", "reason", "comfyui", "models", "ollama_resident", "ark"}


class TestProbeWith:
    """`_probe_with` 的各条失败分支：每层故障都要落到明确的 models 状态与 reason。"""

    async def test_comfyui_not_running(self, monkeypatch):
        async def _no_ollama():
            return []

        monkeypatch.setattr(pr, "ollama_resident", _no_ollama)
        client = _FakeClient(
            stats_error=ComfyUIError("连不上", kind="not_running"),
        )

        status = await pr._probe_with(client)

        assert status.ready is False
        assert "双击" in status.reason  # 给可执行的启动提示
        assert status.models == {"comfy": "unknown"}
        assert status.ollama_resident == []

    async def test_comfyui_other_error(self, monkeypatch):
        async def _no_ollama():
            return []

        monkeypatch.setattr(pr, "ollama_resident", _no_ollama)
        client = _FakeClient(stats_error=ComfyUIError("boom", kind="http"))

        status = await pr._probe_with(client)

        assert status.ready is False
        assert "ComfyUI 不可用" in status.reason

    async def test_object_info_failure(self, monkeypatch):
        async def _no_ollama():
            return []

        monkeypatch.setattr(pr, "ollama_resident", _no_ollama)
        client = _FakeClient(
            stats={"devices": [{"vram_total": 16 * 2**30, "vram_free": 4 * 2**30}],
                   "system": {"comfyui_version": "0.4.2"}},
            info_error=ComfyUIError("节点表读不到", kind="http"),
        )

        status = await pr._probe_with(client)

        assert status.ready is False
        assert "读取 ComfyUI 节点信息失败" in status.reason
        assert status.comfyui["vram_total_gib"] == 16.0
        assert status.models == {"comfy": "unknown"}

    async def test_missing_workflow(self, monkeypatch):
        async def _no_ollama():
            return []

        monkeypatch.setattr(pr, "ollama_resident", _no_ollama)

        def _missing(_name):
            raise FileNotFoundError("wf.json")

        monkeypatch.setattr(pr.wf, "load_template", _missing)
        client = _FakeClient(stats={}, info={})

        status = await pr._probe_with(client)

        assert status.models == {"comfy": "missing_workflow"}
        assert "模板文件缺失" in status.reason
        assert status.ready is False

    async def test_missing_nodes(self, monkeypatch):
        async def _no_ollama():
            return []

        monkeypatch.setattr(pr, "ollama_resident", _no_ollama)
        monkeypatch.setattr(pr.wf, "load_template", lambda _n: ({}, "digest"))
        monkeypatch.setattr(pr.wf, "missing_nodes", lambda _t, _i: ["KSampler"])
        client = _FakeClient(stats={}, info={})

        status = await pr._probe_with(client)

        assert status.models == {"comfy": "missing_nodes"}
        assert "KSampler" in status.reason

    async def test_missing_model_files(self, monkeypatch):
        async def _no_ollama():
            return []

        monkeypatch.setattr(pr, "ollama_resident", _no_ollama)
        monkeypatch.setattr(pr.wf, "load_template", lambda _n: ({}, "digest"))
        monkeypatch.setattr(pr.wf, "missing_nodes", lambda _t, _i: [])
        monkeypatch.setattr(pr.wf, "check_models", lambda _t, _i: ["z_image_int8.safetensors"])
        client = _FakeClient(stats={}, info={})

        status = await pr._probe_with(client)

        assert status.models == {"comfy": "missing_model"}
        assert "模型文件未就位" in status.reason

    async def test_ready_when_all_checks_pass(self, monkeypatch):
        async def _ollama():
            return [{"name": "qwen3-vl:8b", "size_gib": 2.0}]

        monkeypatch.setattr(pr, "ollama_resident", _ollama)
        monkeypatch.setattr(pr.wf, "load_template", lambda _n: ({}, "digest"))
        monkeypatch.setattr(pr.wf, "missing_nodes", lambda _t, _i: [])
        monkeypatch.setattr(pr.wf, "check_models", lambda _t, _i: [])
        client = _FakeClient(stats={"devices": [], "system": {"comfyui_version": "0.4.2"}}, info={})

        status = await pr._probe_with(client)

        assert status.ready is True
        assert status.reason is None
        assert status.models == {"comfy": "ok"}
        assert status.comfyui["version"] == "0.4.2"
        assert status.ollama_resident == [{"name": "qwen3-vl:8b", "size_gib": 2.0}]

    async def test_ark_spec_only_needs_cloud_config(self, monkeypatch):
        """云端档位不依赖 ComfyUI 权重：ARK 配置齐了就算就绪，缺配置要写明原因。"""
        async def _no_ollama():
            return []

        monkeypatch.setattr(pr, "ollama_resident", _no_ollama)
        monkeypatch.setattr(pr.settings, "ark_enabled", True)
        monkeypatch.setattr(pr.settings, "ark_api_key", "sk-test")
        monkeypatch.setattr(pr, "MODEL_SPECS", {"cloud": {"workflow": "wf.json", "provider": "ark"}})
        client = _FakeClient(stats={}, info={})

        status = await pr._probe_with(client)

        assert status.models == {"cloud": "ok"}
        assert status.ready is True

        # 关掉云端配置：同一档位变 off，且 reason 汇总里点名缺什么
        monkeypatch.setattr(pr.settings, "ark_enabled", False)
        status = await pr._probe_with(_FakeClient(stats={}, info={}))
        assert status.models == {"cloud": "off"}
        assert status.ready is False
        assert "云端出图未启用" in status.reason

    async def test_bad_spec_between_template_and_overrides(self, monkeypatch):
        """档位 files 与模板占位不符（KeyError）：标 bad_spec 而不是抛异常。"""
        async def _no_ollama():
            return []

        monkeypatch.setattr(pr, "ollama_resident", _no_ollama)
        monkeypatch.setattr(pr.wf, "load_template", lambda _n: ({}, "digest"))

        def _bad(_template, _files):
            raise KeyError("unet_name")

        monkeypatch.setattr(pr.wf, "apply_file_overrides", _bad)
        client = _FakeClient(stats={}, info={})

        status = await pr._probe_with(client)

        assert status.models == {"comfy": "bad_spec"}
        assert "档位配置与模板不符" in status.reason


class TestProbeEntry:
    async def test_injected_client_is_not_closed(self):
        client = _FakeClient(stats_error=ComfyUIError("boom", kind="not_running"))

        await pr.probe(client)

        assert client.aclosed is False

    async def test_own_client_is_closed(self, monkeypatch):
        created = _FakeClient(stats_error=ComfyUIError("boom", kind="not_running"))
        monkeypatch.setattr(pr, "ComfyUIClient", lambda *_a, **_k: created)

        status = await pr.probe(None)

        assert status.ready is False
        assert created.aclosed is True
