"""定时采集计划：计划 CRUD、到期执行（调度循环调用）与手动立即执行。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.scraper import ScraperSchedule, ScraperTask
from app.schemas.scraper import ScraperScheduleCreate, ScraperScheduleUpdate
from app.services.scraper.process import _check_cdp, _safe_launch
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

_SCHEDULE_PLATFORMS = {"xiaohongshu", "douyin"}
_SCHEDULE_SORT_MODES = {"general", "latest", "popular"}


def _validate_schedule_platform(platform: str) -> str:
    """校验计划平台合法性。"""
    p = platform.strip().lower()
    if p not in _SCHEDULE_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}，允许: {_SCHEDULE_PLATFORMS}")
    return p


def _build_schedule_task_config(sched: ScraperSchedule) -> dict:
    """由计划构造采集任务配置（关键词按轮换机制取用，小红书定时任务使用配置中的调试端口走 CDP）。

    轮换规则：以已执行次数（run_count）为游标，每次执行轮流使用关键词列表中的
    一个（keywords[run_count % len]）；列表只有一个关键词时退化为固定关键词。
    """
    keywords = json.loads(sched.keywords or "[]")
    if keywords:
        # 轮换取词：每次任务开始时的关键词不一样（创建计划时选择的关键词轮流使用）
        rotation = keywords[sched.run_count % len(keywords)]
        keywords = [rotation]
    config: dict = {
        "keywords": keywords,
        "max_count": sched.max_count,
        "headless": True,  # 定时任务默认无头，避免弹出浏览器窗口
        "cdp_port": settings.chrome_debug_port if sched.platform == "xiaohongshu" else None,
    }
    if sched.sort_mode and sched.platform == "xiaohongshu":
        config["sort_mode"] = sched.sort_mode
    return config


def _advance_next_run(interval_minutes: int, due_at: datetime, now: datetime) -> datetime:
    """从到期点推进到未来的下一个执行槽，保持固定节奏。

    不直接用 now + interval，是为了避免服务停机或手动执行导致节奏漂移：
    例如每天 08:00 到期的计划，若 09:30 才恢复执行，下次仍应是次日 08:00
    而非 09:30。
    """
    nxt = due_at
    while nxt <= now:
        nxt += timedelta(minutes=interval_minutes)
    return nxt


async def create_schedule(db: AsyncSession, data: ScraperScheduleCreate) -> ScraperSchedule:
    """创建定时采集计划。"""
    platform = _validate_schedule_platform(data.platform)
    keywords = [k.strip() for k in data.keywords if k.strip()]
    if not keywords:
        raise HTTPException(status_code=400, detail="至少需要一个关键词")
    if data.sort_mode and data.sort_mode not in _SCHEDULE_SORT_MODES:
        raise HTTPException(status_code=400, detail=f"不支持的排序方式: {data.sort_mode}")

    sched = ScraperSchedule(
        platform=platform,
        keywords=json.dumps(keywords, ensure_ascii=False),
        max_count=data.max_count,
        sort_mode=data.sort_mode if platform == "xiaohongshu" else None,
        enabled=data.enabled,
        interval_minutes=data.interval_minutes,
        next_run_at=utcnow() + timedelta(minutes=data.interval_minutes) if data.enabled else None,
    )
    db.add(sched)
    await db.flush()
    await db.refresh(sched)
    await db.commit()
    return sched


async def list_schedules(db: AsyncSession) -> list[ScraperSchedule]:
    """列出全部定时采集计划（按 ID 倒序，即创建顺序倒序）。"""
    result = await db.execute(select(ScraperSchedule).order_by(ScraperSchedule.id.desc()))
    return list(result.scalars().all())


async def update_schedule(db: AsyncSession, schedule_id: int, data: ScraperScheduleUpdate) -> ScraperSchedule:
    """更新定时采集计划（仅更新传入字段）。

    间隔变更或重新启用时，从当前时间重新计算 next_run_at；停用时清空 next_run_at。
    """
    sched = await db.get(ScraperSchedule, schedule_id)
    if not sched:
        raise HTTPException(status_code=404, detail="定时计划不存在")

    if data.keywords is not None:
        keywords = [k.strip() for k in data.keywords if k.strip()]
        if not keywords:
            raise HTTPException(status_code=400, detail="至少需要一个关键词")
        sched.keywords = json.dumps(keywords, ensure_ascii=False)
    if data.max_count is not None:
        sched.max_count = data.max_count
    if data.sort_mode is not None:
        if sched.platform == "xiaohongshu":
            if data.sort_mode not in _SCHEDULE_SORT_MODES:
                raise HTTPException(status_code=400, detail=f"不支持的排序方式: {data.sort_mode}")
            # 「综合」与创建路径一致归一化为 None，避免 'general' 字符串与 NULL 并存
            sched.sort_mode = None if data.sort_mode == "general" else data.sort_mode
    interval_changed = data.interval_minutes is not None and data.interval_minutes != sched.interval_minutes
    if data.interval_minutes is not None:
        sched.interval_minutes = data.interval_minutes
    if data.enabled is not None:
        was_enabled = sched.enabled
        sched.enabled = data.enabled
        if data.enabled and (not was_enabled or interval_changed):
            sched.next_run_at = utcnow() + timedelta(minutes=sched.interval_minutes)
        elif not data.enabled:
            sched.next_run_at = None
    elif interval_changed and sched.enabled:
        sched.next_run_at = utcnow() + timedelta(minutes=sched.interval_minutes)

    await db.commit()
    await db.refresh(sched)
    return sched


async def delete_schedule(db: AsyncSession, schedule_id: int) -> dict:
    """删除定时采集计划（不删除已产生的采集任务）。"""
    sched = await db.get(ScraperSchedule, schedule_id)
    if not sched:
        raise HTTPException(status_code=404, detail="定时计划不存在")
    await db.delete(sched)
    await db.commit()
    return {"deleted": 1, "id": schedule_id}


async def run_schedule_now(db: AsyncSession, schedule_id: int) -> dict:
    """立即执行一次定时采集计划：创建采集任务并启动。

    小红书计划复用 CDP 预检，Chrome 调试端口不可用时直接返回明确错误，
    避免前端提示「已触发」但子进程实际连不上 Chrome 而立刻失败。
    """
    sched = await db.get(ScraperSchedule, schedule_id)
    if not sched:
        raise HTTPException(status_code=404, detail="定时计划不存在")

    if sched.platform == "xiaohongshu":
        # 端口探测是阻塞 socket 操作（最长约 3 秒），放线程池避免卡住事件循环
        ok, detail, is_chrome = await asyncio.to_thread(
            _check_cdp, settings.chrome_debug_port
        )
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Chrome 调试端口不可用: {detail}。"
                    "请先在「采集任务」页签启动调试模式 Chrome 后再执行定时计划。"
                ),
            )
        if not is_chrome:
            raise HTTPException(
                status_code=400,
                detail=f"CDP 采集必须使用 Google Chrome（非 360 极速浏览器等衍生版本）: {detail}",
            )

    task = ScraperTask(
        platform=sched.platform,
        status="pending",
        config=json.dumps(_build_schedule_task_config(sched), ensure_ascii=False),
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    now = utcnow()
    sched.last_task_id = task.id
    sched.last_run_at = now
    sched.run_count += 1
    # 保持原有节奏：仅当计划已到期时，从到期点推进到未来，而非从手动执行时间重置
    if sched.enabled and sched.next_run_at is not None and sched.next_run_at <= now:
        sched.next_run_at = _advance_next_run(sched.interval_minutes, sched.next_run_at, now)
    await db.commit()

    # 启动失败时由 _safe_launch 把任务置 failed，避免永久 pending
    await _safe_launch(db, task)
    return {"message": f"计划 {schedule_id} 已触发", "task_id": task.id}


async def run_due_schedules(db: AsyncSession) -> int:
    """执行所有到期的定时采集计划（由后端调度循环周期性调用）。

    创建任务失败（如 Chrome 未启动）时记录日志并照常推进 next_run_at，
    避免同一计划反复重试刷屏；具体失败原因可从对应任务记录中查看。

    通过条件 UPDATE（乐观锁）原子推进 next_run_at：仅当 next_run_at 仍为
    本循环读取到的到期值时生效。若「立即执行」已在同一次到期点抢先推进，
    rowcount 为 0，跳过本次避免重复触发两次。
    """
    now = utcnow()
    result = await db.execute(
        select(ScraperSchedule).where(
            ScraperSchedule.enabled.is_(True),
            ScraperSchedule.next_run_at.is_not(None),
            ScraperSchedule.next_run_at <= now,
        )
    )
    due = result.scalars().all()
    if not due:
        return 0

    triggered = 0
    for sched in due:
        # 乐观锁认领：从到期点推进到未来；已被其它入口抢先推进则跳过
        claimed = await db.execute(
            update(ScraperSchedule)
            .where(
                ScraperSchedule.id == sched.id,
                ScraperSchedule.enabled.is_(True),
                ScraperSchedule.next_run_at == sched.next_run_at,
            )
            .values(
                next_run_at=_advance_next_run(sched.interval_minutes, sched.next_run_at, now),
                last_run_at=now,
                run_count=ScraperSchedule.run_count + 1,
            )
        )
        if claimed.rowcount == 0:
            continue

        launched_id: int | None = None
        try:
            task = ScraperTask(
                platform=sched.platform,
                status="pending",
                config=json.dumps(_build_schedule_task_config(sched), ensure_ascii=False),
            )
            db.add(task)
            await db.flush()
            await db.refresh(task)
            launched_id = task.id
        except Exception as e:
            logger.warning(f"[定时采集] 计划 {sched.id} 创建任务失败: {e}")

        if launched_id is not None:
            await db.execute(
                update(ScraperSchedule)
                .where(ScraperSchedule.id == sched.id)
                .values(last_task_id=launched_id)
            )
        await db.commit()
        if launched_id is not None:
            task_row = await db.get(ScraperTask, launched_id)
            if task_row:
                # 启动失败时由 _safe_launch 把任务置 failed，避免永久 pending
                await _safe_launch(db, task_row)
        triggered += 1
    return triggered
