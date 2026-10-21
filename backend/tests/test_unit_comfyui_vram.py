"""显存守门的判定逻辑测试（决策 3 的回归保险）。

关键回归：**第二次出图不能被 ComfyUI 自己的模型缓存卡住**。
早期实现只看 `vram_free >= need`，而模型驻留后 free 天然很低 → 任务会白等 15 分钟。
现在的判定是「显存够 → 过；Ollama 没驻留 → 也过（那是 ComfyUI 自己的缓存）；否则才等」。
"""

import pytest

from app.services.comfyui import vram as vram_mod
from app.services.comfyui.vram import VramTimeoutError, wait_for_vram


class _Client:
    """可控的假 ComfyUI：按顺序返回给定的空闲显存值。"""

    def __init__(self, free_gibs: list[float]) -> None:
        self._values = list(free_gibs)
        self.calls = 0

    async def system_stats(self) -> dict:
        self.calls += 1
        value = self._values[min(self.calls - 1, len(self._values) - 1)]
        return {"devices": [{"vram_total": 16 * 2**30, "vram_free": int(value * 2**30)}]}


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """把 10 秒复查间隔压成 0，避免测试真的等。"""
    monkeypatch.setattr(vram_mod, "CHECK_INTERVAL", 0)


async def test_passes_when_free_is_enough(monkeypatch):
    monkeypatch.setattr(vram_mod, "ollama_resident", _no_resident)
    client = _Client([16.0])
    await wait_for_vram(client, 15.0, wait_seconds=60)
    assert client.calls == 1


async def test_passes_when_comfyui_holds_its_own_cache(monkeypatch):
    """回归：free 只有 2 GiB 但 Ollama 没驻留 → 是 ComfyUI 自己的缓存，必须直接过。"""
    monkeypatch.setattr(vram_mod, "ollama_resident", _no_resident)
    client = _Client([2.0])
    await wait_for_vram(client, 15.0, wait_seconds=60)
    assert client.calls == 1


async def test_waits_while_ollama_holds_vram_then_proceeds(monkeypatch):
    """Ollama 先占着、随后释放：应该等一轮后放行（而不是直接失败）。"""
    calls = {"n": 0}

    async def _resident_then_released() -> list[dict]:
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"name": "qwen3-vl:8b-instruct", "size_gib": 5.4}]
        return []

    monkeypatch.setattr(vram_mod, "ollama_resident", _resident_then_released)
    client = _Client([2.0, 16.0])  # 第一次显存不足，第二次已释放
    await wait_for_vram(client, 15.0, wait_seconds=600)
    assert client.calls == 2


async def test_timeout_mentions_the_culprit(monkeypatch):
    """超时错误必须说清「谁占着显存」——否则用户只能瞎猜。"""
    monkeypatch.setattr(
        vram_mod, "ollama_resident", _residents([{"name": "qwen3-vl:8b-instruct", "size_gib": 5.4}])
    )
    client = _Client([2.0])
    with pytest.raises(VramTimeoutError) as exc:
        await wait_for_vram(client, 15.0, wait_seconds=0)
    message = str(exc.value)
    assert "等待显存超时" in message
    assert "qwen3-vl:8b-instruct" in message
    assert "ollama stop" in message


async def test_small_ollama_model_is_ignored(monkeypatch):
    """本机实测：空闲时只有 14.8 GiB 可用（总 15.9），而需求是 15.0 GiB。

    若把 0.03 GiB 的 embedding 模型也算成「占用」，每次出图都会白等 15 分钟 ——
    所以门槛以下的驻留一律忽略，直接执行。
    """
    monkeypatch.setattr(
        vram_mod, "ollama_resident", _residents([{"name": "all-minilm:latest", "size_gib": 0.03}])
    )
    client = _Client([14.8])
    await wait_for_vram(client, 15.0, wait_seconds=600)
    assert client.calls == 1


async def test_stop_signal_cancels(monkeypatch):
    monkeypatch.setattr(vram_mod, "ollama_resident", _no_resident)
    client = _Client([0.5])
    with pytest.raises(VramTimeoutError) as exc:
        await wait_for_vram(client, 15.0, wait_seconds=600, should_stop=lambda: True)
    assert "取消" in str(exc.value)


async def _no_resident() -> list[dict]:
    return []


def _residents(items: list[dict]):
    async def _inner() -> list[dict]:
        return list(items)

    return _inner
