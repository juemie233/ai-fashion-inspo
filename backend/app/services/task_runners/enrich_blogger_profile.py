"""博主资料补全任务：小红书按关注列表解析 uid + 抖音离线回填 IP 属地。

- **抖音**：直接读 f2 用户库（``douyin_users.db``）按 sec_user_id 精确匹配回填
  ``ip_location``——离线、零网络、零风控；
- **小红书**：起一次浏览器拉「我关注的用户」列表（一次请求拿到全部关注账号的
  uid），按昵称归一化匹配补 ``profile_url`` / ``platform_user_id``；除这一次请求
  外全是本地 URL ↔ ID 互推，逐个博主不再发请求，因此不需要风控延时，也不设单次
  数量上限；
- 每处理一个博主 task.done++（进度可观测）；每批检查取消检查点
  （cancelled 则停止，不影响已完成博主）；
- 单博主失败记录原因不阻塞整体；结果含逐博主明细（成功/跳过+原因），
  前端可据此展示跳过列表并单独重试。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.person import Blogger
from app.models.task import TaskQueue
from app.services.blogger_enrichment_service import (
    backfill_douyin_from_f2,
    build_following_index,
    enrich_one,
    list_missing_profile_bloggers,
    load_f2_profile_map,
)
from app.services.task_runners.common import PermanentTaskError, utcnow

logger = logging.getLogger(__name__)


async def create_enrich_blogger_profile_task(
    db: AsyncSession, blogger_ids: list[int] | None = None
) -> tuple[TaskQueue | None, int]:
    """创建博主资料补全任务（返回 (任务, 待处理数)；无缺口博主返回 (None, 0)）。

    参数:
        blogger_ids: 限定补全范围（None = 全部有缺口的博主）
    """
    bloggers = await list_missing_profile_bloggers(db, blogger_ids)
    # 范围限定下仍可能包含无缺口博主：按 id 过滤
    if blogger_ids:
        id_set = set(blogger_ids)
        bloggers = [b for b in bloggers if b.id in id_set]
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
    logger.info(f"已创建博主资料补全任务: #{task.id}，{len(ids)} 个博主")
    return task, len(ids)


async def _is_cancelled(db: AsyncSession, task: TaskQueue) -> bool:
    """检查任务是否被外部置为 cancelled（每处理一个博主后调用）。"""
    result = await db.execute(select(TaskQueue.status).where(TaskQueue.id == task.id))
    return (result.scalar() or "running") == "cancelled"


async def execute_enrich_blogger_profile(db: AsyncSession, task: TaskQueue) -> None:
    """执行博主资料补全任务：抖音离线回填 + 小红书关注列表解析，记录明细，可取消。"""
    payload = task.result or {}
    ids = payload.get("blogger_ids") or []
    total = len(ids)
    task.total = total
    if total == 0:
        task.progress = 100
        task.result = {**payload, "results": [], "message": "无可补全的博主"}
        await db.commit()
        return

    # 目标博主的平台：决定要不要起浏览器（只有小红书才需要 Cookie 与浏览器）
    platform_rows = await db.execute(
        select(Blogger.id, Blogger.platform).where(Blogger.id.in_(ids))
    )
    platforms = {bid: platform for bid, platform in platform_rows.all()}
    needs_browser = any(p == "xiaohongshu" for p in platforms.values())

    results: list[dict] = []
    updated = 0
    skipped = 0
    failed = 0
    douyin_updated = 0
    cancelled = False
    # f2 用户库（抖音 IP 属地来源）：惰性加载一次，离线读文件
    f2_profiles: dict[str, dict] | None = None
    loop = asyncio.get_event_loop()
    browser_executor = None
    scraper = None
    if needs_browser:
        from concurrent.futures import ThreadPoolExecutor

        from app.scrapers.xiaohongshu import XiaohongshuScraper

        # 关注列表接口需要登录态：加载采集管理导入的 Cookie，缺失时快速失败
        # （避免整个任务跑到浏览器里才发现没登录，原因不明确）
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
        browser_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="xhs-enrich"
        )

    try:
        # 小红书侧：一次拉全关注列表（按昵称解析 uid 的唯一来源），失败即整任务失败
        following_index: dict[str, str] = {}
        if needs_browser:
            rows = await loop.run_in_executor(
                browser_executor, scraper.list_following_sync
            )
            following_index = build_following_index(rows)
            logger.info(
                f"小红书关注列表：{len(rows)} 人，"
                f"可用于解析 {len(following_index)} 个昵称"
            )

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
            elif blogger.platform == "douyin":
                # 离线回填：读本地 f2 库，不联网
                if f2_profiles is None:
                    f2_profiles = await asyncio.to_thread(load_f2_profile_map)
                result = await backfill_douyin_from_f2(db, blogger, f2_profiles)
                results.append(result)
                if result["status"] == "updated":
                    updated += 1
                    douyin_updated += 1
                else:
                    skipped += 1
            else:
                # 小红书：只用已拉好的关注列表索引，逐个博主零请求
                result = await enrich_one(db, blogger, following=following_index)
                results.append(result)
                if result["status"] == "updated":
                    updated += 1
                elif result["status"] == "skipped":
                    skipped += 1
                else:
                    failed += 1
            task.done = idx
            task.progress = round(idx / total * 100)
            task.updated_at = utcnow()
            await db.commit()
    finally:
        if browser_executor is not None and scraper is not None:
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
        # 其中抖音（离线 f2 用户库）回填成功数：前端据此说明「IP 属地补了 N 位」
        "douyin_updated": douyin_updated,
        "cancelled": cancelled,
    }
    if cancelled:
        task.status = "cancelled"  # worker 见 status != running 不会覆盖为 success
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"博主资料补全任务结束: #{task.id} 成功 {updated}（抖音 IP 属地 {douyin_updated}）"
        f" 跳过 {skipped} 失败 {failed} cancelled={cancelled}"
    )
