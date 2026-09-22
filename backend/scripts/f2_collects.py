"""抖音「收藏夹」枚举与按夹下载（先扫描、后下载）。

为什么需要这个模块
------------------
f2 的 `-M collection`（平铺收藏）会把**所有**收藏作品都下下来，包括用户建在收藏夹
里的那些——实测：31 个收藏夹共约 2000 件，其中「股票 / 马克思 / 哲学 / 历史」这类
1 件的夹也照下不误。用户要的是**先看清单、勾掉不想要的夹、只下勾选的**。

f2 自带的 `-M collects`（收藏夹模式）虽然能按夹下，但它的选夹是**交互式**的
（`handler.select_user_collects` 里 `rich_prompt.ask` 让人手输序号）——worker 是
非交互子进程，喂不了。所以这里用 f2 的**内部接口**自己走：

1. `collects/list/`             → 收藏夹清单（夹名 / 夹 ID / 夹内作品数 / 最近收藏时间）
2. `collects/video/list/`       → 某个夹的作品列表（分页，只取元数据）
3. `DouyinHandler.downloader`   → 把选中的作品交给 f2 自己的下载器落盘

这样做的两个附带好处：进度分母是真的（夹的 `total_number` 已知，不再靠耗时估），
而且**每页之间可中断**（暂停/取消能在下载中途真正生效，不用等整条命令跑完）。

⚠ Cookie 口径（踩坑，别再踩）
----------------------------
必须**整份 YAML 解析** f2 配置里的 cookie（实测 4827 字符、57 个字段）。早期用正则
从 app.yaml 里截 `cookie:` 那一行只拿到 172 字符（只有 `UIFID_TEMP`），残缺登录态下
收藏类接口**返回「状态码 200 但内容为空」**——看起来像风控/并发，其实是 Cookie 残缺。
"""

import asyncio
import sys
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path，便于模块方式执行
sys.path.insert(0, str(Path(__file__).parent.parent))

from .f2_common import COLLECT_NAMING_TEMPLATE, DEFAULT_F2_DIR  # noqa: E402

"""翻页间隔（秒）：收藏夹接口与作品列表接口都自带风控，别打太快。"""
COLLECTS_PAGE_SLEEP = 2.0

"""收藏夹清单每页取多少个夹（接口的分页粒度，与作品列表的 page_counts 不同）。"""
COLLECTS_FOLDER_PAGE_COUNTS = 20

"""收藏夹清单最多翻几页（默认 5 页 = 100 个夹）——正常人的收藏夹不会超过这个量级。

⚠ 达到上限会**静默截断**：只影响清单完整度（少列的夹不会出现在勾选列表里），
不会下错东西；真碰到 100+ 个夹再把这个值调大。
"""
COLLECTS_FOLDER_PAGE_LIMIT = 5

"""每页取多少个作品（f2 帮助里建议不超过 20）。"""
COLLECTS_WORKS_PAGE_COUNTS = 20


def load_f2_runtime(f2_dir: Path | str | None = None) -> tuple[dict, str]:
    """装配调用 f2 接口所需的 kwargs（与 f2 CLI 同口径）。

    Args:
        f2_dir: f2 工作目录（内含 `f2/` 包与 `conf/app.yaml`）。

    Returns:
        ``(kwargs, cookie_source)``；``kwargs`` 可直接交给 `DouyinCrawler` /
        `DouyinHandler`，``cookie_source`` 是配置来源（供界面/日志说明）。

    Raises:
        RuntimeError: 没找到 f2 或配置里没有 Cookie。
    """
    from .report_f2_id_alignment import ensure_clone_f2_on_path

    root = Path(f2_dir or DEFAULT_F2_DIR)
    # ⚠ 必须在导入 f2 之前插路径：site-packages 那份是 2024-12-31 的旧版，
    # 抖音签名已失效（详见 ensure_clone_f2_on_path）
    pkg_file = ensure_clone_f2_on_path(root)

    import f2 as f2_pkg  # noqa: PLC0415 —— 必须在插路径之后导入
    from f2.apps.douyin.utils import ClientConfManager
    from f2.utils.conf_manager import ConfigManager

    conf_path = Path(pkg_file or f2_pkg.__file__).parent / "conf" / "app.yaml"
    conf = ConfigManager(str(conf_path)).get_config("douyin") or {}
    cookie = str(conf.get("cookie") or "")
    if not cookie:
        raise RuntimeError(
            f"f2 配置里没有 Cookie（{conf_path}）：收藏列表只有本人可见，"
            "请先手动跑一次 f2 完成登录（或更新配置里的 cookie）"
        )

    return (
        {
            "cookie": cookie,
            "headers": {
                "User-Agent": ClientConfManager.user_agent(),
                "Referer": ClientConfManager.referer(),
            },
            "proxies": {"http://": None, "https://": None},
            "timeout": int(conf.get("timeout") or 10),
            "max_tasks": int(conf.get("max_tasks") or 5),
            "max_connections": int(conf.get("max_connections") or 5),
            "max_retries": int(conf.get("max_retries") or 5),
            "app_name": "douyin",
            # 产物落点与 f2 CLI 完全一致（`-p <f2 工作目录>/Download`）：
            # {path}/douyin/{mode}/{我的昵称}/。⚠ 必须绝对路径——配置里那个相对
            # "Download" 会被解析到**后端进程的 cwd**，文件就下错地方了。
            "path": str(root / "Download"),
            "page_counts": COLLECTS_WORKS_PAGE_COUNTS,
        },
        str(conf_path),
    )


async def _list_folders_async(kwargs: dict, page_limit: int) -> list[dict]:
    """翻页取收藏夹清单（只读元数据）。"""
    from f2.apps.douyin.crawler import DouyinCrawler
    from f2.apps.douyin.filter import UserCollectsFilter
    from f2.apps.douyin.model import UserCollects

    folders: list[dict] = []
    cursor = 0
    for page_index in range(max(1, page_limit)):
        async with DouyinCrawler(kwargs) as crawler:
            response = await crawler.fetch_user_collects(
                UserCollects(cursor=cursor, count=COLLECTS_FOLDER_PAGE_COUNTS)
            )
        page = UserCollectsFilter(response or {})
        ids = page.collects_id or []
        if not ids:
            break
        names = page.collects_name or []
        totals = page.total_number or []
        for i, collects_id in enumerate(ids):
            folders.append(
                {
                    "id": str(collects_id),
                    "name": names[i] if i < len(names) else "",
                    "total": int(totals[i]) if i < len(totals) and totals[i] else 0,
                }
            )
        if not page.has_more:
            break
        cursor = page.max_cursor
        if page_index + 1 < page_limit:
            await asyncio.sleep(COLLECTS_PAGE_SLEEP)
    return folders


def list_collect_folders(
    f2_dir: Path | str | None = None,
    page_limit: int = COLLECTS_FOLDER_PAGE_LIMIT,
) -> dict:
    """列出收藏夹（夹名 / 夹 ID / 夹内作品数），**只读、不下载任何媒体**。

    收藏夹清单只翻 1~2 页（每页 20 个夹），一次调用通常 2~5 秒，可以同步接口直接返回。

    Args:
        f2_dir: f2 工作目录。
        page_limit: 最多翻几页。

    Returns:
        ``{"folders": [{"id", "name", "total"}, ...],
        "total_folders": int, "total_works": int, "cookie_source": str}``
        ``total_works`` 是各夹 ``total`` 之和（含跨夹重复，仅供量级参考）。

    为什么不含「最近收藏时间」：接口在夹没被收藏过时会给 f2 过滤器一个空值，过滤器
    把它格式化成字面量 ``"Invalid timestamp"``（垃圾串）；而这个字段目前没有消费方
    （夹的排序由接口自己保证最新在前），留着只会带来一份需要清洗的数据。
    """
    kwargs, cookie_source = load_f2_runtime(f2_dir)
    folders = asyncio.run(_list_folders_async(kwargs, page_limit))
    return {
        "folders": folders,
        "total_folders": len(folders),
        "total_works": sum(int(f["total"] or 0) for f in folders),
        "cookie_source": cookie_source,
    }


async def _iter_folder_works(
    handler,
    collects_id: str,
    max_counts: int,
    should_stop,
    on_page,
) -> int:
    """把一个收藏夹的作品**逐页**交给调用方处理，返回处理过的作品数。

    Args:
        handler: `DouyinHandler`（已构造，含 downloader）。
        collects_id: 收藏夹 ID。
        max_counts: 该夹最多处理多少件（0=全量）。
        should_stop: 无参可调用对象，返回 True 时停止（暂停/取消）。
        on_page: ``on_page(aweme_list) -> None``（同步或协程），拿到一页作品。

    Returns:
        已处理的作品数（``should_stop`` 触发时是提前停止的那个数）。
    """
    processed = 0
    async for page in handler.fetch_user_collects_videos(
        collects_id, 0, COLLECTS_WORKS_PAGE_COUNTS, max_counts or None
    ):
        if should_stop():
            break
        aweme_list = page._to_list()
        if not aweme_list:
            continue
        result = on_page(aweme_list)
        if asyncio.iscoroutine(result):
            await result
        processed += len(aweme_list)
        if should_stop():
            break
    return processed


async def _download_folders_async(
    f2_dir: Path | str | None,
    user: str,
    collect_ids: list[str],
    max_counts: int,
    should_stop,
    on_progress,
) -> dict:
    """按选中的收藏夹下载（本体；由 :func:`download_collect_folders` 包 asyncio.run）。"""
    from f2.apps.douyin.handler import DouyinHandler
    from f2.apps.douyin.utils import SecUserIdFetcher, create_user_folder

    from .f2_fetch import like_user_url

    kwargs, cookie_source = load_f2_runtime(f2_dir)
    kwargs["mode"] = "collection"  # 决定产物目录 {path}/douyin/collection/{我的昵称}/
    kwargs["naming"] = COLLECT_NAMING_TEMPLATE
    kwargs["folderize"] = False  # 不按夹分子目录：扫描/入库只认「我的昵称」这一层
    kwargs["interval"] = "all"  # 收藏模式不读日期窗口，显式给值只是避免 f2 的空参告警
    # 与 CLI 路径产出**完全一致**：不下封面/文案/原声（这三样要么不入库、要么会
    # 让「封面」被当成一张独立素材进库，和现有采集口径不符）
    kwargs["music"] = False
    kwargs["cover"] = False
    kwargs["desc"] = False

    wanted = {str(c) for c in collect_ids}
    folders = [
        f for f in await _list_folders_async(kwargs, COLLECTS_FOLDER_PAGE_LIMIT)
        if f["id"] in wanted
    ]
    missing = sorted(wanted - {f["id"] for f in folders})

    sec_user_id = await SecUserIdFetcher.get_sec_user_id(like_user_url(user))
    handler = DouyinHandler(kwargs)
    # 不写 f2 的用户库：那是 f2 翻页断点用的簿记，我们整批自己记；而它的库名是
    # 相对路径（`douyin_users.db`），在后端进程里会落到 backend/ 下、还会和 worker
    # 抢锁——直接摘掉这一步。
    async def _skip_save(*_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    handler.downloader.save_last_aweme_id = _skip_save

    profile = await handler.fetch_user_profile(sec_user_id)
    user_path = create_user_folder(kwargs, profile.nickname)

    stats = {
        "cookie_source": cookie_source,
        "root": str(user_path),
        "folders": [],
        "works": 0,
        # 选中的夹作品总数 = 进度条的真分母（各夹 total_number 之和）
        "total_works": sum(int(f["total"] or 0) for f in folders),
        "current": {},
        "stopped": False,
        "missing_folders": missing,
    }
    if on_progress is not None:
        on_progress(_snapshot(stats))

    async def _download_page(aweme_list: list) -> None:
        await handler.downloader.create_download_tasks(kwargs, aweme_list, user_path)

    for index, folder in enumerate(folders, 1):
        if should_stop():
            stats["stopped"] = True
            break
        stats["current"] = {"id": folder["id"], "name": folder["name"], "index": index}
        done = await _iter_folder_works(
            handler,
            folder["id"],
            max_counts,
            should_stop,
            _download_page,
        )
        stats["works"] += done
        stats["folders"].append(
            {"id": folder["id"], "name": folder["name"], "total": folder["total"], "works": done}
        )
        if on_progress is not None:
            on_progress(_snapshot(stats))

    return stats


def _snapshot(stats: dict) -> dict:
    """给回调的进度**快照**（拷贝嵌套结构）。

    为什么必须拷贝：``on_progress`` 是在下载线程里被调用的，而消费者（任务执行器的
    watcher 协程）会把这个结构写进任务结果并落库。若直接传内部结构，watcher 会在
    「另一个线程正在往 folders 里 append」的同时序列化它——轻则读到半截列表，
    重则序列化过程中列表变长（跨线程共享可变对象），所以这里一律传副本。
    """
    return {
        **stats,
        "folders": [dict(f) for f in stats.get("folders") or []],
        "current": dict(stats.get("current") or {}),
        "missing_folders": list(stats.get("missing_folders") or []),
    }


def _collect_naming() -> str:
    """收藏模式的命名模板（与 :data:`f2_common.COLLECT_NAMING_TEMPLATE` 同一份）。"""
    return COLLECT_NAMING_TEMPLATE


def download_collect_folders(
    f2_dir: Path | str | None,
    user: str,
    collect_ids: list[str],
    max_counts: int = 0,
    should_stop=None,
    on_progress=None,
) -> dict:
    """只下载**选中的收藏夹**里的作品（先扫描、后下载里的「下载」这一步）。

    与平铺 `-M collection` 的区别：作品清单来自 `collects/video/list/`（按夹），
    因此**没被选中的夹一件都不会下载**。文件落点、命名模板、跳过规则与 f2 CLI
    完全一致，故下游（跨模式合并 → 扫描 → 五层判重 → 入库）无需任何改动。

    Args:
        f2_dir: f2 工作目录。
        user: 「我的主页链接 / sec_user_id」。
        collect_ids: 选中的收藏夹 ID 列表（空列表等于什么都不下）。
        max_counts: **每个夹**最多取最近多少件（0=该夹全量）。
        should_stop: 无参可调用对象，返回 True 时停止（暂停/取消中途生效）。
        on_progress: ``on_progress(stats) -> None``，每处理完一个夹回调一次。

    Returns:
        ``{"cookie_source", "root", "folders": [{"id","name","total","works"}],
        "works": int, "total_works": int, "current": dict, "stopped": bool,
        "missing_folders": [...]}``
    """
    stop = should_stop or (lambda: False)
    if not collect_ids:
        # 与真路径同形：调用方无需为「没勾任何夹」写分支
        return {
            "cookie_source": "",
            "root": "",
            "folders": [],
            "works": 0,
            "total_works": 0,
            "current": {},
            "stopped": False,
            "missing_folders": [],
        }
    return asyncio.run(
        _download_folders_async(
            f2_dir, user, [str(c) for c in collect_ids], max_counts, stop, on_progress
        )
    )
