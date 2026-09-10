"""f2 一键获取素材任务（task type: ``f2_import``）：创建与执行（worker 调用）。

链路：调 f2 增量下载各作者新作品 → 扫描下载目录 → 四层去重 → 入库。

两条硬性约定（与 CLI 一致，见 scripts/import_f2_downloads.py 模块 docstring）：
  1. **导入不做标签分析**：不调用 analyze_image、不建向量；素材以未打标状态入库，
     打标交给「批量分析任务」
  2. **必须去重**：内容 SHA-256 / 批次内 / 合成平台 ID / 参数过滤四层判据

执行结构（为什么不直接 await 同步函数）：
  - 下载阶段是子进程（``python -m f2 ...``，逐作者串行），入库阶段是同步 sqlite，
    两者都放到线程里跑，避免阻塞 worker 事件循环——否则任务暂停/取消要等到
    整个导入结束才生效
  - 入库进度由回调写入内存，另起一个 watcher 协程每 2 秒落库一次；watcher 同时
    读任务当前状态，把 cancelled/paused 转成线程内的停止标记（已入库部分保留）
"""

import asyncio
import logging
import subprocess
from pathlib import Path

from sqlalchemy import select
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


async def create_f2_import_task(
    db: AsyncSession,
    authors: list[str] | None = None,
    limit: int | None = None,
    skip_live: bool = False,
    fetch: bool = True,
    fetch_limit: int | None = None,
    make_thumbnails: bool = True,
) -> TaskQueue:
    """创建「f2 一键获取素材」任务记录，返回任务对象。

    Args:
        db: 数据库会话。
        authors: 只处理这些作者（归一化名，空表示全部）。
        limit: 最多导入多少个作品。
        skip_live: 跳过 live 实况的分段视频。
        fetch: 是否先调 f2 增量下载（False 则只入库已下载的文件）。
        fetch_limit: 下载阶段最多处理多少个作者（试跑用）。
        make_thumbnails: 是否生成缩略图。

    Returns:
        新建的任务对象（total/done 由执行阶段填充）。
    """
    task = TaskQueue(
        type="f2_import",
        status="pending",
        progress=0,
        total=0,
        done=0,
        result={
            "authors": list(authors or []),
            "limit": limit,
            "skip_live": skip_live,
            "fetch": fetch,
            "fetch_limit": fetch_limit,
            "make_thumbnails": make_thumbnails,
        },
        max_retries=1,  # 下载与入库都幂等，失败可安全重跑
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


def _run_subprocess(cmd: list[str], cwd: Path) -> int:
    """在 f2 工作目录执行 f2（继承标准输出，下载进度实时可见）。"""
    return subprocess.run(cmd, cwd=str(cwd), check=False).returncode


def f2_import_status() -> dict:
    """检查「一键获取素材」是否可用（供 API 与前端按钮状态使用）。

    三个前提：f2 包已安装、f2 工作目录存在、其用户库里有作者（首次全量需手动跑
    一次 f2，本功能只做增量）。

    Returns:
        {"available": bool, "reason": str, "authors": int, "f2_dir": str, "root": str}
    """
    from scripts import import_f2_downloads as f2

    info = {
        "available": False,
        "reason": "",
        "authors": 0,
        "f2_dir": str(f2.DEFAULT_F2_DIR),
        "root": str(f2.DEFAULT_F2_ROOT),
    }
    if not f2.f2_available():
        info["reason"] = "未检测到 f2（python -m f2 不可用）：请先安装 f2"
        return info
    if not f2.DEFAULT_F2_DIR.exists():
        info["reason"] = f"未找到 f2 工作目录：{f2.DEFAULT_F2_DIR}"
        return info
    authors = f2.load_f2_authors(f2.DEFAULT_F2_DIR)
    info["authors"] = len(authors)
    if not authors:
        info["reason"] = (
            f"f2 用户库为空（{f2.DEFAULT_F2_DIR / f2.F2_AUTHOR_DB}）："
            "请先手动跑一次 f2 完成首次下载"
        )
        return info
    info["available"] = True
    info["reason"] = f"可增量下载 {len(authors)} 个已采集作者的新作品"
    return info


async def _current_status(db: AsyncSession, task_id: int) -> str:
    """重新读取任务当前状态（供取消/暂停判定）。"""
    result = await db.execute(select(TaskQueue.status).where(TaskQueue.id == task_id))
    return result.scalar() or "running"


async def execute_f2_import(db: AsyncSession, task: TaskQueue) -> None:
    """执行 f2 一键获取素材（由 worker 调用）。

    执行期间每处理完一个作者 / 每 2 秒检查一次任务状态：被外部置为
    cancelled 则停止并标记 cancelled，置为 paused 则停止并保留 paused
    （已入库的素材与批次清单都保留，重新执行会按内容判重跳过）。
    """
    from scripts import import_f2_downloads as f2

    opts = dict(task.result or {})
    authors = set(opts.get("authors") or []) or None
    fetch_enabled = bool(opts.get("fetch", True))
    limit = opts.get("limit")
    skip_live = bool(opts.get("skip_live", False))
    make_thumbnails = bool(opts.get("make_thumbnails", True))

    task.error = None
    task.progress = 0
    task.updated_at = utcnow()
    await db.commit()

    fetch_summary = {"total": 0, "ok": 0, "failed": 0, "aborted": False}

    # ── 阶段 1：调 f2 增量下载（逐作者串行；子进程放线程）──
    if fetch_enabled:
        if not f2.f2_available():
            raise RuntimeError(
                "未检测到 f2（python -m f2 不可用）：请先安装 f2 并手动完成一次下载"
            )
        f2_dir = f2.DEFAULT_F2_DIR
        targets = f2.load_f2_authors(f2_dir)
        wanted = {f2.normalize_author(a) for a in authors} if authors else None
        if wanted is not None:
            targets = [
                a for a in targets if f2.normalize_author(a["nickname"]) in wanted
            ]
        if opts.get("fetch_limit"):
            targets = targets[: int(opts["fetch_limit"])]

        fetch_summary["total"] = len(targets)
        task.total = len(targets)
        task.done = 0
        await db.commit()

        for index, author in enumerate(targets, 1):
            if await _current_status(db, task.id) not in ("running", "pending"):
                fetch_summary["aborted"] = True
                break
            cmd = f2.build_f2_command(author, download_root=f2_dir / "Download")
            try:
                rc = await asyncio.to_thread(_run_subprocess, cmd, f2_dir)
            except Exception as exc:  # noqa: BLE001 —— 单作者失败不阻断整批
                rc = -1
                logger.warning(f"f2 调用异常（{author['nickname']}）：{exc}")
            if rc == 0:
                fetch_summary["ok"] += 1
            else:
                fetch_summary["failed"] += 1
                logger.warning(
                    f"f2 下载失败（{author['nickname']}）退出码 {rc}，跳过该作者"
                )
            task.done = index
            task.progress = int(_PROGRESS_AFTER_DOWNLOAD * index / max(1, len(targets)))
            task.updated_at = utcnow()
            await db.commit()

        # 全部作者都失败（cookie 失效 / 风控）时不能算成功：用户会从任务中心
        # 看到「成功 0 下载」而不知情。落一次 result 后抛错，让任务显式失败。
        if fetch_summary["total"] and fetch_summary["ok"] == 0:
            task.result = {**opts, "fetch": fetch_summary}
            await db.commit()
            raise RuntimeError(
                f"f2 下载全部失败（{fetch_summary['failed']}/{fetch_summary['total']} 个作者），"
                "常见原因：cookie 失效或被风控；请先手动跑一次 f2 确认能下载。"
                "若只想入库已下载的文件，请改用「仅入库」模式（fetch=False）"
            )

    # ── 阶段 2：扫描 + 去重计划 ──
    # ⚠ 必须放线程里：build_import_plan 要对整个下载目录做 SHA-256（实测 7.3 GB
    # 约 81 秒）。同步跑会阻塞 worker 事件循环——心跳（10s 间隔 / 90s stale 阈值）
    # 会停跳，运行中的任务可能被 _reset_stale_tasks 判为 stale 并被其它 worker
    # 重新认领（同一批导入被并发执行），期间暂停/取消也完全失效。
    files = await asyncio.to_thread(f2.scan_directory, f2.DEFAULT_F2_ROOT)
    decisions, skipped, deferred_works = await asyncio.to_thread(
        f2.build_import_plan,
        files=files,
        library_hashes=await asyncio.to_thread(f2.load_library_hashes),
        existing_platform_ids=await asyncio.to_thread(f2.load_library_platform_ids),
        bloggers=await asyncio.to_thread(f2.load_douyin_bloggers),
        authors=authors,
        limit=limit,
        skip_live=skip_live,
    )
    to_import = [d for d in decisions if d.action == "import"]
    task.progress = _PROGRESS_AFTER_PLAN
    task.total = len(to_import)
    task.done = 0
    task.result = {
        **opts,
        "fetch": fetch_summary,
        "plan": {
            "files": len(to_import),
            "skipped": skipped,
            "deferred_works": deferred_works,
        },
    }
    task.updated_at = utcnow()
    await db.commit()
    logger.info(f"f2 导入计划：待入库 {len(to_import)} 个文件（跳过 {len(skipped)} 类）")

    if not to_import:
        task.progress = 100
        task.result = {
            **task.result,
            "import": {"imported": 0, "failed": 0, "batch_file": ""},
        }
        task.updated_at = utcnow()
        await db.commit()
        return

    # ── 阶段 3：入库（同步 sqlite 放线程；进度回调 + 状态检查）──
    holder = {"done": 0, "total": len(to_import), "stop": False}
    finished = asyncio.Event()

    def _on_progress(done: int, total: int) -> None:
        holder["done"] = done
        holder["total"] = max(1, total)

    def _should_stop() -> bool:
        return holder["stop"]

    async def _progress_watcher() -> None:
        """定时把线程内进度落库，并把外部 cancelled/paused 转成停止标记。"""
        while not finished.is_set():
            try:
                await asyncio.wait_for(finished.wait(), timeout=_WATCH_INTERVAL)
                break
            except asyncio.TimeoutError:
                pass
            if await _current_status(db, task.id) not in ("running", "pending"):
                holder["stop"] = True
            task.done = holder["done"]
            task.total = holder["total"]
            task.progress = _PROGRESS_AFTER_PLAN + int(
                (100 - _PROGRESS_AFTER_PLAN) * holder["done"] / holder["total"]
            )
            task.updated_at = utcnow()
            await db.commit()

    watcher = asyncio.create_task(_progress_watcher())
    try:
        result = await asyncio.to_thread(
            f2.apply_import,
            to_import,
            make_thumbnails=make_thumbnails,
            on_progress=_on_progress,
            should_stop=_should_stop,
        )
    finally:
        finished.set()
        await watcher

    status_now = await _current_status(db, task.id)
    task.result = {
        **task.result,
        "import": {key: value for key, value in result.items() if key != "ids"},
    }
    task.progress = 100
    task.done = holder["done"]
    task.total = holder["total"]
    task.updated_at = utcnow()
    if result.get("batch_error"):
        # 清单缺失 = 本批无法回滚，写进任务 error 让用户一眼看到（任务仍算成功，
        # 因为素材确实已入库）
        task.error = f"批次清单写入失败（本批无法回滚）：{result['batch_error']}"
    if status_now in ("cancelled", "paused"):
        # 尊重外部状态：worker 见 status != running 不会覆盖为 success
        task.status = status_now
        logger.info(
            f"f2 导入被中断（{status_now}）：已入库 {result['imported']}，"
            f"批次清单 {result['batch_file']}"
        )
    await db.commit()
    logger.info(f"f2 导入完成：入库 {result['imported']}，失败 {result['failed']}")
