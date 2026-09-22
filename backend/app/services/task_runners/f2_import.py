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

"""「我的列表」类模式：点赞（我的喜欢）与收藏（我的收藏）。

两者同形——都是 f2 拉**登录账号自己**的列表（只有本人可见，故必须填「我的主页链接」）、
作品全下在「我的昵称」目录下、原作者与作品 ID 只存在于文件名里；因此共用同一套下载阶段、
进度口径（``like_progress``）与扫描/入库逻辑，差别只有 f2 的 ``-M`` 取值、产物根目录
与界面文案（文案由 ``fetch_mode`` 决定，见 web/src/utils/taskPresentation.ts）。
"""
PERSONAL_FETCH_MODES = ("like", "collection")

"""收藏模式入库后聚合到的合集名（收藏合计里据此看到数量与体积）。"""
COLLECT_COLLECTION_NAME = "抖音收藏"


def _personal_scan_root(f2, fetch_mode: str):
    """「我的列表」模式的产物根目录（点赞 / 收藏）。"""
    return (
        f2.DEFAULT_F2_COLLECT_ROOT
        if fetch_mode == "collection"
        else f2.DEFAULT_F2_LIKE_ROOT
    )


def _personal_label(fetch_mode: str) -> str:
    """「我的列表」模式的中文名（日志与错误提示用）。"""
    return "我的收藏" if fetch_mode == "collection" else "我的喜欢"


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
    profiles: list[str] | None = None,
    collect_ids: list[str] | None = None,
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
        fetch_mode: ``post``（博主主页作品）、``like``（我的喜欢）或
            ``collection``（我的收藏）。后两者都是「我的列表」模式：必须填
            「我的主页链接」，产物落在各自的固定目录，入库后不做作者白名单过滤。
        like_user: 「我的喜欢 / 我的收藏」用的主页链接 / sec_user_id（缺省取
            ``settings.f2_like_user``）——两种模式都要**你自己**的主页链接
            （点赞与收藏列表都只有本人可见）。
        register_bloggers: 「我的列表」入库后是否把未登记的来源作者补建成抖音博主
            并绑定本批素材（默认 True；只对 like / collection 模式生效）。补建的博主
            标记为「自动登记」，不算已登记博主、不进「一键获取素材」的下载白名单。
        like_max_counts: 「我的列表」最多翻多少条（None 表示执行时取
            ``settings.f2_like_max_counts``；0/None 表示全量翻到底）。两种模式通用。
        profiles: **按博主全量下载**——博主主页链接或 sec_user_id 列表。非空时只下
            这些博主，且**不要求它们已在 f2 用户库里**（f2 只认自己见过的账号）；
            首次采集自动用 `-i all` 翻全量，入库范围就是这些博主的产物。
        collect_ids: `fetch_mode=collection` 时**只下这些收藏夹**（夹 ID，来自
            `GET /api/scraper/f2-collects` 的扫描结果）。非空时不再走平铺收藏列表，
            改为逐夹枚举作品后交给 f2 的下载器——没被选中的夹一件都不会下载。
            空列表 = 老口径（下平铺收藏，含所有收藏夹）。

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
            "profiles": list(profiles or []),
            "collect_ids": list(collect_ids or []),
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
        {``available``, ``reason``, ``authors``, ``unknown_authors``, ``f2_dir``, ``root``,
         ``like_root``, ``collect_root``, ``like_user``, ``like_available``, ``like_reason``,
         ``collect_available``, ``collect_reason``, ``like_max_counts``, ``fetch_since_days``}
        - ``available``/``reason``：发布模式（已登记博主主页作品）的可用性与原因；
        - ``like_*`` / ``collect_*``：「我的喜欢 / 我的收藏」各自的可用性与原因
          （前提相同：f2 可用 + 工作目录存在 + 配了「我的主页链接」）；
        - ``like_root`` / ``collect_root``：两种「我的列表」模式的产物目录；
        - ``like_max_counts``：「我的列表」每次最多翻多少条（0=全量）；
        - ``fetch_since_days``：默认日期窗口天数，供前端「只翻最近 N 天」输入框
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
        "collect_root": str(f2.DEFAULT_F2_COLLECT_ROOT),
        # 「我的喜欢 / 我的收藏」需要我自己主页链接（两者都只有本人可见）；
        # 前端据此回填输入框。可用性与发布模式分开判定：它们不依赖「已登记博主」白名单。
        "like_user": like_user,
        "like_available": False,
        "like_reason": "",
        # 收藏模式的可用性：前提与点赞一致（f2 可用 + 工作目录存在 + 配了主页链接）
        "collect_available": False,
        "collect_reason": "",
        # 「我的喜欢」每次最多翻多少条（0=全量）。前端据此显示并允许改。
        # 为什么需要：f2 的点赞分页没有「遇到已下载就停」，全量翻页每次都要空等
        # 每页一次 timeout（本机 10 秒），且零新增时进度条会停在 0 像卡死。
        "like_max_counts": int(settings.f2_like_max_counts or 0),
        "fetch_since_days": int(settings.f2_fetch_since_days or 0),
    }

    f2_ok = f2.f2_available()
    dir_ok = f2.DEFAULT_F2_DIR.exists()
    info["like_available"] = bool(f2_ok and dir_ok and like_user)
    info["collect_available"] = info["like_available"]
    if info["like_available"]:
        info["like_reason"] = "已配置「我的主页链接」，可采集我的喜欢（点赞作品）"
        info["collect_reason"] = "已配置「我的主页链接」，可采集我的收藏（抖音收藏列表）"
    elif not f2_ok:
        info["like_reason"] = "未检测到 f2（python -m f2 不可用）：请先安装 f2"
        info["collect_reason"] = info["like_reason"]
    elif not dir_ok:
        info["like_reason"] = f"未找到 f2 工作目录：{f2.DEFAULT_F2_DIR}"
        info["collect_reason"] = info["like_reason"]
    else:
        info["like_reason"] = (
            "未配置「我的主页链接」：点赞列表只有本人可见，"
            "请先填写你自己的抖音主页链接或 sec_user_id"
        )
        info["collect_reason"] = (
            "未配置「我的主页链接」：收藏列表只有本人可见，"
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
    mode = str(settings.f2_import_auto_mode or "post")
    if mode not in ("post", *PERSONAL_FETCH_MODES):
        logger.warning(f"[f2 自动获取] 配置的模式 {mode!r} 不支持，按 post 处理")
        mode = "post"
    if mode in PERSONAL_FETCH_MODES:
        # 「我的列表」模式的前提是配了「我的主页链接」（点赞/收藏只有本人可见）
        if not status["like_available"]:
            logger.info(f"[f2 自动获取] 跳过本轮：{status['like_reason']}")
            return None
    elif not status["available"]:
        # 环境没准备好（f2 未装 / 作者库为空）：跳过并留痕，不制造失败任务
        logger.info(f"[f2 自动获取] 跳过本轮：{status['reason']}")
        return None

    task, _reused = await create_f2_import_task_if_idle(
        db,
        fetch=True,
        skip_live=bool(settings.f2_import_auto_skip_live),
        since_days=settings.f2_fetch_since_days,
        fetch_mode=mode,
        like_user=str(settings.f2_like_user or "") or None,
    )
    if task is None:
        # 锁内复查发现已有进行中任务（手动点击恰好抢先）：本轮静默跳过
        logger.info("[f2 自动获取] 跳过本轮：已有进行中的 f2_import 任务")
        return None
    logger.info(
        f"[f2 自动获取] 已创建任务 #{task.id}"
        f"（间隔 {interval_hours} 小时，模式 {mode}，作者库 {status['authors']} 个）"
    )
    return task.id


def _running_task_brief(row) -> dict | None:
    """把进行中的任务压成前端展示所需的少量字段（阶段 / 进度 / 计数 / 阶段标记）。

    ``stage`` 是理解 ``done/total`` 的前提：下载阶段是「作者数」，入库阶段是
    「文件数」，界面上要说清楚（见 web 侧 describeRunningTask）。``fetch_mode``
    决定文案说的是「博主主页作品」「我的喜欢」还是「我的收藏」；``like_progress``
    只在「我的列表」（点赞/收藏）下载阶段有值，给界面提供「已落盘 N 个文件 / X GB」
    这个真分母缺失时的证据。
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
        {enabled, mode, interval_hours, skip_live, available, reason, authors,
         last_task_at, next_due_at, running_task_id, running}
        ``mode`` 是自动获取走的入口（post / like / collection），``like_available``
        供界面提示「我的列表模式需要先配我的主页链接」。
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
        "mode": str(settings.f2_import_auto_mode or "post"),
        "interval_hours": interval_hours,
        "skip_live": bool(settings.f2_import_auto_skip_live),
        "available": info["available"],
        "reason": info["reason"],
        # 「我的列表」模式（like/collection）的前提：配了我的主页链接
        "like_available": info["like_available"],
        "like_reason": info["like_reason"],
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


async def _watch_personal_download(
    db: AsyncSession,
    task: TaskQueue,
    future: asyncio.Task,
    personal_root: Path,
    baseline: dict,
    opts: dict,
) -> tuple[dict, dict]:
    """一边等 f2 拉完「我的喜欢 / 我的收藏」，一边把已落盘的文件数写进任务结果。

    为什么需要：这类模式是单条命令全量翻页，下载期可能十几分钟；此前进度只在 f2
    返回后一次性写 0→40%，界面长时间停在 0%，看不出是在下载还是卡住。

    f2 是同步子进程（在线程里跑），本函数**不中断**它：取消/暂停仍由调用方在它
    返回后判定，与发布模式一致（已下载文件保留，重跑自动跳过）。

    Args:
        db: 数据库会话。
        task: 任务行。
        future: ``asyncio.to_thread(f2.run_fetch_likes/run_fetch_collects, ...)`` 的 future。
        personal_root: 「我的列表」产物目录（统计对象）。
        baseline: 下载开始前的统计（用来算「本次新增」）。
        opts: 任务参数（写回 result 时带上，避免上次执行的旧字段残留）。

    Returns:
        (f2 拉取函数的返回值, 结束时的目录统计)。
    """
    from scripts import import_f2_downloads as f2

    started = time.monotonic()
    while True:
        done, _pending = await asyncio.wait({future}, timeout=_WATCH_INTERVAL)
        stats = await asyncio.to_thread(f2.download_tree_stats, personal_root)
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
            logger.warning(f"f2 进度落库失败（忽略，下一轮重试）：{exc}")
        if done:
            return future.result(), stats


async def execute_f2_import(db: AsyncSession, task: TaskQueue) -> None:
    """执行 f2 一键获取素材（由 worker 调用）。

    执行期间每处理完一个作者 / 每 2 秒检查一次任务状态：被外部置为
    cancelled 则停止并标记 cancelled，置为 paused 则停止并保留 paused
    （已入库的素材与批次清单都保留，重新执行会按内容判重跳过）。

    本函数只做参数解析与**阶段编排**：每个阶段（与 task.result 的 stage 标记
    一一对应）拆到下方 ``_xxx_stage`` 私有函数，便于单独阅读与定位——
    下载 1a/1b → 扫描计划 2 → 入库 3 → 博主登记 3b →（收藏模式）合集聚合 3c → 收尾。
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
            "profiles",
            "collect_ids",
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
    # 「我」的主页链接：点赞/收藏列表只有本人可见，任务没带就用配置里记住的那个
    like_user = str(opts.get("like_user") or settings.f2_like_user or "").strip()
    personal_mode = fetch_mode in PERSONAL_FETCH_MODES
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
    # 按博主全量下载：显式点名的博主（主页链接 / sec_user_id）。非空时**只下这些**，
    # 且不需要它们已在 f2 用户库里——f2 只认自己见过的账号，这是那个限制的出口。
    profiles = [str(p).strip() for p in (opts.get("profiles") or []) if str(p).strip()]
    # 「先扫描、后下载」选中的收藏夹（夹 ID）：非空时只下这些夹（见
    # _fetch_collects_by_folder_stage），空列表保持老口径（平铺收藏）
    collect_ids = [str(c).strip() for c in (opts.get("collect_ids") or []) if str(c).strip()]
    register_bloggers = bool(opts.get("register_bloggers", True))
    since_days = opts.get("since_days")
    if since_days is None:
        since_days = settings.f2_fetch_since_days

    # 博主清单与 f2 账号清单：下载阶段用来判断「这个 f2 账号是否已登记」，
    # 入库阶段用来绑定作品。默认只处理能对应到已登记博主的账号——f2 用户库存的是
    # 它见过的所有账号，混进来的无关账号（实测出现过网易第五人格，被入库 142 条）
    # 不该收进素材库。库内一个抖音博主都没有时没有白名单依据，退回旧口径（全部处理）。
    #
    # 点赞/收藏（我的列表）模式例外：这些列表天然跨作者（点/收的是谁的作品都有），
    # 按白名单挡掉就失去意义，故不做作者过滤——入库仍走五层判重（内容哈希 / 垃圾桶 /
    # 批次内 / 平台 ID / 参数过滤），不会重复。
    f2_dir = f2.DEFAULT_F2_DIR
    bloggers = await asyncio.to_thread(f2.load_douyin_bloggers)
    f2_authors = await asyncio.to_thread(f2.load_f2_authors, f2_dir)
    filter_registered = (not include_unknown) and (not personal_mode) and bool(bloggers)
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

    # 下载目标（点名博主 / 白名单命中）：仅发布模式会填充，其它模式保持空列表
    targets: list[dict] = []

    # 下载阶段两个分支都要求本机可用 f2（跑不起来就不必往下走）：一次校验即可
    if fetch_enabled and not f2.f2_available():
        raise RuntimeError(
            "未检测到 f2（python -m f2 不可用）：请先安装 f2 并手动完成一次下载"
        )

    # ── 阶段 1a：「我的喜欢 / 我的收藏」——单条命令翻页（不逐作者、不做时间窗口）──
    if fetch_enabled and personal_mode and collect_ids and fetch_mode == "collection":
        # 「先扫描、后下载」：只下选中的收藏夹（逐夹枚举 + f2 下载器，页间可中断）
        if await _fetch_collects_by_folder_stage(
            db, task, opts, f2_dir, like_user, collect_ids, like_max_counts, fetch_summary
        ):
            return

    elif fetch_enabled and personal_mode:
        if await _fetch_personal_stage(
            db, task, opts, f2_dir, fetch_mode, like_user, like_max_counts, fetch_summary
        ):
            return

    # ── 阶段 1b：发布模式——逐作者串行下载（子进程放线程）──
    elif fetch_enabled:
        targets = _select_post_targets(
            profiles, known_authors, authors, opts.get("fetch_limit")
        )
        if await _fetch_posts_stage(
            db, task, opts, targets, profiles, since_days, f2_dir, fetch_summary
        ):
            return

    # 点名博主：反查昵称成功即以这批产物为入库范围（不再套「已登记博主」白名单）
    if profiles and fetch_enabled:
        plan_authors = await _resolve_named_profile_keys(
            db, task, opts, targets, fetch_summary, f2_dir
        )

    # ── 阶段 2：扫描 + 去重计划 ──
    to_import = await _scan_and_plan_stage(
        db, task, opts, bloggers, plan_authors, limit, skip_live, fetch_mode, fetch_summary
    )
    if not to_import:
        return

    # ── 阶段 3：入库（同步 sqlite 放线程；进度回调 + 状态检查）──
    result, holder = await _apply_import_stage(db, task, to_import, make_thumbnails)

    status_now = await _current_status(db, task.id)
    interrupted = status_now in ("cancelled", "paused")

    # ── 阶段 3b：「我的列表」补登记来源作者博主 ──
    bloggers_stats = await _register_personal_bloggers_stage(
        db, task, result, personal_mode, register_bloggers
    )

    # ── 阶段 3c：「我的收藏」把本批素材聚合进「抖音收藏」合集 ──
    if fetch_mode == "collection":
        await _aggregate_collect_stage(db, task, result)

    # ── 收尾 ──
    await _finalize_import(
        db, task, result, holder, bloggers_stats, interrupted, status_now
    )


async def _fetch_personal_stage(
    db: AsyncSession,
    task: TaskQueue,
    opts: dict,
    f2_dir: Path,
    fetch_mode: str,
    like_user: str,
    like_max_counts: int,
    fetch_summary: dict,
) -> bool:
    """阶段 1a：「我的喜欢 / 我的收藏」——单条命令翻页（不逐作者、不做时间窗口）。

    下载结束后先做一次**跨模式重复合并**（like ↔ collection 里同一作品的同一分段
    合并成硬链接，见 :func:`f2.merge_personal_duplicates`），再交给阶段 2 扫描。

    返回 True 表示任务已被取消/暂停（收尾已落库，调用方直接返回）。"""
    from scripts import import_f2_downloads as f2

    label = _personal_label(fetch_mode)
    if not like_user:
        raise RuntimeError(
            f"未配置「我的主页链接」：{label}只有本人可见，"
            f"请先在「{label}」卡片里填写你的抖音主页链接"
        )
    fetch_summary["total"] = 1
    # 总数要翻到底才知道：下载期不给 done/total（计数看 like_progress 的文件数），
    # 进度条由 watcher 按耗时给软进度。
    # 增量模式（like_max_counts>0）另有作用：翻页量有上界，不会再出现「零新增却
    # 空翻到底、进度条长时间停在 0」。
    task.total = 0
    task.done = 0
    task.result = {**opts, "stage": "download", "like_max_counts": like_max_counts}
    task.updated_at = utcnow()
    await db.commit()

    personal_root = _personal_scan_root(f2, fetch_mode)
    fetcher = f2.run_fetch_collects if fetch_mode == "collection" else f2.run_fetch_likes
    baseline = await asyncio.to_thread(f2.download_tree_stats, personal_root)
    fetch_task = asyncio.create_task(
        asyncio.to_thread(
            fetcher,
            f2_dir,
            like_user,
            f2_dir / "Download",
            max_counts=like_max_counts,
        )
    )
    outcome, final_stats = await _watch_personal_download(
        db, task, fetch_task, personal_root, baseline, opts
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
    # 下载阶段收尾：把 like / collection 两个「我的列表」目录里同一作品的同一分段
    # 合并成硬链接。f2 判断「下过没有」只看**当前模式目录里有没有同名文件**（它没有
    # 下载台账，目录本身就是台账），所以两个列表交叉的作品会被各下一次、各存一份；
    # 合并后两个目录里文件都还在（两侧「存在即跳过」继续有效），磁盘只占一份。
    # 放线程：要扫两个目录并比对内容，不能阻塞 worker 事件循环。
    merge_stats = await asyncio.to_thread(f2.merge_personal_duplicates)
    fetch_summary["merge"] = merge_stats
    if merge_stats["linked"] or merge_stats["conflict"] or merge_stats["failed"]:
        samples = f"；样例：{'；'.join(merge_stats['samples'])}" if merge_stats["samples"] else ""
        logger.info(
            f"[f2 跨模式重复] 合并 {merge_stats['linked']} 个文件（省 "
            f"{merge_stats['saved_bytes'] / 1048576:.1f} MB），内容不同保留两份 "
            f"{merge_stats['conflict']}，失败 {merge_stats['failed']}{samples}"
        )
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
        logger.info(f"f2 拉取「{label}」被中断（{status_now}）：已下载文件保留")
        return True
    if fetch_summary["failed"]:
        task.result = download_result
        await db.commit()
        raise RuntimeError(
            f"f2 拉取「{label}」失败（退出码非 0），常见原因：cookie 失效或被风控；"
            "请先手动跑一次 f2 确认能下载，且 -u 填的是**你自己**的主页链接"
        )
    # 下载完成且未被中断：交给阶段 2 扫描入库
    return False


async def _fetch_collects_by_folder_stage(
    db: AsyncSession,
    task: TaskQueue,
    opts: dict,
    f2_dir: Path,
    like_user: str,
    collect_ids: list[str],
    max_per_folder: int,
    fetch_summary: dict,
) -> bool:
    """阶段 1a′：「先扫描、后下载」——**只下载选中的收藏夹**（逐夹枚举 + f2 下载器）。

    与平铺 `-M collection`（:func:`_fetch_personal_stage`）的区别：

    1. 作品清单来自**收藏夹接口**（按夹），没被选中的夹一件都不会下载——这是这个
       入口存在的全部理由（平铺收藏会把所有夹一起下，实测 31 个夹约 2000 件，
       里面混着「股票 / 哲学 / 历史」这类不想要的）
    2. 进度分母是**真的**：每个夹的 `total_number` 已知，进度条不再是按耗时估的软进度
    3. **页间可中断**：逐页处理并在每页前后看停止标记，所以暂停/取消能在下载中途
       真正生效（平铺模式走 f2 子进程，取消要等整条命令跑完）

    返回 True 表示任务已被取消/暂停（收尾已落库，调用方直接返回）。"""
    from scripts import import_f2_downloads as f2

    label = f"我的收藏（{len(collect_ids)} 个收藏夹）"
    if not like_user:
        raise RuntimeError(
            "未配置「我的主页链接」：收藏列表只有本人可见，"
            "请先在「我的收藏」卡片里填写你的抖音主页链接"
        )

    personal_root = f2.DEFAULT_F2_COLLECT_ROOT
    holder: dict = {
        "stop": False,
        "works": 0,
        "total_works": 0,
        "folders": [],
        "current": "",
    }
    finished = asyncio.Event()
    baseline = await asyncio.to_thread(f2.download_tree_stats, personal_root)

    task.total = len(collect_ids)
    task.done = 0
    task.result = {**opts, "stage": "download", "collect_progress": _collect_progress(holder)}
    task.updated_at = utcnow()
    await db.commit()

    def _on_progress(stats: dict) -> None:
        """下载线程内回调（同步）：只更新内存里的计数，落库交给 watcher。"""
        holder["works"] = int(stats.get("works") or 0)
        holder["total_works"] = int(stats.get("total_works") or 0)
        holder["folders"] = stats.get("folders") or []
        current = stats.get("current") or {}
        holder["current"] = str(current.get("name") or "")

    def _should_stop() -> bool:
        return holder["stop"]

    def _run() -> dict:
        return f2.download_collect_folders(
            f2_dir,
            like_user,
            collect_ids,
            max_counts=max_per_folder,
            should_stop=_should_stop,
            on_progress=_on_progress,
        )

    async def _watcher() -> None:
        """定时把线程内进度落库，并把外部 cancelled/paused 转成停止标记。"""
        while not finished.is_set():
            try:
                await asyncio.wait_for(finished.wait(), timeout=_WATCH_INTERVAL)
                break
            except TimeoutError:
                pass
            try:
                if await _current_status(db, task.id) not in ("running", "pending"):
                    holder["stop"] = True
                task.done = len(holder["folders"])
                task.progress = _collect_progress_pct(holder)
                task.result = {
                    **opts,
                    "stage": "download",
                    "collect_progress": _collect_progress(holder),
                }
                task.updated_at = utcnow()
                await db.commit()
            except Exception as exc:  # noqa: BLE001 —— 进度是辅助信息，不能拖垮下载
                await db.rollback()
                logger.warning(f"f2 收藏夹下载进度落库失败（忽略，下一轮重试）：{exc}")

    fetch_task = asyncio.create_task(asyncio.to_thread(_run))
    watcher = asyncio.create_task(_watcher())
    try:
        outcome = await fetch_task
    finally:
        finished.set()
        await watcher

    final_stats = await asyncio.to_thread(f2.download_tree_stats, personal_root)
    fetch_summary["ok"] = 1
    fetch_summary["failed"] = 0
    fetch_summary["like_user"] = f2.like_user_url(like_user)
    fetch_summary["collect"] = {
        "folders": outcome.get("folders") or [],
        "works": int(outcome.get("works") or 0),
        "stopped": bool(outcome.get("stopped")),
        "missing_folders": outcome.get("missing_folders") or [],
        "per_folder_max_counts": max_per_folder,
    }
    fetch_summary["downloaded"] = {
        "files": final_stats["files"],
        "bytes": final_stats["bytes"],
        "added": max(0, final_stats["files"] - baseline["files"]),
        "added_bytes": max(0, final_stats["bytes"] - baseline["bytes"]),
    }
    merge_stats = await asyncio.to_thread(f2.merge_personal_duplicates)
    fetch_summary["merge"] = merge_stats
    download_result = {
        **opts,
        "stage": "download",
        "fetch": fetch_summary,
        "collect_progress": _collect_progress(holder),
    }
    task.done = len(holder["folders"])
    task.total = max(1, len(collect_ids))
    task.progress = _PROGRESS_AFTER_DOWNLOAD
    task.result = download_result
    task.updated_at = utcnow()
    await db.commit()

    # 取消/暂停：立即收尾，不再做扫描与入库（已下载文件保留，重跑按内容判重跳过）
    if await _current_status(db, task.id) not in ("running", "pending"):
        status_now = await _current_status(db, task.id)
        task.result = download_result
        task.status = status_now
        task.updated_at = utcnow()
        await db.commit()
        logger.info(
            f"f2 拉取「{label}」被中断（{status_now}）："
            f"已完成 {len(holder['folders'])} 个夹、{holder['works']} 件，已下载文件保留"
        )
        return True
    logger.info(
        f"f2 按收藏夹下载完成：{len(holder['folders'])} 个夹、{holder['works']} 件"
        f"（新落盘 {fetch_summary['downloaded']['added']} 个文件）"
    )
    return False


def _collect_progress(holder: dict) -> dict:
    """收藏夹下载进度的对外结构（任务结果里的 ``collect_progress``）。"""
    return {
        "works": int(holder.get("works") or 0),
        "total_works": int(holder.get("total_works") or 0),
        "folders": holder.get("folders") or [],
        "current": str(holder.get("current") or ""),
    }


def _collect_progress_pct(holder: dict) -> int:
    """按「已处理作品数 / 选中的夹作品总数」算百分比（下载阶段占 1~40%）。"""
    total = int(holder.get("total_works") or 0)
    if total <= 0:
        # 还没枚举到分母：按已完成的夹数给个粗略起点，别停在 0%
        return 1
    done = min(total, int(holder.get("works") or 0))
    return max(1, min(_PROGRESS_AFTER_DOWNLOAD, int(_PROGRESS_AFTER_DOWNLOAD * done / total)))


def _select_post_targets(
    profiles: list[str],
    known_authors: list[dict],
    authors: set[str] | None,
    fetch_limit: int | None,
) -> list[dict]:
    """阶段 1b 的目标选择：点名博主优先，否则走白名单（可再被 authors 收窄）。

    两类「一个都下不了」的情况都**响亮失败**（见各 raise 的注释），不再出现
    下载 0 个作者却报 success 的静默失败。"""
    from scripts import import_f2_downloads as f2

    if profiles:
        # 按博主全量：**显式点名**的博主直接下，不要求它先出现在 f2 用户库里
        # （f2 只认自己见过的账号，这正是那个限制的出口）。入库白名单同理，
        # 不能用「已登记博主」那一套——用户已经点名了。
        parsed = [(raw, f2.profile_author(raw)) for raw in profiles]
        invalid = [raw for raw, author in parsed if author is None]
        if invalid:
            raise RuntimeError(
                f"无法识别的博主：{'、'.join(invalid)}。"
                "请填完整主页链接（https://www.douyin.com/user/MS4wLjABAAAA…）"
                "或 sec_user_id；抖音号与 v.douyin.com 短链不支持"
            )
        targets = [author for _raw, author in parsed if author]
        if not targets:
            raise RuntimeError("按博主全量下载需要至少一个博主主页链接或 sec_user_id")
    else:
        targets = known_authors
        wanted = {f2.normalize_author(a) for a in authors} if authors else None
        if wanted is not None:
            targets = [
                a for a in targets if f2.normalize_author(a["nickname"]) in wanted
            ]
            # 点名的作者一个都不在 f2 用户库里 → 什么都下不了。**必须响亮失败**：
            # 任务 351 就是这么静默过去的（请求「唐思瑶ya」不在 f2 用户库，
            # 下载 0 个作者、入库 0，状态却是 success，用户看不出任何原因）。
            if not targets:
                available = sorted(a["nickname"] for a in known_authors)
                raise RuntimeError(
                    f"这些作者不在 f2 用户库里，无法下载：{'、'.join(sorted(wanted))}。"
                    "f2 的下载目标只来自它自己的用户库（它只认见过的账号），"
                    "所以要先让 f2 认识她们——改用卡片里的「按博主全量下载」"
                    "（填博主主页链接即可，不需要 f2 事先认识），"
                    "或先手动跑一次 f2 采她的主页。"
                    + (
                        f"当前 f2 认识 {len(available)} 个账号：{'、'.join(available[:10])}"
                        f"{'…' if len(available) > 10 else ''}"
                        if available
                        else "当前 f2 用户库为空"
                    )
                )
    if fetch_limit:
        targets = targets[: int(fetch_limit)]

    return targets


async def _fetch_posts_stage(db: AsyncSession, task: TaskQueue, opts: dict, targets: list[dict],
                   profiles: list[str], since_days: int, f2_dir: Path,
                   fetch_summary: dict) -> bool:
    """阶段 1b：发布模式——逐作者串行下载（子进程放线程）。

    返回 True 表示任务已被取消/暂停（已落库，调用方直接返回）。"""
    from scripts import import_f2_downloads as f2

    fetch_summary["total"] = len(targets)
    task.total = len(targets)
    task.done = 0
    await db.commit()

    # 日期窗口：按作者目录的最近下载时间逐作者计算（见 compute_fetch_interval）。
    # 不给窗口时 f2 会把作者全部历史翻一遍且每页固定 sleep 一次 timeout。
    # 新博主（无论点名还是首次）没有本地记录 → 该函数给 `all`，即全量。
    post_root = f2_dir / f2.F2_DOWNLOAD_SUBDIR
    last_download = await asyncio.to_thread(f2.author_last_download, post_root)

    for index, author in enumerate(targets, 1):
        if await _current_status(db, task.id) not in ("running", "pending"):
            fetch_summary["aborted"] = True
            break
        display = f2.profile_display(author)
        last_at = last_download.get(f2.normalize_author(author["nickname"] or ""))
        # 点名博主用 compute_profile_interval：**首次全量**（否则只拿到最近
        # since_days 天，用户以为下全了其实没有）
        interval = (
            f2.compute_profile_interval(since_days, last_at)
            if profiles
            else f2.compute_fetch_interval(since_days, last_at)
        )
        cmd = f2.build_f2_command(
            author, download_root=f2_dir / "Download", interval=interval
        )
        logger.info(f"f2 下载 {display}（窗口 {interval}）")
        try:
            rc = await asyncio.to_thread(_run_subprocess, cmd, f2_dir)
        except Exception as exc:  # noqa: BLE001 —— 单作者失败不阻断整批
            rc = -1
            logger.warning(f"f2 调用异常（{display}）：{exc}")
        if rc == 0:
            fetch_summary["ok"] += 1
        else:
            fetch_summary["failed"] += 1
            logger.warning(f"f2 下载失败（{display}）退出码 {rc}，跳过该作者")
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
        return True

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
    return False


async def _resolve_named_profile_keys(db: AsyncSession, task: TaskQueue, opts: dict,
                            targets: list[dict], fetch_summary: dict,
                            f2_dir: Path) -> set[str]:
    """点名博主的昵称反查（决定本次入库范围）。

    下单后 f2 已把新账号写进用户库，此时才能按 sec_user_id 反查昵称——入库白名单
    是按**归一化昵称**匹配的，缺了它就会「下载成功、入库 0」。反查不到任何昵称说明
    这批博主没下成：响亮失败，而不是报成功 + 入库 0。"""
    from scripts import import_f2_downloads as f2

    resolved = await asyncio.to_thread(
        f2.resolve_profile_nicknames,
        f2_dir,
        [str(a.get("sec_user_id") or "") for a in targets],
    )
    keys = {f2.normalize_author(n) for n in resolved.values() if n}
    fetch_summary["profiles"] = [
        {
            "sec_user_id": sec,
            "nickname": resolved.get(sec, ""),
            "url": f"https://www.douyin.com/user/{sec}",
        }
        for sec in (str(a.get("sec_user_id") or "") for a in targets)
    ]
    if not keys:
        task.result = {**opts, "stage": "download", "fetch": fetch_summary}
        task.progress = _PROGRESS_AFTER_DOWNLOAD
        await db.commit()
        raise RuntimeError(
            "这些博主一个都没下成，因此没有可入库的产物。"
            "常见原因：cookie 失效（f2 下载需要登录态，且项目不传 "
            "--auto-cookie，用的是 f2 配置里的 cookie）、主页链接/账号有误，"
            "或被风控。请先手动跑一次 "
            "`python -m f2 dy -u <主页链接> -M post -i all` 确认能下载"
        )
    # 点名博主的产物就是本次的入库范围（不再套「已登记博主」白名单）
    return keys


async def _scan_and_plan_stage(db: AsyncSession, task: TaskQueue, opts: dict, bloggers: dict,
                     plan_authors: set[str] | None, limit: int | None, skip_live: bool,
                     fetch_mode: str, fetch_summary: dict) -> list:
    """阶段 2：扫描下载目录 + 去重计划（放线程，否则阻塞 worker 事件循环）。

    无可入库文件时本函数直接落「done」结果并返回空列表，调用方据此收工。"""
    from scripts import import_f2_downloads as f2

    scan_root = (
        _personal_scan_root(f2, fetch_mode)
        if fetch_mode in PERSONAL_FETCH_MODES
        else f2.DEFAULT_F2_ROOT
    )
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
        return []
    return to_import


async def _apply_import_stage(db: AsyncSession, task: TaskQueue, to_import: list,
                    make_thumbnails: bool) -> tuple[dict, dict]:
    """阶段 3：入库（同步 sqlite 放线程；进度 watcher + 状态检查）。

    返回 (apply_import 结果, 进度 holder)。"""
    from scripts import import_f2_downloads as f2

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
    return result, holder


async def _register_personal_bloggers_stage(db: AsyncSession, task: TaskQueue, result: dict,
                              personal_mode: bool, register_bloggers: bool) -> dict | None:
    """阶段 3b：「我的喜欢 / 我的收藏」补登记来源作者博主（失败不影响已入库素材）。"""
    from scripts import import_f2_downloads as f2

    bloggers_stats: dict | None = None
    if personal_mode and register_bloggers and result.get("batch_file"):
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
                f"f2「我的列表」博主登记：新建 {bloggers_stats['created']} 个、"
                f"复用 {bloggers_stats['reused']} 个、绑定素材 {bloggers_stats['linked']} 条"
                f"（同名多候选跳过 {bloggers_stats['ambiguous']} 个）"
            )
        except Exception as exc:  # noqa: BLE001 —— 素材已入库，登记失败不该让任务失败
            await db.rollback()
            bloggers_stats = None
            task.error = f"来源作者博主自动登记失败（素材已入库，可在结果面板手工重试）：{exc}"
            logger.warning(f"f2 自动登记博主失败：{exc}")
    return bloggers_stats


async def _aggregate_collect_stage(db: AsyncSession, task: TaskQueue, result: dict) -> dict | None:
    """阶段 3c：「我的收藏」把本批入库素材聚合进「抖音收藏」合集。

    为什么用合集而不是标签：合集的语义就是「一批素材的集合」（收藏合计里直接看到数量
    与体积），而标签是 AI/手动语义、会进入标签治理（去重/合并/健康扫描）——收藏来源是
    事实而非语义，不该污染标签体系。

    幂等：合集不存在则创建，已加入的素材不会重复（collection_items 有唯一约束）。
    失败不影响已入库素材，只写进任务 error 提示人工处理。
    """
    imported_ids = [str(i) for i in (result.get("ids") or []) if i]
    if not imported_ids:
        return None
    from app.services import collection_service
    from app.models.collection import Collection

    try:
        collection = (
            await db.execute(
                select(Collection).where(Collection.name == COLLECT_COLLECTION_NAME)
            )
        ).scalars().first()
        if collection is None:
            data = await collection_service.create_collection(
                db,
                name=COLLECT_COLLECTION_NAME,
                description="由「一键获取我的收藏」自动聚合：本合集的素材来自抖音收藏列表",
                query_json=None,
            )
            collection_id = int(data["id"])
            created = True
        else:
            collection_id = int(collection.id)
            created = False
        added = await collection_service.add_inspirations(db, collection_id, imported_ids)
        stats = {
            "id": collection_id,
            "name": COLLECT_COLLECTION_NAME,
            "created": created,
            "added": int(added.get("added") or 0),
            "skipped": int(added.get("skipped") or 0),
        }
        task.result = {**task.result, "collection": stats}
        await db.commit()
        logger.info(
            f"f2「我的收藏」已聚合进合集「{COLLECT_COLLECTION_NAME}」#{collection_id}："
            f"新增 {stats['added']} 条（已在合集内跳过 {stats['skipped']} 条）"
        )
        return stats
    except Exception as exc:  # noqa: BLE001 —— 素材已入库，聚合失败不该让任务失败
        await db.rollback()
        task.error = f"加入「{COLLECT_COLLECTION_NAME}」合集失败（素材已入库）：{exc}"
        logger.warning(f"f2 收藏合集聚合失败：{exc}")
        return None


async def _finalize_import(db: AsyncSession, task: TaskQueue, result: dict, holder: dict,
                 bloggers_stats: dict | None, interrupted: bool,
                 status_now: str) -> None:
    """收尾：写终态与结果（被中断时尊重外部状态，不覆盖为 success）。"""

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

