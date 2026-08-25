"""Ollama 运行状态检查与自动启动工具函数。

用于在提交 AI 分析任务前检查 Ollama 是否运行，
如果未运行则自动启动，并返回相应提示信息。
"""

import asyncio
import logging
import os

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


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


async def start_ollama() -> str | None:
    """在 Windows 系统上启动 Ollama 进程（后台启动）。

    通过 PowerShell 启动 ollama serve 后台服务，
    并等待 Ollama API 就绪后返回。

    返回:
        成功启动时返回提示信息（中文），失败返回 None。
    """
    if os.name != "nt":
        return None

    # 检查是否已经在运行
    if await is_ollama_running():
        return "Ollama 已在运行"

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
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    await asyncio.wait_for(
                        client.get(f"{settings.ollama_base_url}/api/version"),
                        timeout=15,
                    )
                    logger.info("Ollama 已成功启动并响应 API")
                    return "Ollama 正在启动中，请稍后重试分析任务"
            except Exception:
                logger.warning("Ollama 进程已启动但 API 尚未就绪")
                return "Ollama 正在启动中，请稍后重试分析任务"
        else:
            logger.warning(f"Ollama 启动失败: {stderr.decode()[:200]}")
            return "无法启动 Ollama，请手动启动后重试"
    except Exception as e:
        logger.error(f"启动 Ollama 失败: {e}")
        return f"启动 Ollama 失败: {e}"
