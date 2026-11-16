"""AI 路由共享工具：.env 更新、体积格式化等。

2026-11-16（task-9 / R2）起，单图 AI 分析已并入 worker 任务队列：
``routers/ai_analysis.py`` 的 ``analyze_inspiration`` 创建 ``batch_analyze`` 任务
（priority=10、force_retry=True），由独立 worker 执行并受 ``GPU_EXCLUSIVE_TASK_TYPES``
互斥约束。此前的 **API 进程内存分析队列与 ``_run_analysis`` 已删除**，不再保留旁路：
内存队列既不受 task_queue 的审计/重试/看门狗保护，也会与 worker 的重负载任务并发抢 GPU。
"""

import asyncio
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


async def _update_env_file(updates: dict[str, str]) -> None:
    """将键值对更新写入 .env 文件（保留其他配置不变）。"""
    env_path = Path(__file__).parent.parent.parent / ".env"

    def _write() -> None:
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
        else:
            content = ""

        for key, value in updates.items():
            if re.search(rf"^{key}=.*$", content, re.MULTILINE):
                content = re.sub(rf"^{key}=.*$", f"{key}={value}", content, flags=re.MULTILINE)
            else:
                if content and not content.endswith("\n"):
                    content += "\n"
                content += f"{key}={value}\n"

        env_path.write_text(content, encoding="utf-8")

    await asyncio.to_thread(_write)
    logger.info(f"已更新 .env: {list(updates.keys())}")


def _format_size(size_bytes: int) -> str:
    """将字节数转换为可读格式。"""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}"
