"""f2 导入的「计划 + 入库 + 登记」阶段（切片 E）。

从 `task_runners/f2_import.py` 拆出的第五片。三件事按执行顺序排在这里：

1. ``_scan_and_plan_stage``：扫描下载目录、五层去重、算出本批要入库的文件；
2. ``_apply_import_stage``：把计划交给线程内的同步入库函数，另有 watcher 每 2 秒把
   线程内进度落库，并把外部 cancelled/paused 转成线程内的停止标记；
3. ``_register_personal_bloggers_stage``：把「我的喜欢」的来源作者补建成博主。

收尾结算（收藏夹归位 + 批次清单）不在这里，见切片 F。
"""

import asyncio
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskQueue
from app.services.task_runners.common import utcnow
from app.services.task_runners.f2_personal import (
    PERSONAL_FETCH_MODES,
    _append_notice,
    _personal_scan_root,
)
from app.services.task_runners.f2_progress import _PROGRESS_AFTER_PLAN, _WATCH_INTERVAL

logger = logging.getLogger(__name__)


async def _current_status(db: AsyncSession, task_id: int) -> str:
    """重新读取任务当前状态（供取消/暂停判定）。

    转发到 ``f2_import._current_status``，延迟导入有两个原因：

    1. **测试的 monkeypatch 点**：tests/test_f2_import_execute.py 一律
       ``monkeypatch.setattr(f2_import, "_current_status", ...)`` 来模拟取消/暂停，
       阶段函数必须走这里才能让 patch 生效（照抄实现会**静默**失效：真读库返回
       running，取消永远不发生）；
    2. 避免 f2_import 与本模块在模块级互相导入（f2_import 文末要再导出本模块符号）。
    """
    from app.services.task_runners.f2_import import _current_status as impl

    return await impl(db, task_id)


async def _scan_and_plan_stage(db: AsyncSession, task: TaskQueue, opts: dict, bloggers: dict,
                     plan_authors: set[str] | None, limit: int | None, skip_live: bool,
                     fetch_mode: str, fetch_summary: dict,
                     collect_scope: dict | None = None,
                     allowed_aweme_ids: set[str] | None = None) -> list:
    """阶段 2：扫描下载目录 + 去重计划（放线程，否则阻塞 worker 事件循环）。

    无可入库文件时本函数直接落「done」结果并返回空列表，调用方据此收工。

    Args:
        collect_scope: 收藏模式传入一个 dict，函数会把「本次扫描到的作品 ID」写进
            ``collect_scope["aweme_ids"]``（供合集补齐用：合集要收的是收藏全集，
            而不是「本批新入库」）。
        allowed_aweme_ids: **入库范围白名单**（勾选的收藏夹里的作品 ID）。非 None 时
            扫描结果先按它过滤：没勾的夹即使文件早就躺在硬盘上也不入库。这是「我只勾了
            两个夹」这句承诺的另一半——下载阶段只下勾选的夹，入库阶段必须同样只看它们。
    """
    from scripts import import_f2_downloads as f2

    scan_root = (
        _personal_scan_root(f2, fetch_mode)
        if fetch_mode in PERSONAL_FETCH_MODES
        else f2.DEFAULT_F2_ROOT
    )
    # 扫描 + 去重是重活（大目录分钟级），单独标一个阶段：否则界面在下载结束到入库
    # 开始的这段窗口里还停在「下载中」的文案上，看起来像卡住
    task.result = {**(task.result or {}), "stage": "scan"}
    task.updated_at = utcnow()
    await db.commit()
    files = await asyncio.to_thread(f2.scan_directory, scan_root)
    scoped_out = 0
    if allowed_aweme_ids is not None:
        kept = [f for f in files if f.aweme_id and f.aweme_id in allowed_aweme_ids]
        scoped_out = len(files) - len(kept)
        files = kept
        logger.info(
            f"收藏夹范围过滤：{len(kept)} 个文件在勾选的夹内，跳过 {scoped_out} 个"
            "（未勾选的夹 / 无作品 ID 的历史产物）"
        )
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
    if collect_scope is not None:
        # 本次扫描到的作品 ID（含被判重跳过、没入库的那些）：合集补齐要用它把
        # 「你的收藏全集」补齐，而不是只收本批新入库的几条
        collect_scope["aweme_ids"] = sorted(
            {d.item.aweme_id for d in decisions if d.item.aweme_id}
        )
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
            # 因不在勾选的收藏夹内而没入库的文件数（0 = 没限定范围/全都命中）
            "scoped_out": scoped_out,
        },
    }
    if scoped_out:
        # 范围过滤是「用户看不见就被挡掉」的典型场景：必须留痕，否则他只会觉得
        # 「怎么少了这么多」。注意**必须在上面的 task.result 整体赋值之后**再追加，
        # 否则会被这次赋值覆盖掉（踩过一次：notices 直接消失）。
        _append_notice(task, f"未勾选的收藏夹跳过 {scoped_out} 个文件")
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
        return bool(holder["stop"])

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
        task.result = {**(task.result or {}), "stage": "blogger"}
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
