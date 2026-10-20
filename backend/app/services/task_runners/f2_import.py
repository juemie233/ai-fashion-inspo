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

模块边界（2026-10-20 拆分完成，见 docs/代码检查.md「结构性欠账」）：
  本文件只剩**任务创建 / 状态查询 / 自动调度 / 执行编排**，各阶段在
  ``f2_personal``（「我的列表」常量与助手）、``f2_fetch_stages``（取数）、
  ``f2_plan_apply``（扫描去重 / 入库 / 登记）、``f2_finalize``（合集归位 / 收尾结算）。
  文末把这些符号**原样再导出**，`f2_import.xxx` 的既有用法与测试 patch 点不变；
  拆分时踩过的坑（monkeypatch 静默失效、转发壳、F401 收尾）同见该文档。
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskQueue
from app.services.task_runners.common import (
    PermanentTaskError,
    # 取消/暂停判定：实现住在 common.py，这里持有同名别名是为了让
    # `monkeypatch.setattr(f2_import, "_current_status", fake)` 继续生效（测试依赖），
    # 同时把同一个可调用对象**注入**给各阶段函数（见 execute_f2_import 的调用点）。
    current_status as _current_status,
    utcnow,
)

logger = logging.getLogger(__name__)


async def create_f2_import_task(
    db: AsyncSession,
    authors: list[str] | None = None,
    limit: int | None = None,
    skip_live: bool = False,
    fetch: bool = True,
    fetch_limit: int | None = None,
    make_thumbnails: bool = True,
    prune_downloads: bool = False,
    since_days: int | None = None,
    include_unknown_authors: bool = False,
    fetch_mode: str = "post",
    like_user: str | None = None,
    register_bloggers: bool = True,
    like_max_counts: int | None = None,
    profiles: list[str] | None = None,
    collect_ids: list[str] | None = None,
    allow_all_collect: bool = False,
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
            并绑定本批素材（默认 True；**只对 like 生效**：collection 由
            :func:`personal_registers_bloggers` 强制关闭，见那里的说明）。补建的博主
            标记为「自动登记」，不算已登记博主、不进「一键获取素材」的下载白名单。
        like_max_counts: 「我的列表」最多翻多少条（None 表示执行时取
            ``settings.f2_like_max_counts``；0/None 表示全量翻到底）。两种模式通用；
            **带 collect_ids（按收藏夹下载）时是「每个夹」的上限**（每夹各取最近 N 件，
            否则大夹会把小夹饿死——实测夹内作品数从 1 到 334 差三个数量级）。
        profiles: **按博主全量下载**——博主主页链接或 sec_user_id 列表。非空时只下
            这些博主，且**不要求它们已在 f2 用户库里**（f2 只认自己见过的账号）；
            首次采集自动用 `-i all` 翻全量，入库范围就是这些博主的产物。
        collect_ids: `fetch_mode=collection` 时**只下这些收藏夹**（夹 ID，来自
            `GET /api/scraper/f2-collects` 的扫描结果）。非空时不再走平铺收藏列表，
            改为逐夹枚举作品后交给 f2 的下载器——没被选中的夹一件都不会下载，
            **也不会入库**（入库范围同样按勾选的夹过滤）。
        allow_all_collect: 没勾任何收藏夹时是否允许「平铺收藏」导入**全部**收藏
            （含用户没勾的夹）。默认 False：这条路径会把用户明确不想要的夹
            （股票/哲学这类）也收进素材库，实测已发生两次（任务 #386、#387），
            所以改成必须显式确认——前端「一键下载全部收藏」按钮会在确认弹窗后
            才把它置为 True。

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
            "prune_downloads": bool(prune_downloads),
            "since_days": since_days,
            "include_unknown_authors": include_unknown_authors,
            "fetch_mode": fetch_mode,
            "like_user": like_user,
            "register_bloggers": personal_registers_bloggers(
                fetch_mode, register_bloggers
            ),
            "like_max_counts": like_max_counts,
            "profiles": list(profiles or []),
            "collect_ids": list(collect_ids or []),
            "allow_all_collect": bool(allow_all_collect),
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
    db: AsyncSession, **kwargs: Any
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
    info: dict[str, Any] = {
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
        # 「按博主全量下载」（profiles：点名主页链接/sec_user_id → 下这个博主的全部作品）
        # 的可用性单独一档：只要 f2 能跑、工作目录在就够——它**不依赖 f2 用户库里有没有
        # 这个账号**，首次采集自己会翻全量。沿用发布模式的 available 会把这个入口挡在
        # 「已登记博主白名单」之外（点赞入口栽过同一个坑，见 tests 里的
        # test_f2_import_like_mode_not_blocked_by_blogger_whitelist）。
        "profiles_available": False,
        "profiles_reason": "",
        # 「我的喜欢」每次最多翻多少条（0=全量）。前端据此显示并允许改。
        # 为什么需要：f2 的点赞分页没有「遇到已下载就停」，全量翻页每次都要空等
        # 每页一次 timeout（本机 10 秒），且零新增时进度条会停在 0 像卡死。
        "like_max_counts": int(settings.f2_like_max_counts or 0),
        "fetch_since_days": int(settings.f2_fetch_since_days or 0),
    }

    f2_ok = f2.f2_available()
    dir_ok = f2.DEFAULT_F2_DIR.exists()

    # 按博主全量下载：与点赞/收藏一样只要求「f2 可用 + 工作目录存在」（见上文字段注释）
    info["profiles_available"] = bool(f2_ok and dir_ok)
    if info["profiles_available"]:
        info["profiles_reason"] = "可点名下载指定博主的全部作品（首次翻全量，之后增量）"
    elif not f2_ok:
        info["profiles_reason"] = "未检测到 f2（python -m f2 不可用）：请先安装 f2"
    else:
        info["profiles_reason"] = f"未找到 f2 工作目录：{f2.DEFAULT_F2_DIR}"

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

    known: Any = authors
    unknown: list[dict] = []
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
        if mode == "collection":
            # 收藏模式必须**手选收藏夹**（见 execute_f2_import 的硬前置）：
            # 自动获取没有「勾选」这回事，跑起来只会导入整个收藏目录。
            logger.info(
                "[f2 自动获取] 跳过本轮：收藏模式需要手选收藏夹，"
                "请在采集卡片上点「先扫描收藏夹」手动运行"
            )
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


def _running_task_brief(row: Any) -> dict | None:
    """把进行中的任务压成前端展示所需的少量字段（阶段 / 进度 / 计数 / 阶段标记）。

    ``stage`` 是理解 ``done/total`` 的前提：下载阶段是「作者数」，入库阶段是
    「文件数」，界面上要说清楚（见 web 侧 describeRunningTask）。``fetch_mode``
    决定文案说的是「博主主页作品」「我的喜欢」还是「我的收藏」；``download_progress``
    只在「我的列表」（点赞/收藏）下载阶段有值，给界面提供「已落盘 N 个文件 / X GB」
    这个真分母缺失时的证据。
    """
    if row is None:
        return None
    task_id, status, progress, done, total, result = row
    stage = ""
    fetch_mode = ""
    download_progress = None
    like_max_counts = 0
    if isinstance(result, dict):
        stage = str(result.get("stage") or "")
        fetch_mode = str(result.get("fetch_mode") or "")
        like_max_counts = int(result.get("like_max_counts") or 0)
        raw_progress = result.get("download_progress")
        if isinstance(raw_progress, dict):
            download_progress = raw_progress
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
        "download_progress": download_progress,
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
    auto_mode = str(settings.f2_import_auto_mode or "post")
    return {
        "enabled": bool(settings.f2_import_auto_enabled),
        "mode": auto_mode,
        "interval_hours": interval_hours,
        "skip_live": bool(settings.f2_import_auto_skip_live),
        "available": info["available"],
        "reason": info["reason"],
        # 「我的列表」模式（like/collection）的前提：配了我的主页链接
        "like_available": info["like_available"],
        "like_reason": info["like_reason"],
        # 收藏模式不支持自动获取（没有「手选收藏夹」这回事，自动跑只会导入整个收藏
        # 目录）：前端据此把开关说明写清楚，避免用户以为它能自动同步收藏夹
        "collection_unsupported": auto_mode == "collection",
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
            "prune_downloads",
            "since_days",
            "include_unknown_authors",
            "fetch_mode",
            "like_user",
            "register_bloggers",
            "like_max_counts",
            "profiles",
            "collect_ids",
            "allow_all_collect",
        )
        if key in raw_result
    }
    authors = set(opts.get("authors") or []) or None
    fetch_enabled = bool(opts.get("fetch", True))
    limit = opts.get("limit")
    skip_live = bool(opts.get("skip_live", False))
    make_thumbnails = bool(opts.get("make_thumbnails", True))
    include_unknown = bool(opts.get("include_unknown_authors", False))
    # 没勾收藏夹时是否允许导入「全部收藏」：默认 False（见 create_f2_import_task 说明）
    allow_all_collect = bool(opts.get("allow_all_collect", False))
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
    # 同一个纯函数在建任务时也用过：直接构造的任务（测试/旧记录）在这里再兜一层，
    # 保证「收藏不补建博主」这条口径不依赖调用方
    register_bloggers = personal_registers_bloggers(
        fetch_mode, bool(opts.get("register_bloggers", True))
    )
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

    # 收藏模式的硬前置：**没勾收藏夹就什么都不做**（除非显式确认「全部收藏」）。
    # 平铺收藏会把整棵收藏目录（用户明确不想要的夹也在内）全收进素材库——实测
    # 任务 #386、#387 各因此进了 2627 件无关内容。放在下载之前拦，连下载都不必跑。
    if fetch_mode == "collection" and not collect_ids and not allow_all_collect:
        task.result = {**task.result, "stage": "done", "selection_required": True}
        task.updated_at = utcnow()
        await db.commit()
        raise PermanentTaskError(
            "收藏模式需要先选收藏夹：请在卡片上点「先扫描收藏夹」，"
            "勾选要归档的夹再下载（未勾选的夹既不会下载也不会入库）。"
            "确实要导入全部收藏（含未勾选的夹）时，请用「一键下载全部收藏」并确认。"
        )

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
    if not fetch_enabled and collect_ids:
        # 只入库（fetch=false）也认收藏夹范围：不下载，但只把勾选夹的作品入库。
        # 这样「修正一批误入库/补一批漏入库」都不必重新下载。
        logger.info(
            f"[f2] fetch=false + collect_ids（{len(collect_ids)} 个夹）：不下载，"
            "只入库这些收藏夹里已下载的作品"
        )
    if fetch_enabled and personal_mode and collect_ids and fetch_mode == "collection":
        # 「先扫描、后下载」：只下选中的收藏夹（逐夹枚举 + f2 下载器，页间可中断）
        if await _fetch_collects_by_folder_stage(
            db,
            task,
            opts,
            f2_dir,
            like_user,
            collect_ids,
            like_max_counts,
            fetch_summary,
            status_of=_current_status,
        ):
            return

    elif fetch_enabled and personal_mode:
        if await _fetch_personal_stage(
            db,
            task,
            opts,
            f2_dir,
            fetch_mode,
            like_user,
            like_max_counts,
            fetch_summary,
            status_of=_current_status,
        ):
            return

    # ── 阶段 1b：发布模式——逐作者串行下载（子进程放线程）──
    elif fetch_enabled:
        targets = _select_post_targets(
            profiles, known_authors, authors, opts.get("fetch_limit")
        )
        if await _fetch_posts_stage(
            db,
            task,
            opts,
            targets,
            profiles,
            since_days,
            f2_dir,
            fetch_summary,
            status_of=_current_status,
        ):
            return

    # 点名博主：反查昵称成功即以这批产物为入库范围（不再套「已登记博主」白名单）
    if profiles and fetch_enabled:
        plan_authors = await _resolve_named_profile_keys(
            db, task, opts, targets, fetch_summary, f2_dir
        )

    # ── 阶段 2：扫描 + 去重计划 ──
    # 收藏模式顺手把「本次扫描到的作品 ID」带出来：合集要收的是你的收藏全集，
    # 而不是「本批新入库的那几条」（已在库的会被判重跳过，见阶段 3c）
    collect_scope: dict = {}
    # 勾选的收藏夹 = 入库范围白名单（None 表示不限定）。收藏目录里躺着**所有**夹的
    # 历史文件，「只下勾选的夹」不足以让它们不进库——入库这一步必须同样只看勾选的夹。
    allowed_aweme_ids: set[str] | None = None
    folder_entries: dict[str, dict] = {}
    if fetch_mode == "collection":
        folder_entries = load_collect_folder_entries(_personal_scan_root(f2, fetch_mode))
        if collect_ids:
            allowed_aweme_ids = set()
            for folder_id in collect_ids:
                entry = folder_entries.get(str(folder_id))
                if entry:
                    allowed_aweme_ids.update(entry["aweme_ids"])
            if not allowed_aweme_ids:
                # 勾了夹但清单里没有任何作品 ID：宁可什么都不入库，也不要退化成
                # 「整棵树都收」（那正是用户要避免的：无关内容混进素材库）。
                # 这里**必须抛错**：任务一件都没干成，标成 success 等于静默骗人
                # （而且 worker 会把 task.error 清掉，连原因都留不下）。
                logger.warning(
                    f"[f2] 勾选的 {len(collect_ids)} 个收藏夹在归属清单里没有作品记录，"
                    "本次不入库任何文件（请用「先扫描收藏夹」重新下载以生成归属清单）"
                )
                task.result = {**task.result, "stage": "done", "scope_empty": True}
                task.updated_at = utcnow()
                await db.commit()
                raise PermanentTaskError(
                    "勾选的收藏夹没有归属记录：本次未入库任何文件。"
                    "请改用「先扫描收藏夹 → 下载勾选的夹」重新生成归属清单。"
                )
    to_import = await _scan_and_plan_stage(
        db,
        task,
        opts,
        bloggers,
        plan_authors,
        limit,
        skip_live,
        fetch_mode,
        fetch_summary,
        collect_scope=collect_scope if fetch_mode == "collection" else None,
        allowed_aweme_ids=allowed_aweme_ids,
    )
    # 合集补齐的范围（仅收藏模式）：本次扫描到的作品 ∪ 本次枚举到但被 F 跳过（已在库）的作品
    collect_extras: list[str] = []
    # 抖音收藏夹归属（作品 ID → 收藏夹名）：来自按夹下载写的清单；平铺下载时为空
    collect_folder_map: dict[str, list[str]] = {}
    if fetch_mode == "collection":
        collect_extras = list(collect_scope.get("aweme_ids") or [])
        collect_extras += list(
            (fetch_summary.get("collect") or {}).get("skipped_existing_ids") or []
        )
        # 二级归属：按「作品 ID → 收藏夹名」聚合（限定范围时只保留**勾选**的夹，
        # 否则同一作品同时属于勾选夹与未勾选夹时，会给没勾的夹也建出一个二级）
        if allowed_aweme_ids is None:
            collect_folder_map = load_collect_folder_map(_personal_scan_root(f2, fetch_mode))
        else:
            selected_folder_ids = {str(c) for c in collect_ids}
            collect_folder_map = {
                entry["name"]: sorted(set(entry["aweme_ids"]) & allowed_aweme_ids)
                for folder_id, entry in folder_entries.items()
                if folder_id in selected_folder_ids
                and entry["name"]
                and (set(entry["aweme_ids"]) & allowed_aweme_ids)
            }
    if not to_import:
        # 本批没有要入库的文件，但收藏模式仍要把「已在库」的部分补进合集，
        # 否则零新增的那几轮合集永远不更新
        if collect_extras or collect_folder_map:
            await _aggregate_collect_stage(
                db,
                task,
                {"ids": []},
                extra_ids=await collect_library_ids_by_aweme(db, collect_extras),
                folder_map=collect_folder_map,
            )
        return

    # ── 阶段 3：入库（同步 sqlite 放线程；进度回调 + 状态检查）──
    result, holder = await _apply_import_stage(
        db, task, to_import, make_thumbnails, status_of=_current_status
    )

    status_now = await _current_status(db, task.id)
    interrupted = status_now in ("cancelled", "paused")

    # ── 阶段 3b：「我的列表」补登记来源作者博主 ──
    bloggers_stats = await _register_personal_bloggers_stage(
        db, task, result, personal_mode, register_bloggers
    )

    # ── 阶段 3c：「我的收藏」按抖音收藏夹归位到两级收藏夹 ──
    if fetch_mode == "collection":
        # 合集 = 本批新入库的 + 本次扫描/枚举到但已在库的（E：否则「先喜欢后收藏」
        # 的作品永远进不了合集）；二级归属来自按夹下载写的清单（P）
        await _aggregate_collect_stage(
            db,
            task,
            result,
            extra_ids=await collect_library_ids_by_aweme(db, collect_extras),
            folder_map=collect_folder_map,
        )

    # ── 收尾 ──
    await _finalize_import(
        db, task, result, holder, bloggers_stats, interrupted, status_now
    )


# 原样再导出（切片 B）：这些符号的函数体已搬到 f2_personal.py，这里保留同名绑定，
# 调用方（task_runner.py、routers、tests）与既有的 `f2_import.xxx` 用法不变。
# E402：放在文末是为了让「搬走的块」原位置留空，不与阶段函数交错阅读。
from app.services.task_runners.f2_personal import (  # noqa: E402, F401
    DOUYIN_AUTO_SOURCE,
    DOUYIN_ROOT_COLLECTION_NAME,
    PERSONAL_FETCH_MODES,
    UNCLASSIFIED_COLLECTION_NAME,
    _append_notice,
    _brief_reason,
    _personal_label,
    _personal_scan_root,
    ensure_collection,
    load_collect_folder_entries,
    load_collect_folder_map,
    personal_registers_bloggers,
)

# 原样再导出（切片 D）：取数阶段的函数体已搬到 f2_fetch_stages.py，这里保留同名绑定，
# 调用方（execute_f2_import、tests）与既有的 `f2_import.xxx` 用法不变。
# E402：统一放在文末，与切片 B 的再导出集中成一段，避免与阶段函数交错阅读。
from app.services.task_runners.f2_fetch_stages import (  # noqa: E402, F401
    _collect_progress,
    _collect_progress_pct,
    _fetch_collects_by_folder_stage,
    _fetch_personal_stage,
    _fetch_posts_stage,
    _resolve_named_profile_keys,
    _select_post_targets,
)


# 原样再导出（切片 A）：库内作品 ID 索引，与下方各切片的再导出集中成一段。
# E402：再导出统一放在文末——模块级只需在**调用时**解析，放在这里不影响任何调用方。
from app.services.task_runners.f2_library_ids import (  # noqa: E402, F401
    _EXTRA_ID_CHUNK,
    collect_library_ids_by_aweme,
    known_library_aweme_ids,
)


# 原样再导出（切片 E）：计划/入库/登记阶段的函数体已搬到 f2_plan_apply.py。
from app.services.task_runners.f2_plan_apply import (  # noqa: E402, F401
    _apply_import_stage,
    _register_personal_bloggers_stage,
    _scan_and_plan_stage,
)


# 原样再导出（切片 F）：收尾阶段的函数体已搬到 f2_finalize.py。
from app.services.task_runners.f2_finalize import (  # noqa: E402, F401
    _aggregate_collect_stage,
    _finalize_import,
)
