"""采集进程管理：Chrome/CDP 检测、采集子进程启动/自动续采/取消信号。

子进程完全隔离 Playwright：任务记录落库后由 ``_safe_launch`` 拉起独立
进程执行（``scripts/run_scraper.py``），异常退出时 ``_maybe_auto_retry``
按配置自动续采。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.scraper import ScraperTask

logger = logging.getLogger(__name__)

# 运行中子进程映射
_scraper_pids: dict[int, int] = {}  # task_id → pid
_scraper_retry_count: dict[int, int] = {}  # task_id → 自动续采已重试次数

# Chrome 调试模式启动命令模板（路径从配置读取）
CHROME_DEBUG_CMD = (
    '"{chrome}" '
    "--remote-debugging-port={port} "
    '--user-data-dir="{data_dir}"'
)


def _check_cdp(port: int, timeout: float = 2.0) -> tuple[bool, str, bool]:
    """检测 Chrome 调试端口是否可用。

    Args:
        port: CDP 端口号
        timeout: 连接超时（秒）

    Returns:
        (是否可用, 详情信息, 是否为 Google Chrome)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex(("127.0.0.1", port))
        if result == 0:
            import urllib.request
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/json/version",
                    headers={"User-Agent": "Chrome"},
                )
                with urllib.request.urlopen(req, timeout=1) as resp:
                    data = json.loads(resp.read().decode())
                    browser = data.get("Browser", "Unknown")
                    is_chrome = "Chrome" in browser and "360" not in browser
                    if is_chrome:
                        return True, f"已连接 {browser} (端口 {port})", True
                    else:
                        return True, (
                            f"端口 {port} 上运行的是 {browser}，而非 Google Chrome。"
                            f"CDP 采集必须使用 Google Chrome，请关闭当前浏览器后重新启动 Chrome 调试模式。"
                        ), False
            except Exception:
                return True, f"端口 {port} 可达，但未能确认调试协议", False
        else:
            return False, f"端口 {port} 无响应（请先在命令行中启动调试 Chrome）", False
    except socket.timeout:
        return False, f"端口 {port} 连接超时", False
    except Exception as e:
        return False, f"端口检测异常: {e}", False
    finally:
        sock.close()


def _launch_scraper_process(task_id: int) -> None:
    """启动独立子进程执行采集，完全隔离 Playwright。"""
    script = Path(__file__).parent.parent.parent.parent / "scripts" / "run_scraper.py"

    # 日志输出到文件，方便排查
    logs_dir = Path(__file__).parent.parent.parent.parent / "storage" / "logs" / "scraper"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_f = open(logs_dir / f"task_{task_id}.log", "w", encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, "-u", str(script), str(task_id)],
        stdout=log_f,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    _scraper_pids[task_id] = proc.pid

    def _reap() -> None:
        """后台线程等待子进程退出后回收句柄，异常退出时自动续采。"""
        returncode = proc.wait()
        log_f.close()
        _scraper_pids.pop(task_id, None)

        # 正常退出（0）视为完成，重置续采计数；非 0 视为异常，尝试自动续采
        if returncode == 0:
            _scraper_retry_count.pop(task_id, None)
        else:
            _maybe_auto_retry(task_id)

    threading.Thread(target=_reap, daemon=True).start()


async def _safe_launch(db: AsyncSession, task: ScraperTask) -> None:
    """启动采集子进程；启动失败时把任务置 failed，避免永久停留在 pending。"""
    try:
        _launch_scraper_process(task.id)
    except Exception as e:
        logger.error(f"启动采集子进程失败 task {task.id}: {e}")
        task.status = "failed"
        task.error = f"采集进程启动失败: {e}"
        await db.commit()


def _maybe_auto_retry(task_id: int) -> None:
    """任务子进程异常退出时，若任务仍可续采且未超重试上限，自动重新拉起。"""
    async def _load() -> ScraperTask | None:
        async with async_session() as db:
            return await db.get(ScraperTask, task_id)
    try:
        task = asyncio.run(_load())
    except Exception as e:
        logger.warning(f"自动续采前加载任务失败（放弃本次续采）: {task_id} — {e}")
        return

    # 用户取消的任务不自动重试
    if task is None or task.status == "cancelled":
        return

    retried = _scraper_retry_count.get(task_id, 0)
    if retried >= settings.scraper_task_auto_retry:
        # 续采次数耗尽：把任务标记失败，避免永久停留在 pending/running
        # （此前仅靠服务重启清扫兜底，任务可能长期显示「排队中」）
        try:
            async def _mark_failed() -> None:
                async with async_session() as db:
                    task_row = await db.get(ScraperTask, task_id)
                    if task_row and task_row.status not in ("cancelled", "completed"):
                        task_row.status = "failed"
                        task_row.error = "采集进程异常退出且自动续采次数已用尽"
                        await db.commit()
            asyncio.run(_mark_failed())
        except Exception as e:
            logger.warning(f"标记续采失败任务失败 {task_id}: {e}")
        return

    _scraper_retry_count[task_id] = retried + 1
    logger.warning(
        f"采集任务 {task_id} 异常退出，自动续采（{retried + 1}/{settings.scraper_task_auto_retry}）"
    )
    # 延时片刻，给 ChromeManager 崩溃重启留出时间，避免立刻重连失败
    time.sleep(3)
    # 期间若已被其它入口（如手动续采）重新拉起，则不再重复启动
    if task_id in _scraper_pids:
        return
    try:
        _launch_scraper_process(task_id)
    except Exception as e:
        logger.error(f"自动续采启动失败 task {task_id}: {e}")


async def has_active_scraper_tasks() -> bool:
    """是否存在进行中的采集任务（running/pending，跨进程权威状态）。

    供 ChromeManager 空闲判定使用：仅看进程内 ``_scraper_pids`` 会在多 worker
    或 API 进程重启后误判「无活动采集」而提前关闭 Chrome，打断正在进行的采集。
    采集子进程会同步写库更新任务状态，DB 是权威来源。
    """
    async with async_session() as db:
        result = await db.execute(
            select(func.count(ScraperTask.id)).where(
                ScraperTask.status.in_(["running", "pending"])
            )
        )
        return (result.scalar() or 0) > 0


async def check_cdp(port: int) -> dict:
    """检查指定端口的 Chrome 调试连接是否就绪。
    端口探测是阻塞 socket 操作（最长约 3 秒），放入线程池避免卡住事件循环。
    """
    ok, detail, is_chrome = await asyncio.to_thread(_check_cdp, port)
    return {
        "available": ok,
        "is_google_chrome": is_chrome,
        "detail": detail,
        "startup_command": CHROME_DEBUG_CMD.format(
            chrome=settings.chrome_executable,
            port=port,
            data_dir=settings.chrome_user_data_dir,
        ),
    }
