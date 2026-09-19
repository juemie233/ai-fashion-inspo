"""f2 一键获取素材任务（task type: ``f2_import``）：创建与执行（worker 调用）。

链路：调 f2 增量下载各作者新作品 → 扫描下载目录 → 五层去重 → 入库。

两条硬性约定（与 CLI 一致，见 scripts/import_f2_downloads.py 模块 docstring）：
  1. **导入不做标签分析**：不调用 analyze_image、不建向量；素材以未打标状态入库，
     打标交给「批量分析任务」
  2. **必须去重**：内容 SHA-256 / 垃圾桶 / 批次内 / 合成平台 ID / 参数过滤五层判据

执行结构（为什么不直接 await 同步函数）：
  - 下载阶段是子进程（``python -m f2 ...``，逐作者串行），入库阶段是同步 sqlite，
    两者都放到线程里跑，避免阻塞 worker 事件循环——否则任务暂停/取消要等到
    整个导入结束才生效
  - 入库进度由回调写入内存，另起一个 watcher 协程每 2 秒落库一次；watcher 同时
    读任务当前状态，把 cancelled/paused 转成线程内的停止标记（已入库部分保留）
  - 「我的喜欢」的下载同理：f2 单条命令全量翻页期间，watcher 每 2 秒统计 like
    目录里已落盘的文件数与体积（点赞总数要翻到底才知道，进度条只能按耗时给软进度）
"""

import asyncio
import logging
import subprocess
import time
from datetime import datetime
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

"""「我的喜欢」下载阶段的软进度上限（%）。

点赞总数要全量翻页到底才知道，进度条没有真分母；这里给的是**按耗时估算**的软
进度：起步 1%（0% 长时间不动最劝退），随耗时缓慢逼近上限后停住等 f2 收尾，再由
扫描/入库阶段接手。真实证据是任务结果里的 ``like_progress``（已落盘文件数与体积），
它在下载期间持续上涨。
"""
_LIKE_PROGRESS_CAP = 35

"""软进度的时间常数（秒）：耗时达到这个点约走到上限的一半（双曲线，永不到顶）。"""
_LIKE_PROGRESS_HALF_SECONDS = 900


async def create_f2_import_task(
    db: AsyncSession,
    authors: list[str] | None = None,
    limit: int | None = None,
    skip_live: bool = False,
    fetch: bool = True,
    fetch_limit: int | None = None,
    make_thumbnails: bool = True,
    since_days: int | None = None,
    include_unknown_authors: bool = False,
    fetch_mode: str = "post",
    like_user: str | None = None,
    register_bloggers: bool = True,
    like_max_counts: int | None = None,
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
        since_days: f2 日期窗口天数（None 表示执行时取
            ``settings.f2_fetch_since_days``；0 表示翻全历史）。
        include_unknown_authors: 是否连「未登记到博主库」的 f2 账号一起处理。
            默认 False：f2 用户库存的是它见过的所有账号，混进来的无关账号
            （实测出现过网易第五人格这类官方号）不该被下载入库。
        fetch_mode: ``post``（博主主页作品）或 ``like``（我的喜欢）。
        like_user: 「我的喜欢」用的主页链接 / sec_user_id（缺省取
            ``settings.f2_like_user``）。
        register_bloggers: 「我的喜欢」入库后是否把未登记的来源作者补建成抖音博主
            并绑定本批素材（默认 True；只对 like 模式生效）。补建的博主标记为
            「自动登记」，不算已登记博主、不进「一键获取素材」的下载白名单。
        like_max_counts: 「我的喜欢」最多翻多少条（None 表示执行时取
            ``settings.f2_like_max_counts``；0/None 表示全量翻到底）。只对 like 模式生效。

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
            "since_days": since_days,
            "include_unknown_authors": include_unknown_authors,
            "fetch_mode": fetch_mode,
            "like_user": like_user,
            "register_bloggers": register_bloggers,
            "like_max_counts": like_max_counts,
        },
        max_retries=1,  # 下载与入库都幂等，失败可安全重跑
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


# ── 并发保护：同一时间只允许一个进行中的 f2_import 任务 ──

# 进行中的状态集合（排队 / 执行 / 已暂停都算「占用」，不再新建）
_RUNNING_F2_STATUSES = ("pending", "running", "paused")

# 自动调度循环（每 30 秒 tick）与手动 API 都是「先查有无进行中任务、再创建」，
# 两步之间有 await 窗口，理论上可同时通过检查各建一个任务——两个 f2 子进程同时
# 翻页会放大风控风险。调度循环与路由同属后端进程（worker 只执行不创建），
# 一把进程内锁即可把临界区串行化。
_create_lock = asyncio.Lock()


async def _running_f2_task_id(db: AsyncSession) -> int | None:
    """返回进行中的 f2_import 任务 id（无则 None）。"""
    return (
        await db.execute(
            select(TaskQueue.id)
            .where(
                TaskQueue.type == "f2_import",
                TaskQueue.status.in_(_RUNNING_F2_STATUSES),
            )
            .order_by(TaskQueue.id.desc())
            .limit(1)
        )
    ).scalar()


async def create_f2_import_task_if_idle(
    db: AsyncSession, **kwargs
) -> tuple[TaskQueue | None, int | None]:
    """并发安全地创建 f2_import 任务：已有进行中任务时复用它、不新建。

    Args:
        db: 数据库会话。
        **kwargs: 透传给 :func:`create_f2_import_task` 的任务参数。

    Returns:
        (新建任务, None) 或 (None, 复用的进行中任务 id)——两者恰有一个非 None。
    """
    async with _create_lock:
        running_id = await _running_f2_task_id(db)
        if running_id is not None:
            return None, running_id
        task = await create_f2_import_task(db, **kwargs)
        return task, None


def _run_subprocess(cmd: list[str], cwd: Path) -> int:
    """在 f2 工作目录执行 f2（继承标准输出，下载进度实时可见）。"""
    return subprocess.run(cmd, cwd=str(cwd), check=False).returncode


def f2_import_status() -> dict:
    """检查「一键获取素材」是否可用（供 API 与前端按钮状态使用）。

    前提：f2 包已安装、f2 工作目录存在、其用户库里有**能对应到已登记博主**的账号
    （首次全量需手动跑一次 f2，本功能只做增量）。

    ``authors`` 报的是「已登记博主数」而不是 f2 账号总数：f2 用户库存的是它见过
    的所有账号，混进来的无关账号（实测出现过网易第五人格这类官方号，被原样下载
    入库 142 条）默认跳过，另在 ``unknown_authors`` 里列出来提示用户。

    Returns:
        {"available": bool, "reason": str, "authors": int, "unknown_authors": list[str],
         "f2_dir": str, "root": str, "fetch_since_days": int}
        ``fetch_since_days`` 是默认日期窗口天数，供前端「只翻最近 N 天」输入框
        取初值——否则前端会硬编码一个默认值并随请求下发，把 .env 里的配置顶掉。
    """
    from app.config import settings
    from scripts import import_f2_downloads as f2

    like_user = str(settings.f2_like_user or "")
    info = {
        "available": False,
        "reason": "",
        "authors": 0,
        "unknown_authors": [],
        "f2_dir": str(f2.DEFAULT_F2_DIR),
        "root": str(f2.DEFAULT_F2_ROOT),
        "like_root": str(f2.DEFAULT_F2_LIKE_ROOT),
        # 「我的喜欢」需要我自己主页链接（点赞列表只有本人可见）；前端据此回填输入框。
        # 可用性与发布模式分开判定：点赞不依赖「已登记博主」白名单。
        "like_user": like_user,
        "like_available": False,
        "like_reason": "",
        # 「我的喜欢」每次最多翻多少条（0=全量）。前端据此显示并允许改。
        # 为什么需要：f2 的点赞分页没有「遇到已下载就停」，全量翻页每次都要空等
        # 每页一次 timeout（本机 10 秒），且零新增时进度条会停在 0 像卡死。
        "like_max_counts": int(settings.f2_like_max_counts or 0),
        "fetch_since_days": int(settings.f2_fetch_since_days or 0),
    }

    f2_ok = f2.f2_available()
    dir_ok = f2.DEFAULT_F2_DIR.exists()
    info["like_available"] = bool(f2_ok and dir_ok and like_user)
    if info["like_available"]:
        info["like_reason"] = "已配置「我的主页链接」，可采集我的喜欢（点赞作品）"
    elif not f2_ok:
        info["like_reason"] = "未检测到 f2（python -m f2 不可用）：请先安装 f2"
    elif not dir_ok:
        info["like_reason"] = f"未找到 f2 工作目录：{f2.DEFAULT_F2_DIR}"
    else:
        info["like_reason"] = (
            "未配置「我的主页链接」：点赞列表只有本人可见，"
            "请先填写你自己的抖音主页链接或 sec_user_id"
        )

    if not f2_ok:
        info["reason"] = "未检测到 f2（python -m f2 不可用）：请先安装 f2"
        return info
    if not dir_ok:
        info["reason"] = f"未找到 f2 工作目录：{f2.DEFAULT_F2_DIR}"
        return info
    authors = f2.load_f2_authors(f2.DEFAULT_F2_DIR)
    if not authors:
        info["reason"] = (
            f"f2 用户库为空（{f2.DEFAULT_F2_DIR / f2.F2_AUTHOR_DB}）："
            "请先手动跑一次 f2 完成首次下载"
        )
        return info

    known, unknown = authors, []
    bloggers = f2.load_douyin_bloggers()
    if bloggers:
        # 只有库内登记过抖音博主时才有白名单依据；一个都没有则按 f2 账号总数报
        known, unknown = f2.select_known_authors(authors, bloggers)
    names = [a["nickname"] for a in unknown]
    info["unknown_authors"] = names
    info["authors"] = len(known)
    if not known:
        info["reason"] = (
            f"f2 用户库有 {len(authors)} 个账号，但没有一个对应到已登记的抖音博主："
            "请先补全博主的 sec_user_id（脚本 scripts/sync_blogger_ids.py），"
            "或勾选下方「包含未登记账号」"
        )
        return info

    info["available"] = True
    info["reason"] = f"可增量下载 {len(known)} 个已登记博主的新作品"
    if names:
        shown = "、".join(names[:5]) + ("…" if len(names) > 5 else "")
        info["reason"] += f"；另有 {len(names)} 个未登记账号会被跳过（{shown}）"
    return info


async def _current_status(db: AsyncSession, task_id: int) -> str:
    """重新读取任务当前状态（供取消/暂停判定）。"""
    result = await db.execute(select(TaskQueue.status).where(TaskQueue.id == task_id))
    return result.scalar() or "running"


async def _last_f2_task(db: AsyncSession) -> tuple[int | None, datetime | None, int | None]:
    """返回 (最近一条 f2_import 任务的 id, 创建时间, 进行中的任务 id)。

    到期判定以「最近一条任务的创建时间」为锚：手动点过一次之后，当天不会再被
    自动任务重复触发（f2 增量本身也只下新作品，重复触发没有收益）。
    """
    last_row = (
        await db.execute(
            select(TaskQueue.id, TaskQueue.created_at)
            .where(TaskQueue.type == "f2_import")
            .order_by(TaskQueue.id.desc())
            .limit(1)
        )
    ).first()
    running = await _running_f2_task_id(db)
    if last_row is None:
        return None, None, running
    return last_row[0], last_row[1], running


async def maybe_schedule_auto_import(db: AsyncSession) -> int | None:
    """按配置的间隔自动创建「一键获取素材」任务（每日新增作品自动入库）。

    由后端调度循环周期性调用（每 30 秒一次，见 `app/main.py`）。全部条件满足才
    创建，任一不满足即静默跳过（只记日志，不产生噪音任务）：

    1. 配置开启（``settings.f2_import_auto_enabled``，界面可切换并持久化）
    2. 环境可用（f2 已安装 + 工作目录存在 + 作者库非空，见 :func:`f2_import_status`）
    3. 当前没有进行中的 f2_import 任务（避免并发跑两个 f2）
    4. 距最近一次任务创建时间 ≥ ``settings.f2_import_interval_hours``

    Returns:
        新建任务的 id；本轮无需触发时返回 None。
    """
    from datetime import timedelta

    from app.config import settings

    if not settings.f2_import_auto_enabled:
        return None

    _last_id, last_created, running = await _last_f2_task(db)
    if running:
        return None

    interval_hours = max(1, int(settings.f2_import_interval_hours or 24))
    if last_created is not None and (utcnow() - last_created) < timedelta(hours=interval_hours):
        return None

    status = f2_import_status()
    if not status["available"]:
        # 环境没准备好（f2 未装 / 作者库为空）：跳过并留痕，不制造失败任务
        logger.info(f"[f2 自动获取] 跳过本轮：{status['reason']}")
        return None

    task, _reused = await create_f2_import_task_if_idle(
        db,
        fetch=True,
        skip_live=bool(settings.f2_import_auto_skip_live),
        since_days=settings.f2_fetch_since_days,
    )
    if task is None:
        # 锁内复查发现已有进行中任务（手动点击恰好抢先）：本轮静默跳过
        logger.info("[f2 自动获取] 跳过本轮：已有进行中的 f2_import 任务")
        return None
    logger.info(
        f"[f2 自动获取] 已创建任务 #{task.id}"
        f"（间隔 {interval_hours} 小时，作者库 {status['authors']} 个）"
    )
    return task.id


def _running_task_brief(row) -> dict | None:
    """把进行中的任务压成前端展示所需的少量字段（阶段 / 进度 / 计数 / 阶段标记）。

    ``stage`` 是理解 ``done/total`` 的前提：下载阶段是「作者数」，入库阶段是
    「文件数」，界面上要说清楚（见 web 侧 describeRunningTask）。``fetch_mode``
    决定文案说的是「博主主页作品」还是「我的喜欢」；``like_progress`` 只在点赞
    下载阶段有值，给界面提供「已落盘 N 个文件 / X GB」这个真分母缺失时的证据。
    """
    if row is None:
        return None
    task_id, status, progress, done, total, result = row
    stage = ""
    fetch_mode = ""
    like_progress = None
    like_max_counts = 0
    if isinstance(result, dict):
        stage = str(result.get("stage") or "")
        fetch_mode = str(result.get("fetch_mode") or "")
        like_max_counts = int(result.get("like_max_counts") or 0)
        raw_like = result.get("like_progress")
        if isinstance(raw_like, dict):
            like_progress = raw_like
    return {
        "id": task_id,
        "status": status,
        "progress": progress or 0,
        "done": done or 0,
        "total": total or 0,
        "stage": stage,
        "fetch_mode": fetch_mode,
        # 0=全量翻页；>0=增量（前端据此把下载期文案从「全量翻页」改成「最近 N 条」）
        "like_max_counts": like_max_counts,
        "like_progress": like_progress,
    }


async def get_f2_auto_status(db: AsyncSession) -> dict:
    """自动获取的配置与到期信息（供采集管理页卡片展示）。

    Returns:
        {enabled, interval_hours, skip_live, available, reason, authors,
         last_task_at, next_due_at, running_task_id, running}
        ``running`` 为进行中任务的简要信息（无则 None），供卡片显示
        「正在执行 #N · 下载中：第 3/21 个作者」并说明当前阶段。
    """
    from datetime import timedelta

    from app.config import settings

    info = f2_import_status()
    _last_id, last_created, running = await _last_f2_task(db)
    running_row = None
    if running:
        running_row = (
            await db.execute(
                select(
                    TaskQueue.id,
                    TaskQueue.status,
                    TaskQueue.progress,
                    TaskQueue.done,
                    TaskQueue.total,
                    TaskQueue.result,
                ).where(TaskQueue.id == running)
            )
        ).first()
    interval_hours = max(1, int(settings.f2_import_interval_hours or 24))
    return {
        "enabled": bool(settings.f2_import_auto_enabled),
        "interval_hours": interval_hours,
        "skip_live": bool(settings.f2_import_auto_skip_live),
        "available": info["available"],
        "reason": info["reason"],
        "authors": info["authors"],
        "last_task_at": last_created.isoformat() if last_created else None,
        "next_due_at": (
            (last_created + timedelta(hours=interval_hours)).isoformat()
            if last_created
            else None
        ),
        "running_task_id": running,
        "running": _running_task_brief(running_row),
    }


async def _watch_like_download(
    db: AsyncSession,
    task: TaskQueue,
    future: asyncio.Task,
    like_root: Path,
    baseline: dict,
    opts: dict,
) -> tuple[dict, dict]:
    """一边等 f2 拉完「我的喜欢」，一边把已落盘的文件数写进任务结果。

    为什么需要：点赞是单条命令全量翻页，下载期可能十几分钟；此前进度只在 f2
    返回后一次性写 0→40%，界面长时间停在 0%，看不出是在下载还是卡住。

    f2 是同步子进程（在线程里跑），本函数**不中断**它：取消/暂停仍由调用方在它
    返回后判定，与发布模式一致（已下载文件保留，重跑自动跳过）。

    Args:
        db: 数据库会话。
        task: 任务行。
        future: ``asyncio.to_thread(f2.run_fetch_likes, ...)`` 的 future。
        like_root: 「我的喜欢」产物目录（统计对象）。
        baseline: 下载开始前的统计（用来算「本次新增」）。
        opts: 任务参数（写回 result 时带上，避免上次执行的旧字段残留）。

    Returns:
        (``run_fetch_likes`` 的返回值, 结束时的目录统计)。
    """
    from scripts import import_f2_downloads as f2

    started = time.monotonic()
    while True:
        done, _pending = await asyncio.wait({future}, timeout=_WATCH_INTERVAL)
        stats = await asyncio.to_thread(f2.download_tree_stats, like_root)
        elapsed = time.monotonic() - started
        # 起步 1%：进度条长时间停在 0% 是最劝退的观感（哪怕它只是「耗时估算」），
        # 此后随耗时缓慢逼近上限，永不到顶——真进度由入库阶段接手
        task.progress = (
            int(
                (_LIKE_PROGRESS_CAP - 1)
                * elapsed
                / (elapsed + _LIKE_PROGRESS_HALF_SECONDS)
            )
            + 1
        )
        task.result = {
            **opts,
            "stage": "download",
            "like_progress": {
                "files": stats["files"],
                "bytes": stats["bytes"],
                "added": max(0, stats["files"] - baseline["files"]),
                "added_bytes": max(0, stats["bytes"] - baseline["bytes"]),
                "seconds": int(elapsed),
            },
        }
        task.updated_at = utcnow()
        try:
            await db.commit()
        except Exception as exc:  # noqa: BLE001 —— 进度是辅助信息，不能拖垮下载
            await db.rollback()
            logger.warning(f"f2「我的喜欢」进度落库失败（忽略，下一轮重试）：{exc}")
        if done:
            return future.result(), stats


async def execute_f2_import(db: AsyncSession, task: TaskQueue) -> None:
    """执行 f2 一键获取素材（由 worker 调用）。

    执行期间每处理完一个作者 / 每 2 秒检查一次任务状态：被外部置为
    cancelled 则停止并标记 cancelled，置为 paused 则停止并保留 paused
    （已入库的素材与批次清单都保留，重新执行会按内容判重跳过）。
    """
    from app.config import settings
    from scripts import import_f2_downloads as f2

    # 只取创建任务时写入的参数键：暂停/恢复后 task.result 里会残留上一次的
    # stage/fetch/plan/import 字段，整包当 opts 传下去会把旧产物混进新结果
    raw_result = dict(task.result or {})
    opts = {
        key: raw_result[key]
        for key in (
            "authors",
            "limit",
            "skip_live",
            "fetch",
            "fetch_limit",
            "make_thumbnails",
            "since_days",
            "include_unknown_authors",
            "fetch_mode",
            "like_user",
            "register_bloggers",
            "like_max_counts",
        )
        if key in raw_result
    }
    authors = set(opts.get("authors") or []) or None
    fetch_enabled = bool(opts.get("fetch", True))
    limit = opts.get("limit")
    skip_live = bool(opts.get("skip_live", False))
    make_thumbnails = bool(opts.get("make_thumbnails", True))
    include_unknown = bool(opts.get("include_unknown_authors", False))
    fetch_mode = str(opts.get("fetch_mode") or "post")
    # 「我」的主页链接：点赞列表只有本人可见，任务没带就用配置里记住的那个
    like_user = str(opts.get("like_user") or settings.f2_like_user or "").strip()
    like_mode = fetch_mode == "like"
    # 点赞增量条数（0=全量）。收窄的是 f2 的翻页量（`-o`）：点赞分页没有「遇到已下载
    # 就停」，全量每次都要空翻到底；点赞列表最新在前，只翻最近 N 条即可覆盖新增。
    # ⚠ 必须用 `is not None` 判定：显式传 0 表示「本次要全量」，不能被非零的配置项
    # 覆盖掉（用 `or` 会把 0 当成「没传」）。
    _raw_max = opts.get("like_max_counts")
    like_max_counts = max(
        0,
        int(_raw_max)
        if _raw_max is not None
        else int(settings.f2_like_max_counts or 0),
    )
    register_bloggers = bool(opts.get("register_bloggers", True))
    since_days = opts.get("since_days")
    if since_days is None:
        since_days = settings.f2_fetch_since_days

    # 博主清单与 f2 账号清单：下载阶段用来判断「这个 f2 账号是否已登记」，
    # 入库阶段用来绑定作品。默认只处理能对应到已登记博主的账号——f2 用户库存的是
    # 它见过的所有账号，混进来的无关账号（实测出现过网易第五人格，被入库 142 条）
    # 不该收进素材库。库内一个抖音博主都没有时没有白名单依据，退回旧口径（全部处理）。
    #
    # 点赞（喜欢）模式例外：喜欢列表天然跨作者（点的是谁的作品都有），按白名单挡掉
    # 就失去意义，故不做作者过滤——入库仍走五层判重（内容哈希 / 垃圾桶 / 批次内 /
    # 平台 ID / 参数过滤），不会重复。
    f2_dir = f2.DEFAULT_F2_DIR
    bloggers = await asyncio.to_thread(f2.load_douyin_bloggers)
    f2_authors = await asyncio.to_thread(f2.load_f2_authors, f2_dir)
    filter_registered = (not include_unknown) and (not like_mode) and bool(bloggers)
    if filter_registered:
        known_authors, unknown_authors = f2.select_known_authors(f2_authors, bloggers)
    else:
        known_authors, unknown_authors = f2_authors, []
    skipped_unknown = [a["nickname"] for a in unknown_authors]

    # 入库允许的作者范围（归一化名）＝ 博主名集合 ∪ 按 sec_user_id 命中的 f2 昵称
    # （后者覆盖「f2 昵称与库内博主名不一致」的情况）。显式点名作者时同样受白名单
    # 约束——要处理未登记账号请勾选「包含未登记账号」，否则会出现「下载阶段跳过、
    # 入库阶段却放行」的口径分裂。空集合表示「一个都不导入」（不是不过滤）。
    if filter_registered:
        allowed_keys = set(bloggers) | {
            f2.normalize_author(a["nickname"]) for a in known_authors
        }
        plan_authors: set[str] | None = authors & allowed_keys if authors else allowed_keys
    else:
        plan_authors = authors

    task.error = None
    task.progress = 0
    # 阶段标记：done/total 在两个阶段含义不同（下载阶段=作者数，入库阶段=文件数），
    # 前端要靠它解释进度文案（见 web/src/utils/taskPresentation.describeRunningTask）
    task.result = {**opts, "stage": "download" if fetch_enabled else "import"}
    task.updated_at = utcnow()
    await db.commit()

    fetch_summary: dict = {
        "total": 0,
        "ok": 0,
        "failed": 0,
        "aborted": False,
        "mode": fetch_mode,
        "skipped_authors": skipped_unknown,
    }
    if skipped_unknown:
        logger.info(
            f"[f2] 跳过 {len(skipped_unknown)} 个未登记到博主库的账号："
            f"{'、'.join(skipped_unknown)}"
        )

    # ── 阶段 1a：「我的喜欢」——单条命令翻页（不逐作者、不做时间窗口）──
    if fetch_enabled and like_mode:
        if not f2.f2_available():
            raise RuntimeError(
                "未检测到 f2（python -m f2 不可用）：请先安装 f2 并手动完成一次下载"
            )
        if not like_user:
            raise RuntimeError(
                "未配置「我的主页链接」：点赞列表只有本人可见，"
                "请先在「我的喜欢」卡片里填写你的抖音主页链接"
            )
        fetch_summary["total"] = 1
        # 点赞总数要翻到底才知道：下载期不给 done/total（计数看 like_progress 的文件数），
        # 进度条由 watcher 按耗时给软进度。
        # 增量模式（like_max_counts>0）另有作用：翻页量有上界，不会再出现「零新增却
        # 空翻到底、进度条长时间停在 0」。
        task.total = 0
        task.done = 0
        task.result = {**opts, "stage": "download", "like_max_counts": like_max_counts}
        task.updated_at = utcnow()
        await db.commit()

        like_root = f2.DEFAULT_F2_LIKE_ROOT
        baseline = await asyncio.to_thread(f2.download_tree_stats, like_root)
        fetch_task = asyncio.create_task(
            asyncio.to_thread(
                f2.run_fetch_likes,
                f2_dir,
                like_user,
                f2_dir / "Download",
                max_counts=like_max_counts,
            )
        )
        outcome, final_stats = await _watch_like_download(
            db, task, fetch_task, like_root, baseline, opts
        )
        if outcome.get("error"):
            raise RuntimeError(str(outcome["error"]))
        fetch_summary["ok"] = int(outcome.get("ok") or 0)
        fetch_summary["failed"] = int(outcome.get("failed") or 0)
        fetch_summary["like_user"] = f2.like_user_url(like_user)
        fetch_summary["like_max_counts"] = like_max_counts
        fetch_summary["cmd"] = (
            outcome["results"][0].get("cmd", "") if outcome.get("results") else ""
        )
        # 本次下载的真实产出（文件数 / 体积）：入库阶段的 plan 只讲「有多少要入库」，
        # 这个字段回答「下载期到底拉回来多少」——全部被去重挡掉时也看得见
        fetch_summary["downloaded"] = {
            "files": final_stats["files"],
            "bytes": final_stats["bytes"],
            "added": max(0, final_stats["files"] - baseline["files"]),
            "added_bytes": max(0, final_stats["bytes"] - baseline["bytes"]),
        }
        download_result = {
            **opts,
            "stage": "download",
            "fetch": fetch_summary,
            "like_progress": fetch_summary["downloaded"],
        }
        task.done = 1
        task.total = 1
        task.progress = _PROGRESS_AFTER_DOWNLOAD
        task.result = download_result
        task.updated_at = utcnow()
        await db.commit()

        # 取消/暂停：与发布模式一致，立即收尾，不再做扫描与入库
        if await _current_status(db, task.id) not in ("running", "pending"):
            status_now = await _current_status(db, task.id)
            task.result = download_result
            task.status = status_now
            task.updated_at = utcnow()
            await db.commit()
            logger.info(f"f2 拉取「我的喜欢」被中断（{status_now}）：已下载文件保留")
            return
        if fetch_summary["failed"]:
            task.result = download_result
            await db.commit()
            raise RuntimeError(
                "f2 拉取「我的喜欢」失败（退出码非 0），常见原因：cookie 失效或被风控；"
                "请先手动跑一次 f2 确认能下载，且 -u 填的是**你自己**的主页链接"
            )

    # ── 阶段 1b：发布模式——逐作者串行增量下载（子进程放线程）──
    elif fetch_enabled:
        if not f2.f2_available():
            raise RuntimeError(
                "未检测到 f2（python -m f2 不可用）：请先安装 f2 并手动完成一次下载"
            )
        targets = known_authors
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

        # 日期窗口：按作者目录的最近下载时间逐作者计算（见 compute_fetch_interval）。
        # 不给窗口时 f2 会把作者全部历史翻一遍且每页固定 sleep 一次 timeout。
        post_root = f2_dir / f2.F2_DOWNLOAD_SUBDIR
        last_download = await asyncio.to_thread(f2.author_last_download, post_root)

        for index, author in enumerate(targets, 1):
            if await _current_status(db, task.id) not in ("running", "pending"):
                fetch_summary["aborted"] = True
                break
            interval = f2.compute_fetch_interval(
                since_days, last_download.get(f2.normalize_author(author["nickname"]))
            )
            cmd = f2.build_f2_command(
                author, download_root=f2_dir / "Download", interval=interval
            )
            logger.info(f"f2 下载 {author['nickname']}（窗口 {interval}）")
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

        # 用户取消/暂停：立即结束，不再做扫描与入库——扫描是重活（全量约 81 秒），
        # 中断后继续跑纯属浪费，还会让「取消」看起来迟迟不生效。已下载的文件留在
        # f2 目录，恢复/重跑时增量下载与内容判重会自动跳过它们。
        if fetch_summary["aborted"]:
            status_now = await _current_status(db, task.id)
            task.result = {**opts, "stage": "download", "fetch": fetch_summary}
            task.status = status_now
            task.updated_at = utcnow()
            await db.commit()
            logger.info(
                f"f2 下载被中断（{status_now}）：已完成 "
                f"{fetch_summary['ok']}/{fetch_summary['total']} 个作者，已下载文件保留"
            )
            return

        # 全部作者都失败（cookie 失效 / 风控）时不能算成功：用户会从任务中心
        # 看到「成功 0 下载」而不知情。落一次 result 后抛错，让任务显式失败。
        if fetch_summary["total"] and fetch_summary["ok"] == 0:
            task.result = {**opts, "stage": "download", "fetch": fetch_summary}
            await db.commit()
            raise RuntimeError(
                f"f2 下载全部失败（{fetch_summary['failed']}/{fetch_summary['total']} 个作者），"
                "常见原因：cookie 失效或被风控；请先手动跑一次 f2 确认能下载。"
                "若只想入库已下载的文件，请改用「仅入库」模式（fetch=False）"
            )

    # ── 阶段 2：扫描 + 去重计划 ──
    # ⚠ 必须放线程里：scan_directory + build_import_plan 是同步文件/哈希/SQLite 操作
    # （全量重算时实测 7.3 GB 约 81 秒）。同步跑会阻塞 worker 事件循环——心跳
    # （10s 间隔 / 90s stale 阈值）会停跳，运行中的任务可能被 _reset_stale_tasks
    # 判为 stale 并被其它 worker 重新认领（同一批导入被并发执行），期间暂停/取消
    # 也完全失效。哈希缓存（HashCache 落盘）把日常运行降到「只算新增文件」，
    # 但首次/大量新增时仍可能很慢，故线程执行保持不变。
    # 扫描根按模式取：发布模式看 post/，点赞模式看 like/（f2 把喜欢的作品下在
    # 「我的昵称」目录下，原作者在文件名里，见 LIKE_NAMING_TEMPLATE）
    scan_root = f2.DEFAULT_F2_LIKE_ROOT if like_mode else f2.DEFAULT_F2_ROOT
    # 扫描 + 去重是重活（大目录分钟级），单独标一个阶段：否则界面在下载结束到入库
    # 开始的这段窗口里还停在「下载中」的文案上，看起来像卡住
    task.result = {**task.result, "stage": "scan"}
    task.updated_at = utcnow()
    await db.commit()
    files = await asyncio.to_thread(f2.scan_directory, scan_root)
    dedup = await asyncio.to_thread(f2.load_dedup_index)
    started = time.monotonic()
    decisions, skipped, deferred_works, cache_stats = await asyncio.to_thread(
        f2.build_plan_with_cache,
        files,
        dedup,
        hash_cache_path=None,  # 缺省 storage/f2_hash_cache.db
        use_cache=True,
        bloggers=bloggers,
        # 默认只导入已登记博主的产物：扫描的是整个下载目录，里面可能留着
        # 未登记账号（如网易第五人格）的历史文件，不该被顺带收进素材库
        authors=plan_authors,
        limit=limit,
        skip_live=skip_live,
    )
    plan_seconds = round(time.monotonic() - started, 1)
    to_import = [d for d in decisions if d.action == "import"]
    task.progress = _PROGRESS_AFTER_PLAN
    task.total = len(to_import)
    task.done = 0
    task.result = {
        **opts,
        "stage": "import",
        "fetch": fetch_summary,
        "plan": {
            "files": len(to_import),
            "skipped": skipped,
            # 结构化计数：前端展示「已在垃圾桶 N」直接读它，不必再去匹配中文跳过
            # 原因字符串（skipped 的 key 是给人看的文案，改一个字前端就静默失效）
            "trash_skipped": skipped.get(f2.TRASH_SKIP_REASON, 0),
            "deferred_works": deferred_works,
            "seconds": plan_seconds,
            "hash_cache": cache_stats,
            # 带真实作品 ID 的文件数（新命名模板产物）。为 0 说明这批素材入库后
            # 仍点不回抖音原帖——据此判断采集侧模板是否生效。
            "with_aweme_id": sum(1 for d in to_import if d.item.aweme_id),
        },
    }
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"f2 导入计划：待入库 {len(to_import)} 个文件（跳过 {len(skipped)} 类），"
        f"规划耗时 {plan_seconds} 秒，哈希实算 "
        f"{cache_stats.get('computed', '?')} 个文件（复用 {cache_stats.get('hit', '?')} 次）"
    )

    if not to_import:
        task.progress = 100
        task.result = {
            **task.result,
            "stage": "done",
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
        """定时把线程内进度落库，并把外部 cancelled/paused 转成停止标记。

        进度落库是「尽力而为」：watcher 与入库线程写的是同一个 SQLite 文件
        （线程内每文件一次 commit），偶发锁等待超时时单轮失败只记日志、下轮重试。
        否则一次 commit 异常会顺着 ``await watcher`` 炸掉整个执行器——素材其实
        已经入库，任务却被标记失败，是最糟的结果。
        """
        while not finished.is_set():
            try:
                await asyncio.wait_for(finished.wait(), timeout=_WATCH_INTERVAL)
                break
            except TimeoutError:
                pass
            try:
                if await _current_status(db, task.id) not in ("running", "pending"):
                    holder["stop"] = True
                task.done = holder["done"]
                task.total = holder["total"]
                task.progress = _PROGRESS_AFTER_PLAN + int(
                    (100 - _PROGRESS_AFTER_PLAN) * holder["done"] / holder["total"]
                )
                task.updated_at = utcnow()
                await db.commit()
            except Exception as exc:  # noqa: BLE001 —— 进度是辅助信息，不能拖垮导入
                await db.rollback()
                logger.warning(f"f2 导入进度落库失败（忽略，下一轮重试）：{exc}")

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
    interrupted = status_now in ("cancelled", "paused")

    # ── 阶段 3b：「我的喜欢」补登记来源作者博主 ──
    # 点赞列表天然跨作者，入库时大部分素材的作者在博主库里没有记录（实测 585 个
    # 原作者里只有 20 个已登记），素材归属会空着。这一步把未登记的来源作者补建成
    # 抖音博主并绑定本批素材（标记为「自动登记」：算博主但**不进下载白名单**，
    # 避免下次一键获取突然去翻几百个主页）。
    # 位置放在入库之后：清单里才有 inspiration_id 与作者；失败不影响已入库素材。
    bloggers_stats: dict | None = None
    if like_mode and register_bloggers and result.get("batch_file"):
        task.result = {**task.result, "stage": "blogger"}
        task.updated_at = utcnow()
        await db.commit()
        try:
            entries = (await asyncio.to_thread(f2.load_batch_manifest, result["batch_file"]))[
                "imported"
            ]
            from app.services.scraper.f2_bloggers import register_batch_bloggers

            bloggers_stats = await register_batch_bloggers(db, entries)
            logger.info(
                f"f2「我的喜欢」博主登记：新建 {bloggers_stats['created']} 个、"
                f"复用 {bloggers_stats['reused']} 个、绑定素材 {bloggers_stats['linked']} 条"
                f"（同名多候选跳过 {bloggers_stats['ambiguous']} 个）"
            )
        except Exception as exc:  # noqa: BLE001 —— 素材已入库，登记失败不该让任务失败
            await db.rollback()
            bloggers_stats = None
            task.error = f"来源作者博主自动登记失败（素材已入库，可在结果面板手工重试）：{exc}"
            logger.warning(f"f2 自动登记博主失败：{exc}")

    task.result = {
        **task.result,
        "stage": "done",
        "import": {key: value for key, value in result.items() if key != "ids"},
    }
    if bloggers_stats is not None:
        task.result["bloggers"] = bloggers_stats
    # 被中断的任务进度停在当前值：写 100% 会让「已取消/已暂停」看起来像跑完了
    if not interrupted:
        task.progress = 100
    task.done = holder["done"]
    task.total = holder["total"]
    task.updated_at = utcnow()
    if result.get("batch_error"):
        # 清单缺失 = 本批无法回滚，写进任务 error 让用户一眼看到（任务仍算成功，
        # 因为素材确实已入库）
        task.error = f"批次清单写入失败（本批无法回滚）：{result['batch_error']}"
    if interrupted:
        # 尊重外部状态：worker 见 status != running 不会覆盖为 success
        task.status = status_now
        logger.info(
            f"f2 导入被中断（{status_now}）：已入库 {result['imported']}，"
            f"批次清单 {result['batch_file']}"
        )
    await db.commit()
    logger.info(f"f2 导入完成：入库 {result['imported']}，失败 {result['failed']}")
