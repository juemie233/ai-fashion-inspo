"""独立爬虫执行脚本 — CDP 连接用户真实 Chrome，零检测采集。

调用方式::

    cd backend && python -m scripts.run_scraper <task_id>

（以模块方式启动：脚本族已拆分为 scripts/ 包内模块，相对导入依赖包上下文）

功能概览::

    1. 支持小红书（CDP 真实 Chrome）与抖音（CDP / 独立 Playwright）双平台
    2. 关键词搜索模式（带断点续采、排序切换）
    3. 按博主采集模式（主页作品 → 详情页全量提取）
    4. 图片/视频即时下载入库（URL 去重 + 内容 MD5 去重 + 墓碑表去重）
    5. 话题标签自动提取与存档（scraper_hashtags）

架构::

    - 常量定义 / 通用工具 / 登录检测 ── scraper_common
    - 下载模块 / 话题存档 / 缩略图 ── scraper_download
    - 小红书模块 ── scraper_xhs（搜索 / 博主采集 / 详情页提取）
    - 抖音模块 ── scraper_douyin（RENDER_DATA / DOM 提取 / URL 收集 / 管线）
    - 任务执行 ── 本文件仅含入口函数 run_scraper_sync() + _update_task_sync()

.. note::
    本文件作为独立进程运行，通过同步 sqlite3 避免与 Playwright 事件循环冲突。
    数据库迁移由主库 Alembic 负责，本脚本仅做兜底建表。
"""

# ── 模块导入 ──
# 抖音函数由 scraper_douyin 提供（常量已在 scraper_common 中统一）
from .scraper_common import (
    SORT_TYPES,
    utcnow,
    _rdsleep,
    _human_mouse_move,
    ensure_platform_login,
    ScraperBlockedError,
)
from .scraper_download import (
    download_batch,
)
from .scraper_xhs import (
    search_xiaohongshu,
    run_blogger_mode,
)
from .scraper_douyin import (
    collect_douyin_detail_urls,
    collect_douyin_search_urls,
    run_douyin_notes_pipeline,
    resolve_douyin_profile_url,
)

import argparse
from dataclasses import dataclass
import json
import random
import sqlite3 as _sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.database import async_session, init_db
from app.db_migrations import ensure_schema
from app.models.scraper import ScraperTask

# ── UTF-8 输出 ──
# 注意：必须开启 line_buffering，否则 stdout 被重新包装成带缓冲的 TextIOWrapper，
# print 进度日志会一直积压在缓冲区，直到进程退出才落盘，导致日志看起来「卡住不动」。
# 仅在作为主脚本执行时包装：本文件会被测试通过 importlib 导入以验证纯函数，
# 此时不能触碰宿主进程的标准流（否则破坏 pytest 捕获）。
import io
if __name__ == "__main__":
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  任务执行主入口
# ═══════════════════════════════════════════════════════════════


def _update_task_sync(task_id: int, fields: dict) -> None:
    """用同步 sqlite3 更新采集任务字段，规避与 Playwright 同步 API 的事件循环冲突。

    小红书采集阶段，Playwright 的 sync API 在后台 greenlet 中运行自己的事件循环，
    此时在主线程调用 ``loop.run_until_complete`` 会抛
    "Cannot run the event loop while another loop is running"。
    因此任务进度（断点 / 状态 / 错误 / 完成标记）统一走同步 sqlite3，
    与 :func:`download_batch` 的写库思路保持一致。

    Args:
        task_id: 采集任务主键。
        fields: 需要更新的列名到新值的映射（列名仅由调用方常量传入，无注入风险）。
    """
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = [*fields.values(), task_id]
    db_path = settings.db_file_path
    conn = _sqlite3.connect(str(db_path))
    try:
        conn.execute(f"UPDATE scraper_tasks SET {sets} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


@dataclass(frozen=True)
class TaskConfig:
    """采集任务的配置快照（从 ``scraper_tasks.config`` JSON 解析出来的最小集合）。

    单独建模的原因：这段解析原本内联在 700+ 行的 ``run_scraper_sync`` 里，校验失败
    只靠 print + return，既测不到也看不清「缺什么会退出」。抽出来后可单测，
    且错误文案（前端/日志都在看）保持一字不改。
    """

    keywords: list[str]
    max_count: int
    platform: str
    is_blogger: bool
    blogger_id: int | None


def parse_task_config(config: dict, platform: str) -> TaskConfig:
    """解析采集配置并做最小校验（不合法直接抛 ``ValueError``，文案面向用户）。

    Args:
        config: ``scraper_tasks.config`` 解析出的 dict（可能为空）。
        platform: 任务平台（``xiaohongshu`` / ``douyin``）。

    Returns:
        :class:`TaskConfig`。

    Raises:
        ValueError: 按博主采集缺 ``blogger_id``，或搜索采集没有关键词。
    """
    keywords = [k.strip() for k in config.get("keywords", []) if k.strip()]
    max_count = int(config.get("max_count", 50))
    # 采集模式：search 关键词搜索（默认）| user 按博主采集（小红书）
    mode = config.get("collect_mode") or config.get("mode") or "search"
    is_blogger = mode == "user"
    blogger_id: int | None = None
    if is_blogger:
        raw_bid = config.get("blogger_id")
        if not raw_bid:
            raise ValueError("按博主采集缺少 blogger_id")
        blogger_id = int(raw_bid)
    if not keywords and not is_blogger:
        raise ValueError("无关键词")
    return TaskConfig(
        keywords=keywords,
        max_count=max_count,
        platform=platform,
        is_blogger=is_blogger,
        blogger_id=blogger_id,
    )


def format_funnel_summary(
    *,
    items_found: int,
    skipped_existing: int,
    skipped_content_dup: int,
    skipped_non200: int,
    skipped_network: int,
    skipped_video: int,
    items_added: int,
) -> str:
    """把采集漏斗渲染成定宽文本框（纯函数：可单测，也避免报告里数字与统计脱节）。"""
    return "\n".join(
        (
            "",
            "  ╔══════════════════════════════════════════",
            "  ║ 采集漏斗汇总",
            "  ╠══════════════════════════════════════════",
            f"  ║ 搜索提取总数:   {items_found}",
            f"  ║ 跨次已存在:     {skipped_existing}",
            f"  ║ 内容MD5重复:    {skipped_content_dup}",
            f"  ║ HTTP 非 200:    {skipped_non200}",
            f"  ║ 网络失败:       {skipped_network}",
            f"  ║ 视频跳过:       {skipped_video}",
            f"  ║ ★ 最终入库:     {items_added}",
            "  ╚══════════════════════════════════════════",
        )
    )


@dataclass(frozen=True)
class SearchPlan:
    """一次采集的执行计划（关键词 × 排序）与断点计数。

    ``resumed`` 只用于日志：告诉用户「这是接着上次跑的」，不参与任何逻辑判断，
    避免把「是否恢复」混进计划本身。
    """

    plan: list[dict]
    done: int
    items_found: int
    items_added: int
    resumed: bool = False


def build_search_plan(
    *,
    keywords: list[str],
    platform: str,
    config: dict,
    is_blogger: bool,
    resume: dict | None,
) -> SearchPlan:
    """构建（或从 resume_token 恢复）搜索执行计划。

    三支语义（与改造前逐行一致）：

    1. **按博主采集**：不走关键词计划，一轮完成 → 空计划；
    2. **有断点**（且计划非空）：原样恢复 plan/done/累计计数，保证跨重启顺序确定；
    3. **首次运行**：关键词**打乱一次**后按平台展开——
       抖音网页版不支持排序切换，固定综合排序单组合；
       小红书按 ``sort_mode`` 展开：latest/popular/general 各对应一种排序，
       **默认（未配置）只跑综合**；只有配置了未知取值时才回退到 ``SORT_TYPES``
       （三种全跑）。

    Args:
        keywords: 已清洗的关键词列表。
        platform: ``xiaohongshu`` / ``douyin``。
        config: 任务配置 dict（读 ``sort_mode``）。
        is_blogger: 是否按博主采集。
        resume: 解析后的 resume_token（可为 None）。

    Returns:
        :class:`SearchPlan`；``resumed`` 为 True 表示沿用了断点。
    """
    if is_blogger:
        return SearchPlan(plan=[], done=0, items_found=0, items_added=0)

    if resume and isinstance(resume.get("plan"), list) and resume["plan"]:
        return SearchPlan(
            plan=resume["plan"],
            done=int(resume.get("done", 0)),
            items_found=int(resume.get("items_found", 0)),
            items_added=int(resume.get("items_added", 0)),
            resumed=True,
        )

    shuffled = list(keywords)
    random.shuffle(shuffled)
    if platform == "douyin":
        # 抖音网页版不支持排序切换：固定综合排序单组合
        plan = [{"k": kw, "s": "general"} for kw in shuffled]
    else:
        sort_mode = config.get("sort_mode") or "general"
        sorts = {
            "latest": ["time_descending"],
            "popular": ["popularity_descending"],
            "general": ["general"],
        }.get(sort_mode, SORT_TYPES)
        plan = [{"k": kw, "s": s} for kw in shuffled for s in sorts]
    return SearchPlan(plan=plan, done=0, items_found=0, items_added=0)


def diagnose_empty_result(
    *,
    platform: str,
    items_found: int,
    per_search: list[dict],
    login_ok: bool,
) -> str | None:
    """颗粒无收时给出可读的失败原因（纯函数；有收获或没有搜索明细就返回 None）。

    三条口径（与改造前的内联实现逐字一致）：

    - 搜索明细里带 error → 拼成「A | B」并截断到 500 字；
    - 其中抖音 + 未登录 + 「导航失败」→ 补一句**可操作**指引（真实案例：任务 #43，
      只写「导航失败」让人无从下手）；
    - 抖音无显式 error 但提取为 0 → 只陈述事实与排查方向，不臆测原因。

    为什么抽出来：这段文案会直接进 `scraper_tasks.error` 并在前端任务详情展示，
    「登录墙」那句尤其关键——它决定用户下一步是去登录还是去查选择器。
    """
    if items_found != 0 or not per_search:
        return None

    errors = [s.get("error", "") for s in per_search if s.get("error")]
    if errors:
        error_msg = " | ".join(errors)[:500]
        # 抖音未登录时导航失败几乎必然是登录墙：给出可操作指引
        if platform == "douyin" and not login_ok and "导航失败" in error_msg:
            error_msg = f"{error_msg}（检测到抖音未登录：请在调试 Chrome 中登录抖音后重试）"[:500]
        return error_msg

    if platform == "douyin":
        # 搜索路径的所有失败模式均已带明确漏斗 error，此处仅为兜底
        return "抖音采集提取 0 个作品链接（具体原因见任务日志与漏斗明细）"[:500]
    return None


def run_scraper_sync(task_id: int):
    """执行采集任务的同步入口函数。

    这是本脚本的唯一公开入口，由 ``if __name__ == "__main__"`` 块调用。
    负责：初始化数据库、加载任务、解析配置、启动浏览器、执行采集、保存进度、
    更新任务状态、输出漏斗汇总。

    Args:
        task_id: 采集任务主键 ID。

    Returns:
        None
    """
    from playwright.sync_api import sync_playwright
    import asyncio

    # 单事件循环贯穿整个脚本：SQLAlchemy 连接池中的连接绑定创建它们的 loop，
    # 若反复 asyncio.run() 新建/关闭 loop，跨 loop 复用连接会间歇性报
    # "attached to a different loop"。子进程生命周期内只建一个 loop，
    # 进程退出时由操作系统回收。
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # ── 确保表结构与字段最新（独立脚本不经过服务端 lifespan）──
    loop.run_until_complete(init_db())
    loop.run_until_complete(ensure_schema())

    # ── 加载任务 ──
    async def _load():
        async with async_session() as db:
            return await db.get(ScraperTask, task_id)

    task = loop.run_until_complete(_load())
    if not task or task.status in ("completed", "cancelled"):
        print(f"任务 {task_id} 已完结，跳过")
        return

    # ── 设为运行中 ──
    async def _run():
        async with async_session() as db:
            t = await db.get(ScraperTask, task_id)
            if t:
                t.status = "running"
                t.started_at = utcnow()
                await db.commit()

    loop.run_until_complete(_run())

    # ── 标记任务失败（复用：配置异常与采集异常都会调用）──
    def _fail(reason: str):
        """标记任务失败（同步写库，规避与 Playwright 同步 API 的事件循环冲突）。

        Args:
            reason: 失败原因描述。
        """
        _update_task_sync(
            task_id,
            {
                "status": "failed",
                "error": reason[:500],
                "finished_at": utcnow().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            },
        )

    # ── 解析配置（异常时标记失败，避免任务卡在 running）──
    try:
        config = (
            json.loads(task.config)
            if isinstance(task.config, str)
            else task.config or {}
        )
        try:
            parsed = parse_task_config(config, task.platform)
        except ValueError as e:
            # 两种「配置不合格」保留原有的具体提示与失败原因（日志/前端都在看）
            print(f"{e}，退出")
            _fail(str(e))
            return
        keywords = parsed.keywords
        max_count = parsed.max_count
        platform = parsed.platform
        is_blogger = parsed.is_blogger
        blogger_id = parsed.blogger_id

        # 准备下载目录（图片与视频目录统一创建：抖音搜索模式同样会下载视频）
        today = utcnow().strftime("%Y-%m")
        img_dir = settings.images_dir / today
        img_dir.mkdir(parents=True, exist_ok=True)
        videos_dir = settings.videos_dir / today
        videos_dir.mkdir(parents=True, exist_ok=True)
        import httpx
    except Exception as e:
        err = str(e) or type(e).__name__
        print(f"配置解析失败: {err}")
        _fail(f"配置解析失败: {err}")
        return

    # ── 断点续采：构建或恢复执行计划（关键词 × 排序） ──
    # 首次运行：随机打乱关键词一次后展开 ×3 排序，计划随 resume_token 持久化，保证跨重启顺序确定。
    # 按博主采集不走关键词计划（一轮完成，见下方独立执行分支）。
    resume = None
    if task.resume_token:
        try:
            resume = json.loads(task.resume_token)
        except Exception:
            resume = None

    plan_state = build_search_plan(
        keywords=keywords,
        platform=platform,
        config=config,
        is_blogger=is_blogger,
        resume=resume,
    )
    plan = plan_state.plan
    done = plan_state.done
    items_found = plan_state.items_found
    items_added = plan_state.items_added
    if plan_state.resumed:
        print(
            f"断点续采：从第 {done}/{len(plan)} 个组合继续"
            f"（已入库 {items_added}）"
        )

    existing_url_set: set[str] = set()  # 跨批次 URL 去重
    content_hash_set: set[str] = set()  # 跨批次内容 MD5 去重
    total_skipped_existing = 0
    total_skipped_content_dup = 0
    total_skipped_non200 = 0
    total_skipped_network = 0
    total_skipped_video = 0
    per_search: list[dict] = []  # 每次搜索的漏斗明细

    def _save_resume(done_idx: int):
        """持久化断点进度（计划 / 已完成数 / 累计计数）— 同步写库，避免事件循环冲突。

        Args:
            done_idx: 已完成计划项的索引。
        """
        token = json.dumps(
            {
                "plan": plan,
                "done": done_idx,
                "items_found": items_found,
                "items_added": items_added,
            },
            ensure_ascii=False,
        )
        _update_task_sync(task_id, {"resume_token": token})

    # 平台执行器：小红书走 CDP 真实 Chrome；抖音走独立 Playwright 浏览器（网页版无需 CDP）
    pw = None
    dy = None
    page = None

    def _search_douyin(
        keyword: str, need_count: int
    ) -> tuple[list[tuple[str, str]], dict]:
        """使用 DouyinScraper 在独立浏览器中搜索抖音网页版并提取图片 URL。

        Returns:
            (pairs, funnel_dict): (笔记页面 URL, 图片 CDN URL) 列表与该次搜索的漏斗统计
        """
        raw = loop.run_until_complete(
            dy.search(max(10, need_count * 2))
        )
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in raw:
            for img in item.image_urls or []:
                if img and img not in seen:
                    seen.add(img)
                    pairs.append((item.url or "", img))
        funnel: dict = {
            "cards_total": len(raw),
            "urls_extracted": len(pairs),
            "target": need_count,
        }
        if not raw:
            funnel[
                "error"
            ] = "抖音搜索无结果（网页版可能未登录或页面结构变化）"
        return pairs[: need_count * 2], funnel

    try:
        # ── CDP 通道判定 ──
        # 小红书固定走 CDP 真实 Chrome；抖音显式提供 cdp_port 时同样走 CDP
        # （完整采集：图集/视频/正文），未提供则回退独立 Playwright 浏览器旧路径。
        login_ok = True  # 未走登录检测的路径默认视为已登录（仅 CDP 流程可检测）
        use_cdp = (
            platform == "xiaohongshu"
            or (
                platform == "douyin"
                and bool(config.get("cdp_port"))
            )
        )
        if use_cdp:
            pw = sync_playwright().start()

            # ── 连接 CDP Chrome ──
            CDP_PORT = config.get("cdp_port") or 9222
            cdp_url = f"http://localhost:{CDP_PORT}"
            print(f"连接 CDP Chrome: {cdp_url}")

            try:
                browser = pw.chromium.connect_over_cdp(cdp_url)
            except Exception as e:
                chrome_exe = settings.chrome_executable
                data_dir = settings.chrome_user_data_dir
                raise RuntimeError(
                    f"无法连接 CDP Chrome (端口 {CDP_PORT}).\n"
                    f"请先用调试模式启动 Chrome:\n"
                    f'""{chrome_exe}" '
                    f"--remote-debugging-port={CDP_PORT} "
                    f'--user-data-dir="{data_dir}"'
                ) from e

            print(f"已连接 Chrome {browser.version}")
            context = browser.contexts[0]

            # 创建新标签页用于采集
            page = context.new_page()

            # ── 登录检查（小红书/抖音共用逻辑，差异在首页 URL 与会话 Cookie 名）──
            login_ok = ensure_platform_login(
                context, page, platform, timeout=180
            )
            if not login_ok:
                print("警告：平台未登录，未登录状态下采集大概率被登录墙拦截")

            # 提取浏览器 Cookie 用于 httpx 下载鉴权
            browser_cookies = {
                c["name"]: c
                for c in context.cookies()
            }
        else:
            from app.scrapers.douyin import DouyinScraper

            dy = DouyinScraper(
                headless=config.get("headless", True)
            )
            browser_cookies: dict = {}
            print(
                "抖音平台：未配置 CDP 端口，使用独立 Playwright"
                " 浏览器降级采集（仅封面图，建议开启 CDP 走完整通道）"
            )

        # ── 按博主采集：主页作品 → 详情页全量提取（多图/视频/正文/博主标记）──
        if is_blogger:
            if platform == "douyin":
                if page is None:
                    # 未走 CDP 时无法进博主主页逐篇采集，提前失败并给明确指引
                    _fail(
                        "抖音按博主采集需要 CDP Chrome："
                        "请在创建任务时开启 CDP 并登录抖音"
                    )
                    return
                profile_url = resolve_douyin_profile_url(config)
                max_notes_cfg = int(
                    config.get("max_notes", 50) or 50
                )
                note_urls, scroll_funnel = collect_douyin_detail_urls(
                    page,
                    profile_url,
                    max_notes_cfg,
                    max_scrolls=int(
                        config.get("max_scrolls", 15) or 15
                    ),
                )
                print(
                    f"抖音按博主采集：收集到 {len(note_urls)} 个作品"
                )
                per_search.append(
                    {"keyword": profile_url, **scroll_funnel}
                )
                items_found, items_added, blogger_notes, skip_stats = (
                    run_douyin_notes_pipeline(
                        page,
                        task_id,
                        note_urls,
                        budget=None,  # 博主模式以作品数为上限
                        img_dir=img_dir,
                        videos_dir=videos_dir,
                        today=today,
                        httpx_module=httpx,
                        browser_cookies=browser_cookies,
                        existing_url_set=existing_url_set,
                        content_hash_set=content_hash_set,
                        blogger_id=blogger_id,
                        download_video=bool(
                            config.get("download_video", True)
                        ),
                        source_kind="blogger",
                    )
                )
                total_skipped_existing += skip_stats["sk_ex"]
                total_skipped_content_dup += skip_stats["sk_dup"]
                total_skipped_non200 += skip_stats["sk_h"]
                total_skipped_network += skip_stats["sk_n"]
                total_skipped_video += skip_stats["v_skipped"]
            else:
                # 小红书按博主：主页笔记 → 详情页全量提取
                items_found, items_added, blogger_notes = (
                    run_blogger_mode(
                        page,
                        task_id,
                        blogger_id,
                        config,
                        img_dir,
                        videos_dir,
                        today,
                        httpx,
                        browser_cookies,
                        existing_url_set,
                        content_hash_set,
                    )
                )
            for n in blogger_notes:
                per_search.append(n)
            print(
                f"按博主采集完成：作品 {len(blogger_notes)} 条，"
                f"提取 {items_found}，入库 {items_added}"
            )
        else:
            # ── 搜索 + 即时下载：按执行计划（关键词 × 排序）逐项推进，支持断点续采 ──
            total_searches = len(plan)

        # 博主模式 plan 为空列表，此循环自然空转跳过（无需分支判断）
        for plan_idx in range(done, len(plan)):
            entry = plan[plan_idx]
            kw = entry["k"]
            sort_type = entry["s"]
            search_count = plan_idx + 1

            if items_added >= max_count:
                print(
                    f"\n  已入库 {items_added} 张 → 达到目标"
                    f" {max_count}，停止搜索"
                )
                done = len(plan)
                _save_resume(done)
                break

            print(f"\n{'='*50}")
            print(
                f"[搜索 {search_count}/{total_searches}] {kw}"
                f" [{sort_type}]"
            )
            print(f"{'='*50}")

            try:
                # 按剩余需求采集：够用即停，避免滚动浏览远超所需的内容
                remaining = max_count - items_added
                xhs_found = 0  # 小红书回调模式下逐批累计的「提取数」
                if platform == "xiaohongshu":

                    # 默认参数在「定义时」取值：显式绑定当前 cookies，
                    # 避免以后有人在循环内改 browser_cookies 时踩晚绑定（B023）
                    def _flush_xhs_batch(pairs, _cookies=browser_cookies) -> int:
                        """每轮把新增图片立即下载入库（部分成功语义）。

                        对齐 MediaCrawler 的分页回调落库：滚动过程中被风控
                        打断或进程被杀时，已抓到的图片不会白丢；返回本次实际
                        入库数，供搜索端判断是否已满足需求并提前停止滚动。
                        """
                        nonlocal items_added, xhs_found
                        nonlocal total_skipped_existing, total_skipped_content_dup
                        nonlocal total_skipped_non200, total_skipped_network
                        xhs_found += len(pairs)
                        b_added, b_ex, b_h, b_n, b_dup = download_batch(
                            pairs,
                            task_id,
                            existing_url_set,
                            max(1, max_count - items_added),
                            img_dir,
                            today,
                            httpx,
                            _cookies,
                            content_hash_set,
                            platform=platform,
                        )
                        items_added += b_added
                        total_skipped_existing += b_ex
                        total_skipped_content_dup += b_dup
                        total_skipped_non200 += b_h
                        total_skipped_network += b_n
                        print(
                            f"    分批入库 +{b_added}"
                            f"（累计 {items_added}/{max_count}）"
                        )
                        return b_added

                    urls, inner_funnel = search_xiaohongshu(
                        page, kw, remaining, sort_type, on_batch=_flush_xhs_batch
                    )
                elif platform == "douyin" and page is not None:
                    # 抖音 CDP 完整通道：首页搜索框 → 回车进入精选搜索
                    # （直连 /search/ URL 只渲染导航壳，实测静默风控）
                    # → 卡片收集 → 逐详情页提取（图集多图/视频/正文/#话题#）
                    note_urls, card_funnel = collect_douyin_search_urls(
                        page,
                        kw,
                        max_items=max(
                            10, int(remaining * 2)
                        ),
                    )
                    n_found, n_added, note_logs, skip_stats = (
                        run_douyin_notes_pipeline(
                            page,
                            task_id,
                            note_urls,
                            budget=remaining,
                            img_dir=img_dir,
                            videos_dir=videos_dir,
                            today=today,
                            httpx_module=httpx,
                            browser_cookies=browser_cookies,
                            existing_url_set=existing_url_set,
                            content_hash_set=content_hash_set,
                            blogger_id=None,
                            download_video=bool(
                                config.get(
                                    "download_video", True
                                )
                            ),
                            source_kind="search",
                        )
                    )
                    items_found += n_found
                    items_added += n_added
                    total_skipped_existing += skip_stats["sk_ex"]
                    total_skipped_content_dup += skip_stats["sk_dup"]
                    total_skipped_non200 += skip_stats["sk_h"]
                    total_skipped_network += skip_stats["sk_n"]
                    total_skipped_video += skip_stats["v_skipped"]
                    # 记录本次搜索的完整漏斗（明细截断最近 20 条防 diagnostics 膨胀）
                    per_search.append(
                        {
                            "keyword": kw,
                            "sort_type": sort_type,
                            **card_funnel,
                            "notes_opened": len(note_urls),
                            "batch_added": n_added,
                            "details": note_logs[-20:],
                        }
                    )
                    print(
                        f"  打开 {len(note_urls)} 个详情 → 入库 {n_added}"
                    )
                    print(
                        f"  累计入库: {items_added}/{max_count}"
                    )

                    done = plan_idx + 1
                    _save_resume(done)

                    # 搜索间冷却（CDP 保活）
                    if (
                        items_added < max_count
                        and plan_idx < len(plan) - 1
                    ):
                        cool = random.randint(6, 12)
                        print(f"  ⏸ 冷却 {cool}s...")
                        for _ in range(cool):
                            try:
                                if random.random() < 0.5:
                                    _human_mouse_move(page)
                                page.evaluate("1")
                            except Exception:
                                pass
                            _rdsleep(0.8, 1.5)
                    continue
                elif platform == "douyin":
                    urls, inner_funnel = _search_douyin(
                        kw, remaining
                    )
                else:
                    per_search.append(
                        {
                            "keyword": kw,
                            "sort_type": sort_type,
                            "error": f"不支持的平台: {platform}",
                        }
                    )
                    done = plan_idx + 1
                    _save_resume(done)
                    continue

                items_found += xhs_found + len(urls)
                print(f"  提取 {xhs_found + len(urls)} 个 URL")

                # 抖音降级通道：每次搜索后同步其浏览器 Cookie（用于 CDN 下载鉴权）
                if platform == "douyin" and dy is not None:
                    browser_cookies = dy.cookies()

                # 立即下载本批（带浏览器 Cookie；请求头按平台取 Referer）
                # 小红书在滚动过程中已逐批落库（on_batch），此处不再重复下载
                if platform == "xiaohongshu":
                    added = 0
                    sk_ex = sk_h = sk_n = sk_dup = 0
                else:
                    added, sk_ex, sk_h, sk_n, sk_dup = download_batch(
                        urls,
                        task_id,
                        existing_url_set,
                        remaining,
                        img_dir,
                        today,
                        httpx,
                        browser_cookies,
                        content_hash_set,
                        platform=platform,
                    )
                items_added += added
                total_skipped_existing += sk_ex
                total_skipped_content_dup += sk_dup
                total_skipped_non200 += sk_h
                total_skipped_network += sk_n

                # 记录本次搜索的完整漏斗
                # 小红书为逐批落库（inner_funnel 已带 batch_added），不覆盖为 0
                per_search.append(
                    {
                        "keyword": kw,
                        "sort_type": sort_type,
                        **inner_funnel,
                        "batch_added": added + int(
                            inner_funnel.get("batch_added", 0)
                        ),
                        "batch_skipped_existing": sk_ex,
                        "batch_skipped_content_dup": sk_dup,
                        "batch_skipped_http": sk_h,
                        "batch_skipped_network": sk_n,
                    }
                )

                print(
                    f"  本批入库: {added} (跳过: 已存在{sk_ex},"
                    f" MD5重复{sk_dup}, HTTP{sk_h}, 网络{sk_n})"
                )
                print(
                    f"  累计入库: {items_added}/{max_count}"
                )

                # 搜索间冷却 + CDP 保活（仅小红书有 CDP 页面）
                if (
                    items_added < max_count
                    and plan_idx < len(plan) - 1
                ):
                    cool = random.randint(6, 12)
                    print(f"  ⏸ 冷却 {cool}s...")
                    # 轻量页面交互保持 CDP 连接活跃（随机间隔 + 偶发鼠标移动）
                    for _ in range(cool):
                        if page is not None:
                            try:
                                if random.random() < 0.5:
                                    _human_mouse_move(page)
                                page.evaluate(
                                    "1"
                                )  # no-op，单纯保持连接
                            except Exception:
                                pass
                        _rdsleep(0.8, 1.5)

            except ScraperBlockedError as e:
                # 风控类错误不重试（MediaCrawler 实践：重试只会把限流升级为封号）：
                # 继续跑下一个关键词/排序组合同样会加重风控，故记录后立即停止整轮。
                # 断点停在本组合（不推进 done），下次重试仍从它开始，不漏采。
                err = str(e)
                per_search.append(
                    {
                        "keyword": kw,
                        "sort_type": sort_type,
                        "error": err,
                    }
                )
                print(f"  ⛔ 命中平台风控：{err}")
                _save_resume(plan_idx)
                _fail(
                    f"{err}；已停止本轮采集（风控类错误不重试）。"
                    "请在调试 Chrome 中完成验证或重新导入 Cookie 后重试"
                )
                return
            except Exception as e:
                err = str(e) or type(e).__name__
                per_search.append(
                    {
                        "keyword": kw,
                        "sort_type": sort_type,
                        "error": err,
                    }
                )
                print(f"  [ERR] {err}")

            # 每完成一个组合即持久化进度（成功或失败都推进，避免重复执行同一组合）
            done = plan_idx + 1
            _save_resume(done)

    except Exception as e:
        import traceback

        err = str(e) or type(e).__name__
        # 按博主采集路径无「关键词计划」可停，风控在这里冒泡：同样补上可操作指引
        if isinstance(e, ScraperBlockedError) and e.is_fatal:
            err = (
                f"{err}；已停止本轮采集（风控类错误不重试）。"
                "请在调试 Chrome 中完成验证或重新导入 Cookie 后重试"
            )
        print(f"采集失败: {err}")
        traceback.print_exc()
        _fail(err)
        return

    finally:
        # CDP 模式不关 Chrome；但本次采集新开的标签页要关闭——任务结束后
        # 残留的搜索页/详情页会在调试 Chrome 里越积越多（用户要求）。
        # page 仅在 CDP 模式由 context.new_page() 创建（非 CDP 降级路径
        # 始终为 None），关闭它不会影响用户自己打开的标签页。
        if page is not None:
            try:
                page.close()
                print("已关闭采集使用的浏览器标签页")
            except Exception:
                pass
        # Playwright 客户端与抖音独立浏览器正常回收
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        if dy is not None:
            try:
                loop.run_until_complete(dy.close())
            except Exception:
                pass

    # ── 组装持久化漏斗数据 ──
    funnel_diagnostics = json.dumps(
        {
            "per_search": per_search,
            "summary": {
                "total_found": items_found,
                "skipped_url_seen": total_skipped_existing,
                "skipped_content_dup": total_skipped_content_dup,
                "skipped_http_error": total_skipped_non200,
                "skipped_network_error": total_skipped_network,
                "skipped_video": total_skipped_video,
                "total_added": items_added,
            },
        },
        ensure_ascii=False,
    )

    # ── 汇总漏斗日志 ──
    print(
        format_funnel_summary(
            items_found=items_found,
            skipped_existing=total_skipped_existing,
            skipped_content_dup=total_skipped_content_dup,
            skipped_non200=total_skipped_non200,
            skipped_network=total_skipped_network,
            skipped_video=total_skipped_video,
            items_added=items_added,
        )
    )

    # ── 完成任务 ──
    error_msg = diagnose_empty_result(
        platform=platform,
        items_found=items_found,
        per_search=per_search,
        login_ok=login_ok,
    )

    def _done():
        """标记任务完成并写入漏斗诊断（同步写库，规避事件循环冲突）。"""
        fields = {
            "status": "completed",
            "items_found": items_found,
            "items_added": items_added,
            "diagnostics": funnel_diagnostics,
            "resume_token": None,  # 任务完结，清除断点进度
            "finished_at": utcnow().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
        if error_msg:
            fields["error"] = error_msg
        _update_task_sync(task_id, fields)

    _done()

    print(
        f"\n任务 {task_id} 完成: found={items_found}, added={items_added}"
    )
    if error_msg:
        print(f"诊断: {error_msg}")


if __name__ == "__main__":
    # 后端由 app/services/scraper/process.py 以 `-m scripts.run_scraper <task_id>` 拉起，
    # 位置参数与原来的裸索引完全等价；额外白得 --help 与「非数字 task_id」的明确报错
    parser = argparse.ArgumentParser(description="执行一条采集任务（后端以 -m 调起）")
    parser.add_argument("task_id", type=int, help="scraper_tasks.id")
    run_scraper_sync(parser.parse_args().task_id)
