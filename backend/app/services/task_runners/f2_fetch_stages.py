"""f2 导入的「取数阶段」：下载 + 收藏夹归位 + 发布模式选人/取数（切片 D）。

从 `task_runners/f2_import.py` 拆出的第四片（切片 B 之后）。这一块只负责**把作品弄到
本地**：两种「我的列表」下载（点赞 / 收藏，含按夹限定）、按收藏夹重组进程、发布模式
的目标作者选择与逐作者下载、点名博主的主页链接解析。入库（扫描/去重/写入）与收尾
结算不在本模块。

`_current_status` 在这里**转发**到 ``f2_import``（见该函数 docstring）：阶段函数读任务
状态必须走同一个 monkeypatch 点，否则测试里模拟的「取消 / 暂停」对搬家后的代码无效，
且失败是**静默**的（真读库返回 running，什么都不发生）。
"""

import asyncio
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskQueue
from app.services.task_runners.common import utcnow
from app.services.task_runners.f2_library_ids import known_library_aweme_ids
from app.services.task_runners.f2_personal import _personal_label, _personal_scan_root
from app.services.task_runners.f2_progress import (
    _PROGRESS_AFTER_DOWNLOAD,
    _WATCH_INTERVAL,
    _run_subprocess,
    _soft_fraction,
    _watch_download,
    _watch_personal_download,
)

logger = logging.getLogger(__name__)


async def _current_status(db: AsyncSession, task_id: int) -> str:
    """重新读取任务当前状态（供取消/暂停判定）。

    转发到 ``f2_import._current_status``，延迟导入有两个原因：

    1. **测试的 monkeypatch 点**：tests/test_f2_import_execute.py 一律
       ``monkeypatch.setattr(f2_import, "_current_status", ...)`` 来模拟取消/暂停，
       阶段函数必须走这里才能让 patch 生效（照抄实现就会**静默**失效）；
    2. 避免 f2_import 与本模块在模块级互相导入（f2_import 文末要再导出本模块符号）。
    """
    from app.services.task_runners.f2_import import _current_status as impl

    return await impl(db, task_id)


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
    # 总数要翻到底才知道：下载期不给 done/total（计数看 download_progress 的文件数），
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
            f2.DEFAULT_F2_DOWNLOAD_ROOT,
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
        "download_progress": fetch_summary["downloaded"],
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
    # F（下载前两道过滤）：库内已有作品 → 整件跳过；另一个「我的列表」目录里已有
    # 文件 → 硬链接过来让 f2 跳过。两者都只为「省一次重复下载」，不影响入库结果。
    existing_aweme_ids = await known_library_aweme_ids(db)
    link_from_root = f2.DEFAULT_F2_LIKE_ROOT  # 只从 like/ 预链接：那里的字节与收藏一致
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
            existing_aweme_ids=existing_aweme_ids,
            link_from_root=link_from_root,
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
        # F 的成效：库内已有而跳过下载的作品数 / 从同类目录预链接过来的文件数
        "skipped_existing": int(outcome.get("skipped_existing") or 0),
        "skipped_existing_ids": outcome.get("skipped_existing_ids") or [],
        "skipped_ids_truncated": bool(outcome.get("skipped_ids_truncated")),
        "prelinked": int(outcome.get("prelinked") or 0),
    }
    if fetch_summary["collect"]["skipped_ids_truncated"]:
        logger.warning(
            f"[f2 收藏夹] 已入库作品 ID 超过 {f2.SKIPPED_ID_LIMIT} 条被截断："
            "本轮合集只补入前若干条，其余下次运行会补上"
        )
    fetch_summary["downloaded"] = {
        "files": final_stats["files"],
        "bytes": final_stats["bytes"],
        "added": max(0, final_stats["files"] - baseline["files"]),
        "added_bytes": max(0, final_stats["bytes"] - baseline["bytes"]),
    }
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
    post_root = f2.DEFAULT_F2_ROOT
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
            author, download_root=f2.DEFAULT_F2_DOWNLOAD_ROOT, interval=interval
        )
        logger.info(f"f2 下载 {display}（窗口 {interval}）")
        # 下载**期间**也要有实时进度（2026-10-01 用户报「磁盘已落两千张图、界面还是
        # 第 0/1 个作者 · 0%」）：单个作者内部 f2 一路翻页、每页固定等一次 timeout，
        # 没有分母，所以用「作者数作真分母 + 该作者区间内软进度」，并把已落盘文件数
        # 每 2 秒写进 download_progress（界面据此显示「目录内共 N 个 · 本次新增 K」）。
        baseline = await asyncio.to_thread(f2.download_tree_stats, post_root)
        author_extra = {
            "author_index": index,
            "author_total": len(targets),
            "author": display,
        }
        task.result = {
            **opts,
            "stage": "download",
            "fetch": fetch_summary,
            "download_progress": {
                "files": baseline["files"],
                "bytes": baseline["bytes"],
                "added": 0,
                "added_bytes": 0,
                "seconds": 0,
                **author_extra,
            },
        }
        task.updated_at = utcnow()
        await db.commit()

        def _progress_at(elapsed: float, i: int = index) -> int:
            """进度口径：已完成的作者 + 当前作者区间内的软进度，再乘进 0~40% 的下载区间。

            `i - 1` 保证「正在跑第 1 个作者」时从 1% 起步而不是直接跳到 40%。
            写成具名函数而不是 lambda：lambda 带默认参数时 mypy 推不出
            ``Callable[[float], int]``（watch_download 的参数类型）。
            """
            return (
                int(
                    _PROGRESS_AFTER_DOWNLOAD
                    * (i - 1 + _soft_fraction(elapsed))
                    / max(1, len(targets))
                )
                + 1
            )

        try:
            rc, _stats = await _watch_download(
                db,
                task,
                asyncio.create_task(
                    asyncio.to_thread(_run_subprocess, cmd, f2_dir)
                ),
                post_root,
                baseline,
                opts,
                progress_at=_progress_at,
                extra=author_extra,
                result_extra={"fetch": fetch_summary},
            )
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
