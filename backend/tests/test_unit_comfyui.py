"""ComfyUI 客户端与工作流的单元测试（不依赖真实 ComfyUI/GPU）。

这一层的价值全在**错误分类**：出图失败时用户能不能看懂、能不能照做。
所以每个分支都造一次真实 HTTP 响应（``httpx.MockTransport``），而不是 mock 掉方法——
mock 掉方法就测不到「400 的 body 长什么样会被误判成什么」。
"""

import json

import httpx
import pytest

from app.services.comfyui import workflow as wf
from app.services.comfyui.client import (
    ComfyUIClient,
    ComfyUIError,
    ComfyUINotRunningError,
    ComfyUIWorkflowError,
)


def _client(handler) -> ComfyUIClient:
    return ComfyUIClient("http://127.0.0.1:8188", timeout=1, poll_interval=0.01,
                         transport=httpx.MockTransport(handler))


# ── 连接层 ──


async def test_not_running_maps_to_actionable_error():
    """连不上 ComfyUI → kind=not_running（前端文案是「请先双击 bat」，不是 500）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ComfyUINotRunningError) as exc:
        await _client(handler).system_stats()
    assert "未启动" in str(exc.value)


async def test_system_stats_parses_vram():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "system": {"comfyui_version": "0.42.1"},
                "devices": [{"name": "cuda:0 RTX 5060 Ti", "vram_total": 17095200000, "vram_free": 8000000000}],
            },
        )

    stats = await _client(handler).system_stats()
    assert stats["devices"][0]["vram_total"] > 0


# ── 提交层 ──


async def test_submit_400_is_workflow_error_not_generic():
    """400 一定意味着工作流/模型不匹配：要单独分类，别让用户以为是网络问题。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"type": "prompt_outputs_failed_validation"}})

    with pytest.raises(ComfyUIWorkflowError) as exc:
        await _client(handler).submit({"1": {"class_type": "X"}})
    assert exc.value.kind == "invalid"


async def test_submit_returns_prompt_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/prompt"
        return httpx.Response(200, json={"prompt_id": "abc-123"})

    assert await _client(handler).submit({"1": {}}) == "abc-123"


# ── 轮询层 ──


async def test_oom_in_history_is_classified_as_oom():
    """显存不足必须单独分类：文案要引导「降分辨率/停 Ollama」，而不是泛泛的失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "p1": {
                    "status": {
                        "status_str": "error",
                        "messages": ["... Allocation on device ... CUDA out of memory ..."],
                    },
                    "outputs": {},
                }
            },
        )

    with pytest.raises(ComfyUIError) as exc:
        await _client(handler).wait_for_prompt("p1", timeout=5)
    assert exc.value.kind == "oom"


async def test_wait_timeout_interrupts(monkeypatch):
    """超时要主动 interrupt（否则 GPU 会被一张图占住直到自己算完）。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/interrupt":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})  # /history 永远空 = 一直在跑

    with pytest.raises(ComfyUIError) as exc:
        await _client(handler).wait_for_prompt("p1", timeout=0.05)
    assert exc.value.kind == "timeout"
    assert "/interrupt" in calls


async def test_on_tick_cancel_interrupts():
    """取消（on_tick 返回 True）→ interrupt + 抛取消错误。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/interrupt":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    async def _cancel() -> bool:
        return True

    with pytest.raises(ComfyUIError) as exc:
        await _client(handler).wait_for_prompt("p1", timeout=5, on_tick=_cancel, tick_interval=0)
    assert "取消" in str(exc.value)


async def test_fetch_image_returns_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["filename"] == "x.png"
        return httpx.Response(200, content=b"\x89PNG-bytes")

    assert await _client(handler).fetch_image("x.png") == b"\x89PNG-bytes"


# ── 工作流模板 ──


def test_template_loads_with_stable_hash():
    """指纹稳定：同内容必须得到同一个 hash（写进 generation_runs 用于追溯）。"""
    t1, h1 = wf.load_template("z_image_t2i")
    _t2, h2 = wf.load_template("z_image_t2i")
    assert h1 == h2 and len(h1) == 16
    assert "KSampler" in wf.required_nodes(t1)


def test_inject_preserves_types_and_embedded_placeholders():
    """整串占位符保留类型（seed 必须是 int）；嵌在字符串里的要能展开。"""
    template, _ = wf.load_template("z_image_t2i")
    out = wf.inject(
        template,
        PROMPT="米色针织开衫",
        SEED=123456,
        WIDTH=1024,
        HEIGHT=1024,
        STEPS=8,
        CFG=1.0,
        SAMPLER="res_multistep",
        SCHEDULER="simple",
        SHIFT=3.0,
        PREFIX="gen___SEED__",
    )
    assert out["8"]["inputs"]["seed"] == 123456
    assert isinstance(out["8"]["inputs"]["seed"], int)
    assert out["10"]["inputs"]["filename_prefix"] == "gen_123456"
    assert out["4"]["inputs"]["text"] == "米色针织开衫"
    # 原模板不被修改（inject 内部 deepcopy）
    assert template["4"]["inputs"]["text"] == "__PROMPT__"


def test_inject_raises_on_missing_param():
    """漏参数必须在提交前炸：否则 ComfyUI 会拿 "__SEED__" 当字符串 seed 拒绝。"""
    template, _ = wf.load_template("z_image_t2i")
    with pytest.raises(KeyError):
        wf.inject(template, PROMPT="x", SEED=1)


def test_missing_nodes_detects_version_drift():
    """ComfyUI 升级改名 → 自检要能指出缺哪个节点。"""
    template, _ = wf.load_template("z_image_t2i")
    object_info = {"KSampler": {}, "UNETLoader": {}}  # 故意缺一堆
    missing = wf.missing_nodes(template, object_info)
    assert "CLIPLoader" in missing and "VAEDecode" in missing


def test_check_models_reports_files_not_in_dropdown():
    """模型文件放错目录 → 下拉里没有 → 必须报出来（这是最常见的「装好了但不能用」）。"""
    template, _ = wf.load_template("z_image_t2i")
    object_info = {
        "UNETLoader": {"input": {"required": {"unet_name": [["other.safetensors"]]}}},
        "CLIPLoader": {"input": {"required": {"clip_name": [["qwen_3_4b_fp8_mixed.safetensors"]]}}},
        "VAELoader": {"input": {"required": {"vae_name": [["ae.safetensors"]]}}},
    }
    missing = wf.check_models(template, object_info)
    assert len(missing) == 1
    assert "z_image_turbo_int8_convrot" in missing[0]


def test_model_files_in_groups_by_node():
    template, _ = wf.load_template("z_image_t2i")
    grouped = wf.model_files_in(template)
    assert grouped["UNETLoader"] == ["z_image_turbo_int8_convrot.safetensors"]
    assert json.dumps(grouped)  # 可序列化（写日志/接口返回）
