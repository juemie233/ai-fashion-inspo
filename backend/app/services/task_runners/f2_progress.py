"""f2 下载阶段的**进度观察**与子进程执行（从 f2_import.py 抽出的第一块）。

这一块是「边下载边报进度」的全部机制：两种下载（我的列表一次命令全量翻页 / 发布模式
逐作者）+ 软进度 + 每 2 秒把已落盘文件数写回任务行。与任务创建、状态查询、自动调度无关，
原本混在 2000 多行的 f2_import.py 里，最难定位。

⚠️ 拆包后测试的 monkeypatch 点变了：``_WATCH_INTERVAL`` / ``_SOFT_PROGRESS_HALF_SECONDS``
在**本模块**被观察器读取，patch 旧模块（f2_import）不会生效且**不报错、只会变慢**，
所以测试必须同时 patch 两个模块（tests/test_f2_import_task.py 已同步）。
``_run_subprocess`` / ``_watch_personal_download`` 由 f2_import 导入后再调用，patch
f2_import 上的同名属性依然生效。
"""

import logging

import asyncio
import subprocess
import time
from pathlib import Path

from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from app.models.task import TaskQueue
from app.services.task_runners.common import utcnow


logger = logging.getLogger(__name__)


"""下载阶段占用的进度区间（0~40%），其余留给扫描与入库。"""
_PROGRESS_AFTER_DOWNLOAD = 40


"""扫描 + 去重计划完成后的进度（入库从 45% 走到 100%）。"""
_PROGRESS_AFTER_PLAN = 45


"""进度落库间隔（秒）：入库在线程内跑，靠这个间隔把进度写回任务行。"""
_WATCH_INTERVAL = 2.0


"""「我的喜欢」下载阶段的软进度上限（%）。

点赞总数要全量翻页到底才知道，进度条没有真分母；这里给的是**按耗时估算**的软
进度：起步 1%（0% 长时间不动最劝退），随耗时缓慢逼近上限后停住等 f2 收尾，再由
扫描/入库阶段接手。真实证据是任务结果里的 ``download_progress``（已落盘文件数与体积），
它在下载期间持续上涨。
"""
_PERSONAL_PROGRESS_CAP = 35


"""软进度的时间常数（秒）：耗时达到这个点约走到上限的一半（双曲线，永不到顶）。

发布模式（逐作者）用同一个常数：作者数才是真分母，但**单个作者**内部没有分母
（f2 一路翻页、每页固定等一次 timeout），所以每位作者区间内也用软进度爬。
"""
_SOFT_PROGRESS_HALF_SECONDS = 900


def _soft_fraction(elapsed: float) -> float:
    """软进度比例：0 → 1，随耗时双曲线逼近但**永不到顶**（纯函数，便于单测）。

    同一作者内用它在「该作者的进度区间」里爬：`elapsed` 秒时约走完一半、
    15 分钟约 0.5、时间再长也不会到 1（剩下的留给「作者完成」这一步）。
    """
    return elapsed / (elapsed + _SOFT_PROGRESS_HALF_SECONDS)


def _run_subprocess(cmd: list[str], cwd: Path) -> int:
    """在 f2 工作目录执行 f2（继承标准输出，下载进度实时可见）。"""
    return subprocess.run(cmd, cwd=str(cwd), check=False).returncode


async def _watch_download(
    db: AsyncSession,
    task: TaskQueue,
    future: asyncio.Task,
    root: Path,
    baseline: dict,
    opts: dict,
    progress_at: Callable[[float], int],
    extra: dict | None = None,
    result_extra: dict | None = None,
) -> tuple[Any, dict]:
    """一边等 f2 下载，一边把「已落盘文件数 + 软进度」持续写进任务行（两种下载共用）。

    为什么通用化（2026-10-01 用户报「下载目录已落两千张图，界面还停在 0%」）：
    这套观察器原先只服务「我的喜欢 / 我的收藏」；发布模式（逐作者）**只在每个作者
    结束后**才写一次进度，于是下载第一个作者期间 `done=0 / progress=0%`，界面上只有
    一句「第 0/1 个作者」——哪怕磁盘上已经落了两千张图，看起来也像卡死。

    现在两种下载共用同一套观察器，差别只在 `progress_at`（百分比口径）：
      - 我的列表：一次命令全量翻页，软进度逼近 ``_PERSONAL_PROGRESS_CAP``；
      - 发布模式：逐作者，软进度在**该作者的进度区间**里爬（作者数才是真分母）。

    f2 是同步子进程（在线程里跑），本函数**不中断**它：取消/暂停仍由调用方在它返回后
    判定（已下载文件保留，重跑自动跳过）。

    Args:
        db: 数据库会话。
        task: 任务行。
        future: ``asyncio.to_thread(...)`` 的 future（f2 调用或 ``_run_subprocess``）。
        root: 统计对象（产物目录；发布模式用 post 根，逐作者以 baseline 差值算新增）。
        baseline: 本次下载开始前的统计（用来算「本次新增」）。
        opts: 任务参数（写回 result 时带上，避免上次执行的旧字段残留）。
        progress_at: ``(elapsed: float) -> int``，本次的进度百分比口径。
        extra: 追加进 ``download_progress`` 的字段（发布模式记「第 i/N 个作者」）。
        result_extra: 追加进任务结果的**顶层**字段（发布模式用来保留 ``fetch`` 摘要）。

    Returns:
        ``(future 的返回值, 结束时的目录统计)``；future 抛异常时原样抛出，由调用方处理。
    """
    from scripts import import_f2_downloads as f2

    started = time.monotonic()
    while True:
        done, _pending = await asyncio.wait({future}, timeout=_WATCH_INTERVAL)
        stats = await asyncio.to_thread(f2.download_tree_stats, root)
        elapsed = time.monotonic() - started
        task.progress = progress_at(elapsed)
        task.result = {
            **opts,
            **(result_extra or {}),
            "stage": "download",
            "download_progress": {
                "files": stats["files"],
                "bytes": stats["bytes"],
                "added": max(0, stats["files"] - baseline["files"]),
                "added_bytes": max(0, stats["bytes"] - baseline["bytes"]),
                "seconds": int(elapsed),
                **(extra or {}),
            },
        }
        task.updated_at = utcnow()
        try:
            await db.commit()
        except Exception as exc:  # noqa: BLE001 —— 进度是辅助信息，不能拖垮下载
            await db.rollback()
            logger.warning(f"f2 进度落库失败（忽略，下一轮重试）：{exc}")
        if done:
            return future.result(), stats


async def _watch_personal_download(
    db: AsyncSession,
    task: TaskQueue,
    future: asyncio.Task,
    personal_root: Path,
    baseline: dict,
    opts: dict,
) -> tuple[dict, dict]:
    """「我的喜欢 / 我的收藏」的观察器：软进度逼近 ``_PERSONAL_PROGRESS_CAP``（实现见上）。"""
    return await _watch_download(
        db,
        task,
        future,
        personal_root,
        baseline,
        opts,
        # 起步 1%：进度条长时间停在 0% 是最劝退的观感（哪怕它只是「耗时估算」），
        # 此后随耗时缓慢逼近上限，永不到顶——真进度由入库阶段接手
        progress_at=lambda elapsed: int(
            (_PERSONAL_PROGRESS_CAP - 1) * _soft_fraction(elapsed)
        )
        + 1,
    )
