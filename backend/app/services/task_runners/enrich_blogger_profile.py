"""博主资料补全任务：小红书在线搜索补主页信息 + 抖音离线回填 IP 属地。

- **抖音**：直接读 f2 用户库（``douyin_users.db``）按 sec_user_id 精确匹配回填
  ``ip_location``——离线、零网络、零风控，因此**不受单次上限约束**、也不加延时；
- **小红书**：复用采集引擎（XiaohongshuScraper.search_users），串行处理且每个博主
  之间随机延时（0.3~4 秒，连续无结果自动降速）规避风控，单次上限
  MAX_ENRICH_PER_TASK（默认 20），超出部分提示用户分批；
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
    backfill_douyin_from_f2,
    enrich_one,
    list_missing_profile_bloggers,
    load_f2_profile_map,
)
from app.services.task_runners.common import PermanentTaskError, utcnow

logger = logging.getLogger(__name__)

# 单次任务里「需要联网的小红书搜索」数量上限（规避风控；超出部分提示分批执行）。
# 抖音回填是离线读 f2 库，不计入这个上限。
MAX_ENRICH_PER_TASK = 20
# 连续无结果阈值：达到后延时档位放大（小红书风控时搜索会静默返回无结果）
CONSECUTIVE_EMPTY_THRESHOLD = 3
# 延时档位（秒，依次放大）：基础 0.3~0.8（实验性放开）→ 1~2 → 2~4，
# 命中结果后恢复基础档
DELAY_ESCALATIONS = [(0.3, 0.8), (1.0, 2.0), (2.0, 4.0)]


async def create_enrich_blogger_profile_task(
    db: AsyncSession, blogger_ids: list[int] | None = None
) -> tuple[TaskQueue | None, int]:
    """创建博主资料补全任务（返回 (任务, 待处理数)；无缺口博主返回 (None, 0)）。

    抖音（离线回填）全部纳入；小红书（联网搜索）按 :data:`MAX_ENRICH_PER_TASK`
    截断——离线部分零成本，没必要挤占联网部分的配额。

    参数:
        blogger_ids: 限定补全范围（None = 全部有缺口的博主）
    """
    bloggers = await list_missing_profile_bloggers(db, blogger_ids)
    # 范围限定下仍可能包含无缺口博主：按 id 过滤
    if blogger_ids:
        id_set = set(blogger_ids)
        bloggers = [b for b in bloggers if b.id in id_set]
    offline = [b for b in bloggers if b.platform == "douyin"]
    online = [b for b in bloggers if b.platform != "douyin"]
    selected = offline + online[:MAX_ENRICH_PER_TASK]
    if not selected:
        return None, 0
    ids = [b.id for b in selected]
    task = TaskQueue(
        type="enrich_blogger_profile",
        status="pending",
        progress=0,
        total=len(ids),
        done=0,
        # truncated：小红书（联网）部分是否被单次上限截断，路由据此提示分批
        result={"blogger_ids": ids, "truncated": len(online) > MAX_ENRICH_PER_TASK},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    logger.info(
        f"已创建博主资料补全任务: #{task.id}，{len(ids)} 个博主"
        f"（抖音离线 {len(offline)} / 小红书联网 {len(selected) - len(offline)}）"
    )
    return task, len(ids)


async def _is_cancelled(db: AsyncSession, task: TaskQueue) -> bool:
    """检查任务是否被外部置为 cancelled（每处理一个博主后调用）。"""
    result = await db.execute(select(TaskQueue.status).where(TaskQueue.id == task.id))
    return (result.scalar() or "running") == "cancelled"


async def execute_enrich_blogger_profile(db: AsyncSession, task: TaskQueue) -> None:
    """执行博主资料补全任务：抖音离线回填 + 小红书在线搜索，记录明细，可取消。"""
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
        browser_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="xhs-enrich"
        )

    async def _search_users(keyword: str) -> list[dict]:
        return await loop.run_in_executor(
            browser_executor, scraper.search_users_sync, keyword
        )

    try:
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
            elif blogger.platform == "douyin":
                # 离线回填：不联网、不加延时，也不受单次上限约束
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
            # 随机延时防风控（最后一个博主后可省；抖音离线回填不需要）；
            # 连续无结果自动降速
            if idx < total and platforms.get(blogger_id) != "douyin":
                level = min(
                    consecutive_empty // CONSECUTIVE_EMPTY_THRESHOLD,
                    len(DELAY_ESCALATIONS) - 1,
                )
                lo, hi = DELAY_ESCALATIONS[level]
                await asyncio.sleep(random.uniform(lo, hi))
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
