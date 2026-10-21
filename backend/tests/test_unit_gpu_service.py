"""GPU 显存监控服务的测试。

这个模块只做「聚合两个数据源 + 算百分比」，但它跑在每个 AI 页面的轮询里，
且两个数据源（Ollama /api/ps、nvidia-smi）都可能在用户的机器上缺失——
所以测试重点不是算数，而是**各种缺失/异常组合下仍返回可用结构**。
"""

import asyncio

import pytest

from app.services import gpu_service


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """httpx.AsyncClient 的最小替身（只需 get + 异步上下文）。"""

    def __init__(self, response=None, raises: Exception | None = None):
        self._response = response
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url: str):
        if self._raises:
            raise self._raises
        return self._response


def _patch_ollama(monkeypatch, *, response=None, raises=None):
    monkeypatch.setattr(
        gpu_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(response=response, raises=raises),
    )


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0, hang: bool = False):
        self._stdout = stdout
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(10)  # 让 wait_for 超时
        return self._stdout, b""

    def kill(self):
        self.killed = True

    async def wait(self):
        return 0


def _patch_smi(monkeypatch, *, proc=None, raises=None):
    async def _fake_exec(*args, **kwargs):
        if raises:
            raise raises
        return proc

    monkeypatch.setattr(gpu_service.asyncio, "create_subprocess_exec", _fake_exec)


async def test_nvidia_smi_wins_and_percent_computed(monkeypatch):
    """nvidia-smi 有数据时以它为准（物理 GPU 比 Ollama 统计更准）。"""
    _patch_ollama(
        monkeypatch,
        response=_FakeResponse(200, {"models": [{"name": "qwen3-vl:8b", "size_vram": 6 * 1024**3}]}),
    )
    _patch_smi(monkeypatch, proc=_FakeProc(b"NVIDIA GeForce RTX 5060 Ti, 16311, 8000, 8311\n"))

    stats = await gpu_service.collect_gpu_stats()

    assert stats["gpu_available"] is True
    assert stats["gpu_name"] == "NVIDIA GeForce RTX 5060 Ti"
    assert stats["total_vram_mb"] == 16311
    assert stats["used_vram_mb"] == 8000  # 被 smi 覆盖，而不是 Ollama 的 6144
    assert stats["usage_percent"] == round(8000 / 16311 * 100, 1)
    assert stats["loaded_models"][0]["name"] == "qwen3-vl:8b"


async def test_ollama_only_reports_minus_one_percent(monkeypatch):
    """没有 nvidia-smi 但有 Ollama 占用 → 有 GPU、百分比给 -1（不知道总量）。"""
    _patch_ollama(
        monkeypatch,
        response=_FakeResponse(200, {"models": [{"name": "m", "size_vram": 2 * 1024**3}]}),
    )
    _patch_smi(monkeypatch, raises=FileNotFoundError())

    stats = await gpu_service.collect_gpu_stats()

    assert stats["gpu_available"] is True
    assert stats["used_vram_mb"] == 2048.0
    assert stats["total_vram_mb"] == 0
    assert stats["usage_percent"] == -1


async def test_ollama_entry_without_name_falls_back(monkeypatch):
    """条目缺 name/model 时用 unknown，而不是整批 KeyError 丢掉。"""
    _patch_ollama(
        monkeypatch,
        response=_FakeResponse(200, {"models": [{"size_vram": 1024**3}, {"model": "x", "size_vram": 1024**3}]}),
    )
    _patch_smi(monkeypatch, raises=FileNotFoundError())

    stats = await gpu_service.collect_gpu_stats()

    assert [m["name"] for m in stats["loaded_models"]] == ["unknown", "x"]
    assert stats["used_vram_mb"] == 2048.0  # 先累加字节再换算，避免逐条 round 误差


async def test_ollama_non_200_is_silently_ignored(monkeypatch):
    """Ollama 返回 500 不影响整体（AI 页面不能因为 /api/ps 挂了就白屏）。"""
    _patch_ollama(monkeypatch, response=_FakeResponse(500, {}))
    _patch_smi(monkeypatch, proc=_FakeProc(b"GPU, 1000, 500, 500\n"))

    stats = await gpu_service.collect_gpu_stats()

    assert stats["loaded_models"] == []
    assert stats["gpu_available"] is True  # smi 仍然给了数据


async def test_ollama_exception_is_swallowed(monkeypatch):
    _patch_ollama(monkeypatch, raises=RuntimeError("连接被拒"))
    _patch_smi(monkeypatch, raises=FileNotFoundError())

    stats = await gpu_service.collect_gpu_stats()

    assert stats == {
        "gpu_available": False,
        "gpu_name": "",
        "total_vram_mb": 0,
        "used_vram_mb": 0,
        "free_vram_mb": 0,
        "usage_percent": 0,
        "loaded_models": [],
    }


async def test_nvidia_smi_short_output_is_ignored(monkeypatch):
    """输出字段不足 4 段（旧驱动/异常格式）时不要写出半截数据。"""
    _patch_ollama(monkeypatch, response=_FakeResponse(200, {}))
    _patch_smi(monkeypatch, proc=_FakeProc(b"GPU, 1000\n"))

    stats = await gpu_service.collect_gpu_stats()

    assert stats["gpu_available"] is False
    assert stats["total_vram_mb"] == 0


async def test_nvidia_smi_non_zero_exit_is_ignored(monkeypatch):
    _patch_ollama(monkeypatch, response=_FakeResponse(200, {}))
    _patch_smi(monkeypatch, proc=_FakeProc(b"garbage, 1, 2, 3\n", returncode=9))

    stats = await gpu_service.collect_gpu_stats()

    assert stats["gpu_available"] is False


async def test_nvidia_smi_timeout_kills_process(monkeypatch):
    """超时要 kill 掉子进程再抛，避免僵尸进程堆积（5 秒一次轮询很容易踩到）。"""
    proc = _FakeProc(b"", hang=True)
    _patch_ollama(monkeypatch, response=_FakeResponse(200, {}))
    _patch_smi(monkeypatch, proc=proc)

    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr(gpu_service.asyncio, "wait_for", _fast_wait_for)

    stats = await gpu_service.collect_gpu_stats()

    assert proc.killed is True
    assert stats["gpu_available"] is False


async def test_partial_failure_keeps_other_source(monkeypatch):
    """两个源一个成功一个失败 → 成功的那个照常写进结果（并行探测互不影响）。"""
    _patch_ollama(monkeypatch, raises=RuntimeError("boom"))
    _patch_smi(monkeypatch, proc=_FakeProc(b"RTX, 16000, 4000, 12000\n"))

    stats = await gpu_service.collect_gpu_stats()

    assert stats["gpu_available"] is True
    assert stats["free_vram_mb"] == 12000
    assert stats["usage_percent"] == 25.0


@pytest.mark.parametrize("used,total,expected", [(0, 0, 0), (0, 100, 0.0), (50, 100, 50.0)])
async def test_usage_percent_boundaries(monkeypatch, used, total, expected):
    _patch_ollama(monkeypatch, response=_FakeResponse(200, {}))
    _patch_smi(
        monkeypatch, proc=_FakeProc(f"GPU, {total}, {used}, 0\n".encode() if total else b"")
    )

    stats = await gpu_service.collect_gpu_stats()

    assert stats["usage_percent"] == expected
