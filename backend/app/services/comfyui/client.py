"""ComfyUI 的 HTTP 客户端：把 6 个接口包成有类型、有超时、有错误分类的 async 方法。

错误分类是这里最重要的产出。出图失败对用户只有一句「生成失败」的话，就没法自助排查；
所以每条失败路径都必须能映射到一句人话（见 :class:`ComfyUIError.kind`）：

============  ==========================================================
kind          含义与文案方向
============  ==========================================================
not_running   ComfyUI 没启动 → 「请先双击 run_nvidia_gpu.bat」
invalid       工作流与当前 ComfyUI/模型不匹配（400 + node_errors）→ 指出节点
oom           显存不足（history 里 Alloc/out of memory）→ 建议降分辨率或停 Ollama
timeout       单张超时 → 已主动 interrupt
http          其它 HTTP 异常（原样带状态码）
============  ==========================================================
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

"""轮询 /history 时判定「任务已结束」的状态集合。"""
_TERMINAL = {"success", "error"}

"""显存不足的关键字（ComfyUI 报 OOM 时消息形态不固定，覆盖常见几种）。"""
_OOM_HINTS = ("allocation on device", "out of memory", "outofmemory", "cuda out of memory")


class ComfyUIError(Exception):
    """ComfyUI 调用失败。

    Attributes:
        kind: not_running | invalid | oom | timeout | http
        detail: 原始错误摘要（写进任务 error 便于排查，不直接展示给普通用户）
    """

    def __init__(self, message: str, *, kind: str = "http", detail: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = detail or message


class ComfyUINotRunningError(ComfyUIError):
    """连不上 ComfyUI（未启动 / 端口不对）。"""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message, kind="not_running", detail=detail)


class ComfyUIWorkflowError(ComfyUIError):
    """工作流被 ComfyUI 拒绝（节点名/参数/模型文件不匹配）。"""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message, kind="invalid", detail=detail)


def _is_oom(text: str) -> bool:
    low = text.lower()
    return any(hint in low for hint in _OOM_HINTS)


class ComfyUIClient:
    """ComfyUI 的最小 HTTP 封装。

    Args:
        base_url: 形如 ``http://127.0.0.1:8188``（只允许本机，见 settings 注释）。
        timeout: 单次请求超时（秒）。提交/查询都是快请求，10 秒足够；
            「生成一张要 6~40 秒」由 :meth:`wait_for_prompt` 的轮询循环承担，不靠长超时。
        poll_interval: 轮询间隔（秒）。
        transport: 仅测试用（注入 ``httpx.MockTransport``）。
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        poll_interval: float = 0.5,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._transport = transport

    # ── 内部：统一的请求入口，把网络异常翻译成 not_running ──
    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout, transport=self._transport
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with await self._client() as client:
                return await client.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ComfyUINotRunningError(
                f"ComfyUI 未启动或端口不通（{self.base_url}）",
                detail=str(exc),
            ) from exc
        except httpx.TimeoutException as exc:
            raise ComfyUIError(
                f"ComfyUI 请求超时（{path}）", kind="timeout", detail=str(exc)
            ) from exc

    # ── 就绪/环境 ──
    async def system_stats(self) -> dict:
        """取 /system_stats（含 vram_total / vram_free）。"""
        resp = await self._request("GET", "/system_stats")
        if resp.status_code != 200:
            raise ComfyUIError(
                f"/system_stats 返回 {resp.status_code}", detail=resp.text[:300]
            )
        return resp.json()

    async def object_info(self, node: str | None = None) -> dict:
        """取节点定义；不带参数时是整个节点表（较大，仅启动自检用一次）。"""
        path = f"/object_info/{node}" if node else "/object_info"
        resp = await self._request("GET", path)
        if resp.status_code != 200:
            raise ComfyUIError(
                f"/object_info 返回 {resp.status_code}", detail=resp.text[:300]
            )
        return resp.json()

    # ── 提交与轮询 ──
    async def submit(self, workflow: dict, client_id: str = "fashion-inspo") -> str:
        """提交 API 格式工作流，返回 prompt_id。"""
        resp = await self._request(
            "POST", "/prompt", json={"prompt": workflow, "client_id": client_id}
        )
        if resp.status_code == 400:
            # 400 一定是工作流问题（节点名、参数类型、模型文件名不在下拉项里）
            body = resp.text[:500]
            raise ComfyUIWorkflowError(
                "工作流与当前 ComfyUI/模型不匹配（节点或模型文件不存在）",
                detail=body,
            )
        if resp.status_code != 200:
            raise ComfyUIError(
                f"提交工作流失败（HTTP {resp.status_code}）", detail=resp.text[:300]
            )
        data = resp.json()
        prompt_id = str(data.get("prompt_id") or "")
        if not prompt_id:
            raise ComfyUIError("ComfyUI 未返回 prompt_id", detail=str(data)[:300])
        return prompt_id

    async def history(self, prompt_id: str) -> dict | None:
        """取单个 prompt 的执行记录；未完成时返回 None。"""
        resp = await self._request("GET", f"/history/{prompt_id}")
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get(prompt_id)

    async def wait_for_prompt(
        self,
        prompt_id: str,
        *,
        timeout: float,
        on_tick: Callable[[], Awaitable[bool]] | None = None,
        tick_interval: float = 2.0,
    ) -> dict:
        """轮询直到该 prompt 结束，返回 history 记录。

        Args:
            timeout: 总超时（秒）。超时会先 :meth:`interrupt` 再抛 kind=timeout。
            on_tick: 每 ``tick_interval`` 秒 await 一次；返回 True 表示外部要求取消
                （任务被取消/暂停）。用它把「任务状态」接进轮询循环，而不是在客户端
                里直接查库 —— 客户端不认识数据库。
            tick_interval: 回调间隔（秒）。别太勤：每次回调都会查一次任务状态。

        Raises:
            ComfyUIError: kind=timeout（超时/取消）或 kind=oom（显存不足）。
        """
        started = time.monotonic()
        last_tick = started
        while True:
            now = time.monotonic()
            if on_tick is not None and now - last_tick >= tick_interval:
                last_tick = now
                if await on_tick():
                    await self.interrupt()
                    raise ComfyUIError(
                        "已取消", kind="timeout", detail="cancelled by user"
                    )
            entry = await self.history(prompt_id)
            if entry:
                status = entry.get("status") or {}
                status_str = str(status.get("status_str") or "")
                if status_str in _TERMINAL:
                    if status_str == "error":
                        detail = str(status.get("messages") or "")[:600]
                        if _is_oom(detail):
                            raise ComfyUIError(
                                "显存不足（生成时被 CUDA 拒绝分配）",
                                kind="oom",
                                detail=detail,
                            )
                        raise ComfyUIError(
                            "生成失败（ComfyUI 执行报错）", kind="invalid", detail=detail
                        )
                    return entry
                # 有记录但还没终态（仍 running）：继续等
            if time.monotonic() - started > timeout:
                await self.interrupt()
                raise ComfyUIError(
                    f"生成超时（{int(timeout)} 秒），已请求中断",
                    kind="timeout",
                    detail=f"prompt_id={prompt_id}",
                )
            await asyncio.sleep(self.poll_interval)

    async def interrupt(self) -> None:
        """中断当前执行（尽力而为：失败不影响调用方的错误处理）。"""
        try:
            await self._request("POST", "/interrupt")
        except ComfyUIError as exc:  # pragma: no cover - 只记日志
            logger.warning(f"请求 ComfyUI 中断失败（忽略）: {exc}")

    async def cancel_queued(self, prompt_id: str) -> None:
        """把排队中/运行中的指定 prompt 从队列里删掉（尽力而为）。"""
        try:
            resp = await self._request("GET", "/queue")
            if resp.status_code != 200:
                return
            data = resp.json()
            for key in ("queue_running", "queue_pending"):
                for item in data.get(key) or []:
                    # ComfyUI 队列项形如 [number, prompt_id, prompt, extra, outputs]
                    if isinstance(item, list) and len(item) > 1 and item[1] == prompt_id:
                        await self._request(
                            "POST", "/queue", json={"delete": [item[0]]}
                        )
                        return
        except ComfyUIError as exc:  # pragma: no cover - 只记日志
            logger.warning(f"清理 ComfyUI 队列失败（忽略）: {exc}")

    async def fetch_image(
        self, filename: str, subfolder: str = "", folder_type: str = "output"
    ) -> bytes:
        """下载生成结果（/view）。"""
        resp = await self._request(
            "GET",
            "/view",
            params={"filename": filename, "subfolder": subfolder, "type": folder_type},
        )
        if resp.status_code != 200:
            raise ComfyUIError(
                f"取回生成图失败（HTTP {resp.status_code}）", detail=filename
            )
        return resp.content
