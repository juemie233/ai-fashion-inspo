"""Ollama 运行状态检查与自动启动工具函数。

用于在提交 AI 分析任务前检查 Ollama 是否运行，
如果未运行则自动启动，并返回相应提示信息。

**关于「端口已被占用但 API 无响应」**：Ollama 在加载模型或被其它 AI 任务
（质量审核的 VLM、人脸匹配）抢占时，``/api/version`` 可能几秒内答不上来，
此时它其实正在运行。若仅凭健康检查失败就再拉起一个 ``ollama serve``，新实例
只会 bind 失败并把错误刷进 ``%LOCALAPPDATA%\\Ollama\\server.log``——本机实测
该文件自 8/11 起累积 3200+ 行，**全部**是
``bind: Only one usage of each socket address``；而抢到端口的那个实例还可能
是孤儿进程（父 PowerShell 已退出），托盘 App 再也管不到它。所以启动前必须先
探端口，端口有人就只等不就绪、不再起第二个。
"""

import asyncio
import contextlib
import logging
import os
from urllib.parse import urlsplit

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _ollama_host_port() -> tuple[str, int]:
    """从 ollama_base_url 解析出 (host, port)，缺省 127.0.0.1:11434。"""
    parsed = urlsplit(settings.ollama_base_url)
    return parsed.hostname or "127.0.0.1", parsed.port or 11434


def _is_windows() -> bool:
    """当前是否 Windows（自动启动走 PowerShell，只有 Windows 支持）。

    单独抽成函数是为了让平台分支可注入：CI 跑在 Linux 上，若用例直接依赖
    os.name，两个「端口守卫」用例会在 Linux 上走到早退分支而失去意义。
    """
    return os.name == "nt"


async def is_ollama_running() -> bool:
    """检查 Ollama 服务是否正在运行。

    同时通过 HTTP 检查 Ollama API 是否可访问，
    以及通过操作系统进程检查确认进程存在。

    返回:
        True 表示 Ollama 可正常响应，False 表示不可用。
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/version")
            if resp.status_code == 200:
                return True
    except Exception:
        pass
    return False


async def is_ollama_port_listening() -> bool:
    """探测 Ollama 端口是否已有进程在监听（比 HTTP 健康检查更权威）。

    为什么需要：HTTP 检查带超时，Ollama 忙时会在几秒内答不上来，仅凭它判定
    「没在运行」就会拉起第二个实例——新实例 bind 失败刷日志，两个实例抢端口还
    让「谁在服务」变得不可预期。1 秒连接超时对本机足够：连不上就是没人监听。
    """
    host, port = _ollama_host_port()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=1
        )
    except Exception:
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def _wait_ollama_ready() -> str:
    """等待 Ollama API 就绪（最多 15 秒），返回给用户的中文提示。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await asyncio.wait_for(
                client.get(f"{settings.ollama_base_url}/api/version"), timeout=15
            )
            logger.info("Ollama 已成功启动并响应 API")
    except Exception:
        logger.warning("Ollama 进程已启动但 API 尚未就绪")
    return "Ollama 正在启动中，请稍后重试分析任务"


async def start_ollama() -> str | None:
    """在 Windows 系统上启动 Ollama 进程（后台启动）。

    通过 PowerShell 启动 ollama serve 后台服务，并等待 Ollama API 就绪后返回。
    端口已有人监听时**不启动新实例**，只等它就绪（见模块 docstring）。
    非 Windows 平台直接返回 None（不尝试启动）。

    返回:
        成功启动时返回提示信息（中文），失败返回 None。
    """
    if not _is_windows():
        return None

    # 检查是否已经在运行
    if await is_ollama_running():
        return "Ollama 已在运行"

    # HTTP 无响应 ≠ 没在运行：端口已有人监听时只等它就绪，绝不再起一个实例
    if await is_ollama_port_listening():
        logger.info("Ollama 端口已被占用（正在加载模型或繁忙），等待其就绪")
        return await _wait_ollama_ready()

    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-Command",
            "Start-Process -NoNewWindow -FilePath ollama -ArgumentList 'serve'; "
            "Start-Sleep -Seconds 1; "
            "if (Get-Process ollama -ErrorAction SilentlyContinue) { 'OK' } else { 'FAIL' }",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        result = stdout.decode().strip()

        if result == "OK":
            # 等待 Ollama API 就绪（最多等 15 秒）
            return await _wait_ollama_ready()
        logger.warning(f"Ollama 启动失败: {stderr.decode()[:200]}")
        return "无法启动 Ollama，请手动启动后重试"
    except Exception as e:
        logger.error(f"启动 Ollama 失败: {e}")
        return f"启动 Ollama 失败: {e}"
