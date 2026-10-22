"""显存守门：出图前确认 GPU 有余量，不够就**排队等待**而不是直接失败。

为什么是「等待」而不是「自动卸载 Ollama」（决策 3）：

- 自动卸载会打断正在跑的 `quality_check` / `batch_analyze`（它们正用 VLM 打标），
  用户看到的是「打标突然变慢/失败」，而且原因不可见；
- Ollama 的 `keep_alive` 到期后会自己释放显存，等一会儿往往就好了；
- 等待过程写进 `task.result.stage = "waiting_vram"`，前端能显示「在等什么、等了多久」，
  用户想加速可以自己 `ollama stop`。

**判定顺序很关键**（早期版本只看 ``vram_free >= need``，导致第二次出图必然白等）：

1. ``vram_free >= need`` → 直接过（模型没驻留，或显存本来就够）；
2. **Ollama 没有驻留模型** → 直接过：此时 ``vram_free`` 偏低是 ComfyUI 自己缓存着上一个
   模型，它会自行调度（offload/复用），这不是「显存不足」；
3. 否则（Ollama 正占着显存）→ 每 10 秒复查，等它自然过期或用户手动 stop。

这样「连续出图」不会被 ComfyUI 自己的缓存卡住，「打标后再出图」仍会等 Ollama 让位。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from app.services.comfyui.client import ComfyUIClient, ComfyUIError
from app.services.comfyui.probe import ollama_resident

logger = logging.getLogger(__name__)

"""轮询间隔（秒）：显存释放通常在几秒到十几秒内发生，10 秒粒度足够且不吵。"""
CHECK_INTERVAL = 10.0

"""「值得等」的 Ollama 驻留显存门槛（GiB）。

为什么需要：本机实测空闲时 ComfyUI 只有 14.8 GiB 可用（总 15.9，驱动/桌面吃掉约 1 GiB），
而 Z-Image 的保守需求是 15.0 GiB —— 只要 Ollama 驻留了**任何**模型（哪怕 0.03 GiB 的
embedding 模型）都会触发「显存不足」并白等 15 分钟。真实冲突只来自大模型（VLM 5~19 GiB），
所以低于该门槛的驻留一律忽略（ComfyUI 自己能 offload 这点小事）。
"""
OLLAMA_SIGNIFICANT_GIB = 2.0


class VramTimeoutError(ComfyUIError):
    """等到上限仍然没有足够显存。"""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message, kind="vram_timeout", detail=detail)


def _free_gib(stats: dict) -> float:
    """从 /system_stats 取空闲显存（GiB）。

    只取 ``devices[0]``：本机是单卡；多卡场景下这会永远看第一张卡（未做卡间调度），
    如需多卡，这里要改成按档位选卡或用「各卡可用之和」——目前明确不支持。
    """
    devices = stats.get("devices") or []
    dev = devices[0] if devices else {}
    return round(float(dev.get("vram_free") or 0) / 2**30, 2)


async def wait_for_vram(
    client: ComfyUIClient,
    need_gib: float,
    *,
    wait_seconds: int,
    on_wait: Callable[[int, float], Awaitable[None]] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """等到显存可用再返回（判定顺序见模块 docstring）。

    Args:
        client: ComfyUI 客户端（只用到 ``system_stats``）。
        need_gib: 本次所需显存（GiB，保守取实测峰值）。
        wait_seconds: 最长等待秒数（<=0 表示不等待）。
        on_wait: 每次复查后的异步回调 ``on_wait(waited_seconds, free_gib)``，
            调用方用它写 ``task.result.stage``（前端显示等待进度）。
        should_stop: 返回 True 时提前退出（任务被取消）。

    Raises:
        VramTimeoutError: 超时/取消，消息里带「谁占着显存」与建议动作。
    """
    started = time.monotonic()
    deadline = started + max(0, wait_seconds)

    while True:
        if should_stop is not None and should_stop():
            raise VramTimeoutError("已取消（等待显存期间）")
        stats = await client.system_stats()
        free = _free_gib(stats)
        if free >= need_gib:
            return
        residents = await ollama_resident()
        significant = [m for m in residents if float(m.get("size_gib") or 0) >= OLLAMA_SIGNIFICANT_GIB]
        if not significant:
            # 显存低但不是 Ollama 占的 → 是 ComfyUI 自己的模型缓存，它会自行调度
            logger.info(
                f"显存偏低（{free} GiB < {need_gib} GiB）但没有大模型驻留"
                f"（{len(residents)} 个小模型可忽略），按 ComfyUI 自身缓存处理，直接执行"
            )
            return

        waited = int(time.monotonic() - started)
        if on_wait is not None:
            await on_wait(waited, free)
        if time.monotonic() >= deadline:
            who = "；".join(f"{m['name']}（{m['size_gib']} GiB）" for m in significant)
            raise VramTimeoutError(
                f"等待显存超时（已等 {waited} 秒）：当前空闲 {free} GiB，需要 {need_gib} GiB；"
                f"占用显存的是 {who}。可执行 ollama stop <模型名> 释放后重试，"
                "或降低分辨率/张数",
                detail=f"need={need_gib} free={free} waited={waited}",
            )
        logger.info(
            f"Ollama 大模型占着显存（空闲 {free} GiB < 需要 {need_gib} GiB），"
            f"{CHECK_INTERVAL:.0f} 秒后复查"
        )
        await asyncio.sleep(CHECK_INTERVAL)
