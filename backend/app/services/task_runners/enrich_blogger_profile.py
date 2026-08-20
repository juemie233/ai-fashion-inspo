"""博主主页信息补全任务：为缺失主页信息的小红书博主批量搜索补全。

- 复用小红书采集引擎（XiaohongshuScraper.search_users）与任务队列；
- 串行处理，每个博主之间随机延时（1~2 秒）规避风控；单次任务上限
  MAX_ENRICH_PER_TASK（默认 20）个博主，超出部分提示用户分批；
- 每处理一个博主 task.done++（进度可观测）；每批检查取消检查点
  （cancelled 则停止，不影响已完成博主）；
- 单博主失败记录原因不阻塞整体；结果含逐博主明细（成功/失败+原因），
  前端可据此展示失败列表并单独重试。
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.person import Blogger
from app.models.task import TaskQueue
from app.services.blogger_enrichment_service import (
    enrich_one,
    list_missing_profile_bloggers,
)
from app.services.task_runners.common import PermanentTaskError, utcnow

logger = logging.getLogger(__name__)

# 单次补全数量上限（规避风控；超出部分提示分批执行）
MAX_ENRICH_PER_TASK = 20
# 连续无结果阈值：达到后延时档位放大（小红书风控时搜索会静默返回无结果）
CONSECUTIVE_EMPTY_THRESHOLD = 3
# 延时档位（秒，依次放大）：基础 0.3~0.8（实验性放开）→ 1~2 → 2~4，
# 命中结果后恢复基础档
DELAY_ESCALATIONS = [(0.3, 0.8), (1.0, 2.0), (2.0, 4.0)]


async def create_enrich_blogger_profile_task(
    db: AsyncSession, blogger_ids: list[int] | None = None
) -> tuple[TaskQueue | None, int]:
    """创建博主主页补全任务（返回 (任务, 待处理数)；无缺失博主返回 (None, 0)）。

    参数:
        blogger_ids: 限定补全范围（None = 全部缺失博主）
    """
    bloggers = await list_missing_profile_bloggers(db, blogger_ids)
    # 范围限定下仍可能包含非缺失博主：按 id 过滤后再截断上限
    if blogger_ids:
        id_set = set(blogger_ids)
        bloggers = [b for b in bloggers if b.id in id_set]
    bloggers = bloggers[:MAX_ENRICH_PER_TASK]
    if not bloggers:
        return None, 0
    ids = [b.id for b in bloggers]
    task = TaskQueue(
        type="enrich_blogger_profile",
        status="pending",
        progress=0,
        total=len(ids),
        done=0,
        result={"blogger_ids": ids},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    logger.info(f"已创建博主主页补全任务: #{task.id}，{len(ids)} 个博主")
    return task, len(ids)


async def _is_cancelled(db: AsyncSession, task: TaskQueue) -> bool:
    """检查任务是否被外部置为 cancelled（每处理一个博主后调用）。"""
    result = await db.execute(select(TaskQueue.status).where(TaskQueue.id == task.id))
    return (result.scalar() or "running") == "cancelled"


async def execute_enrich_blogger_profile(db: AsyncSession, task: TaskQueue) -> None:
    """执行博主主页补全任务：逐个搜索补全，记录明细，可取消。"""
    payload = task.result or {}
    ids = payload.get("blogger_ids") or []
    total = len(ids)
    task.total = total
    if total == 0:
        task.progress = 100
        task.result = {**payload, "results": [], "message": "无可补全的博主"}
        await db.commit()
        return

    from concurrent.futures import ThreadPoolExecutor

    from app.scrapers.xiaohongshu import XiaohongshuScraper

    # 小红书搜索需要登录态：加载采集管理导入的 Cookie，缺失/无效时快速失败
    # （避免 20 个博主全部跑一遍登录墙才报错，浪费时长且原因不明确）
    cookie_path = Path(settings.storage_root) / "cookies" / "xiaohongshu_cookies.json"
    if not cookie_path.exists():
        raise PermanentTaskError(
            "未找到小红书 Cookie（storage/cookies/xiaohongshu_cookies.json），"
            "请先在采集管理页导入小红书 Cookie 后重试"
        )
    scraper = XiaohongshuScraper(
        headless=settings.scraper_browser_headless,
        cookie_file=str(cookie_path),
    )
    # Playwright sync API 的 greenlet 绑定创建线程：浏览器初始化/Cookie/页面操作
    # 必须同一线程执行（多次 to_thread 落不同线程会报
    # 「Cannot switch to a different thread」）——使用专用单线程执行器串行处理
    loop = asyncio.get_event_loop()
    browser_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="xhs-enrich"
    )

    async def _search_users(keyword: str) -> list[dict]:
        return await loop.run_in_executor(
            browser_executor, scraper.search_users_sync, keyword
        )

    try:
        results: list[dict] = []
        updated = 0
        skipped = 0
        failed = 0
        cancelled = False
        consecutive_empty = 0  # 连续无结果计数（风控自适应降速）
        for idx, blogger_id in enumerate(ids, start=1):
            if await _is_cancelled(db, task):
                cancelled = True
                break
            blogger = await db.get(Blogger, blogger_id)
            if blogger is None:
                results.append(
                    {
                        "blogger_id": blogger_id,
                        "name": f"# {blogger_id}",
                        "status": "failed",
                        "reason": "博主不存在（可能已删除）",
                    }
                )
                failed += 1
            else:
                result = await enrich_one(db, blogger, search_users=_search_users)
                results.append(result)
                if result["status"] == "updated":
                    updated += 1
                    consecutive_empty = 0
                elif result["status"] == "skipped":
                    skipped += 1
                    # 「搜索无结果/无法唯一确认」视为无命中：连续触发可能被风控限流
                    consecutive_empty += 1
                else:
                    failed += 1
            task.done = idx
            task.progress = round(idx / total * 100)
            task.updated_at = utcnow()
            await db.commit()
            # 随机延时防风控（最后一个博主后可省）；连续无结果自动降速
            if idx < total:
                level = min(
                    consecutive_empty // CONSECUTIVE_EMPTY_THRESHOLD,
                    len(DELAY_ESCALATIONS) - 1,
                )
                lo, hi = DELAY_ESCALATIONS[level]
                await asyncio.sleep(random.uniform(lo, hi))
    finally:
        try:
            await loop.run_in_executor(browser_executor, scraper.close_sync)
        except Exception:  # noqa: BLE001 关闭失败不影响任务结果
            pass
        browser_executor.shutdown(wait=False)

    task.result = {
        **payload,
        "results": results,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "cancelled": cancelled,
    }
    if cancelled:
        task.status = "cancelled"  # worker 见 status != running 不会覆盖为 success
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"博主主页补全任务结束: #{task.id} 成功 {updated} 跳过 {skipped} "
        f"失败 {failed} cancelled={cancelled}"
    )
