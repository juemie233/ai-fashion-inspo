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


# ── i2i：编辑模板与参考图上传 ──


def test_edit_template_injects_single_reference():
    """单参考图：模板直出，images 是嵌套 dict（列表会被 ComfyUI 400 拒）。"""
    template, digest = wf.load_template("qwen_edit")
    assert len(digest) == 16
    workflow = wf.build_edit_workflow(
        template,
        reference_names=["ref_a.png"],
        PROMPT="把上衣换成米白针织开衫",
        NEGATIVE="",
        SEED=7,
        STEPS=25,
        CFG=1.0,
        SAMPLER="euler",
        SCHEDULER="simple",
        RESOLUTION=1024,
        PREFIX="gen_1_01",
    )
    assert workflow["4"]["inputs"]["image"] == "ref_a.png"
    assert workflow["6"]["inputs"]["images"] == {"image_1": ["4", 0]}
    # 图生图的 latent 必须来自条件节点（参考图编码），不是空 latent
    assert workflow["7"]["inputs"]["latent_image"] == ["6", 2]
    assert workflow["7"]["inputs"]["steps"] == 25


def test_edit_template_appends_extra_references():
    """多参考图：第 2 张起动态追加 LoadImage 节点并挂到 images.image_{i}。"""
    template, _ = wf.load_template("qwen_edit")
    workflow = wf.build_edit_workflow(
        template,
        reference_names=["a.png", "b.png", "c.png"],
        PROMPT="拼一套穿搭",
        NEGATIVE="",
        SEED=1,
        STEPS=25,
        CFG=1.0,
        SAMPLER="euler",
        SCHEDULER="simple",
        RESOLUTION=1024,
        PREFIX="p",
    )
    assert workflow["6"]["inputs"]["images"] == {
        "image_1": ["4", 0],
        "image_2": ["21", 0],
        "image_3": ["22", 0],
    }
    assert workflow["21"]["class_type"] == "LoadImage"
    assert workflow["22"]["inputs"]["image"] == "c.png"


def test_edit_reference_count_is_bounded():
    """Qwen 2.1 官方上限 10 张：越界要在构造阶段就拒绝。"""
    template, _ = wf.load_template("qwen_edit")
    kwargs = dict(PROMPT="x", NEGATIVE="", SEED=1, STEPS=25, CFG=1.0,
                  SAMPLER="euler", SCHEDULER="simple", RESOLUTION=1024, PREFIX="p")
    with pytest.raises(ValueError):
        wf.build_edit_workflow(template, reference_names=[], **kwargs)
    with pytest.raises(ValueError):
        wf.build_edit_workflow(template, reference_names=[f"{i}.png" for i in range(11)], **kwargs)


async def test_upload_image_posts_multipart_and_returns_name():
    """参考图必须上传到 ComfyUI 的 input 目录（LoadImage 只认那里）。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["ctype"] = request.headers.get("content-type", "")
        seen["body_has_png"] = b"PNG" in request.content
        return httpx.Response(200, json={"name": "ref_up.png", "subfolder": "", "type": "input"})

    name = await _client(handler).upload_image(b"\x89PNG-bytes", "ref.png")
    assert name == "ref_up.png"
    assert seen["path"] == "/upload/image"
    assert "multipart/form-data" in seen["ctype"]
    assert seen["body_has_png"] is True


# ── 审查修复：探测缓存 / 连接复用 ──


async def test_probe_caches_object_info(monkeypatch):
    """全量 object_info 有数 MB：60 秒内重复 /status 不该重复拉。"""
    from app.services.comfyui import probe as probe_mod

    calls = {"n": 0}

    class _C:
        base_url = "http://cache-test"

        async def system_stats(self) -> dict:
            return {"system": {"comfyui_version": "x"},
                    "devices": [{"vram_total": 16 * 2**30, "vram_free": 15 * 2**30}]}

        async def object_info(self, node: str | None = None) -> dict:
            calls["n"] += 1
            return {
                "UNETLoader": {"input": {"required": {"unet_name": [["z_image_turbo_int8_convrot.safetensors"]]}}},
                "CLIPLoader": {"input": {"required": {"clip_name": [["qwen_3_4b_fp8_mixed.safetensors"]]}}},
                "VAELoader": {"input": {"required": {"vae_name": [["ae.safetensors"]]}}},
                "KSampler": {}, "CLIPTextEncode": {}, "ConditioningZeroOut": {},
                "EmptySD3LatentImage": {}, "ModelSamplingAuraFlow": {},
                "VAEDecode": {}, "SaveImage": {},
            }

        async def aclose(self) -> None:
            return None

    async def _no_resident() -> list[dict]:
        return []

    monkeypatch.setattr(probe_mod, "ollama_resident", _no_resident)
    probe_mod._object_info_cache.clear()

    await probe_mod.probe(_C())
    await probe_mod.probe(_C())
    assert calls["n"] == 1  # 第二次命中缓存


async def test_client_reuses_connection(monkeypatch):
    """同一 client 连续请求应复用同一个 AsyncClient（出图期间轮询几十次）。"""
    created: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"devices": []})

    client = _client(handler)
    await client.system_stats()
    first = client._http
    await client.system_stats()
    assert client._http is first  # 复用
    await client.aclose()
    assert client._http is None
    created.append(first)
