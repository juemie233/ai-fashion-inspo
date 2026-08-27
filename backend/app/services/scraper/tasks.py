"""采集任务管理：任务 CRUD、取消、重试、日志与采集源状态。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.scraper import ScraperSeenURL, ScraperTask
from app.schemas.scraper import ScraperTaskCreate
from app.services.audit_service import record_audit_log
from app.services.scraper.process import (
    CHROME_DEBUG_CMD,
    _check_cdp,
    _safe_launch,
    _scraper_pids,
    _scraper_retry_count,
)

logger = logging.getLogger(__name__)


async def get_scraper_sources() -> dict:
    """列出所有可用的采集源及其状态。"""
    from app.database import async_session

    async with async_session() as db:
        tombstone_count = (await db.execute(
            select(func.count(ScraperSeenURL.source_url))
        )).scalar() or 0

    return {
        "default_max_count": settings.scraper_default_max_count,
        "tombstone_count": tombstone_count,
        "sources": [
            {
                "platform": "xiaohongshu",
                "name": "小红书",
                "status": "available",
                "features": ["search", "discover"],
                "note": "需要浏览器登录 Cookie",
            },
            {
                "platform": "douyin",
                "name": "抖音",
                "status": "available",
                "features": ["search", "user"],
                "note": "CDP 真实 Chrome 通道：搜索（图集/视频/正文/话题）与按博主采集；首次需扫码登录",
            },
            {
                "platform": "browser_extension",
                "name": "浏览器插件",
                "status": "available",
                "features": ["one_click_capture"],
                "note": "最可靠的采集方式",
            },
        ]
    }


async def get_task_log(task_id: int) -> dict:
    """获取采集任务的日志内容（最近 200 行）。"""
    log_file = Path(settings.storage_root) / "logs" / "scraper" / f"task_{task_id}.log"
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")

    # 大日志读取是阻塞 I/O，放线程池避免卡住事件循环
    content = await asyncio.to_thread(
        lambda: log_file.read_text(encoding="utf-8", errors="replace")
    )
    lines = content.split("\n")
    return {
        "task_id": task_id,
        "total_lines": len(lines),
        "content": "\n".join(lines[-200:]),
        "size_bytes": log_file.stat().st_size,
    }


async def cancel_scraper_task(db: AsyncSession, task_id: int) -> dict:
    """取消运行中或等待中的采集任务（发送终止信号给子进程）。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail=f"任务状态为 {task.status}，无法取消")

    task.status = "cancelled"
    task.error = "用户手动取消"
    await db.commit()

    # 记录审计：取消采集任务属破坏性操作，留痕便于追溯
    await record_audit_log(
        action="cancel_scraper_task",
        target_type="scraper_tasks",
        count=1,
        detail=f"取消采集任务 {task_id}",
    )

    # 向子进程发送 SIGTERM
    pid = _scraper_pids.get(task_id)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"已发送 SIGTERM 给采集进程 PID={pid} (task {task_id})")
        except OSError:
            pass  # 进程已退出

    return {"message": f"任务 {task_id} 已取消"}


async def create_scraper_task(db: AsyncSession, data: ScraperTaskCreate) -> ScraperTask:
    """创建并启动一个新的采集任务。

    CDP 模式下会预先检测 Chrome 调试端口，不可用时返回明确的错误提示。
    """
    # CDP 模式：预检 Chrome 调试端口（小红书固定走 CDP；抖音显式传 cdp_port 时走 CDP，
    # 未传则由执行器回退独立浏览器降级路径）。端口探测是阻塞 socket 操作（最长约 3 秒），
    # 放线程池避免卡住事件循环
    if data.cdp_port is not None and data.platform in ("xiaohongshu", "douyin"):
        ok, detail, is_chrome = await asyncio.to_thread(_check_cdp, data.cdp_port)
        if not ok:
            cmd = CHROME_DEBUG_CMD.format(
                chrome=settings.chrome_executable,
                port=data.cdp_port,
                data_dir=settings.chrome_user_data_dir,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": f"Chrome 调试端口不可用: {detail}",
                    "hint": "请先用调试模式启动 Google Chrome 后再创建采集任务",
                    "command": cmd,
                },
            )
        if not is_chrome:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": detail,
                    "hint": "CDP 采集必须使用 Google Chrome（非 360 极速浏览器等衍生版本）",
                },
            )

    # 构建任务配置（保留向后兼容 + 新增字段）
    config = {
        "keywords": data.keywords,
        "max_count": data.max_count,
        "headless": data.headless,
        "cdp_port": data.cdp_port,
        "cookie_file": data.cookie_file,
    }
    # 新增可选字段
    extra = data.model_dump(exclude={"platform", "keywords", "max_count", "headless", "cdp_port", "cookie_file"}, exclude_none=True)
    config.update(extra)

    # 按博主采集（collect_mode=user）：校验博主存在，未传主页信息时从博主记录自动补齐。
    # profile_url 缺省拼接规则按平台区分（xhs: /user/profile/{uid}，douyin: /user/{uid}）
    if data.collect_mode == "user":
        from app.models.person import Blogger

        blogger = await db.get(Blogger, data.blogger_id)
        if not blogger:
            raise HTTPException(status_code=404, detail="博主未找到")
        if not config.get("profile_url") and blogger.profile_url:
            config["profile_url"] = blogger.profile_url
        if not config.get("platform_user_id") and blogger.platform_user_id:
            config["platform_user_id"] = blogger.platform_user_id
        # 博主平台与任务平台不符：跨平台主页链接无效，直接拒绝而非静默采错人
        if blogger.platform and blogger.platform not in (data.platform, "other"):
            raise HTTPException(
                status_code=400,
                detail=f"博主平台为 {blogger.platform}，与任务平台 {data.platform} 不符，请选择对应平台的博主",
            )
        if not config.get("profile_url") and config.get("platform_user_id"):
            puid = config["platform_user_id"]
            if data.platform == "douyin":
                config["profile_url"] = f"https://www.douyin.com/user/{puid}"
            else:
                config["profile_url"] = f"https://www.xiaohongshu.com/user/profile/{puid}"
        if not config.get("profile_url") and not config.get("platform_user_id"):
            raise HTTPException(
                status_code=400,
                detail="该博主缺少主页信息（profile_url / 平台用户 ID），请先完善博主资料",
            )

    task = ScraperTask(
        platform=data.platform,
        status="pending",
        config=json.dumps(config),
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    # 先提交事务，确保子进程能看到任务记录
    await db.commit()

    # 后台启动采集（子进程模式，完全隔离 Playwright）；
    # 启动失败时由 _safe_launch 把任务置 failed，避免永久 pending
    await _safe_launch(db, task)

    return task


async def list_scraper_tasks(
    db: AsyncSession,
    platform: str | None = None,
    status: str | None = None,
    sort: str = "newest",  # newest | oldest | most_found | most_added
    page: int = 1,
    size: int = 50,
) -> tuple[list[ScraperTask], int, dict[str, int]]:
    """获取采集任务列表，支持筛选、排序与分页。

    Returns:
        (任务列表, 符合筛选条件的总数, 按状态聚合的统计)
    """
    conditions = []
    if platform:
        conditions.append(ScraperTask.platform == platform)
    if status:
        conditions.append(ScraperTask.status == status)

    # 总数统计（用于前端分页）
    count_query = select(func.count(ScraperTask.id))
    if conditions:
        count_query = count_query.where(*conditions)
    total = (await db.execute(count_query)).scalar() or 0

    # 按状态聚合（覆盖全部筛选结果，而非仅当前页）
    stats_result = await db.execute(
        select(ScraperTask.status, func.count(ScraperTask.id))
        .where(*conditions)
        .group_by(ScraperTask.status)
    )
    stats = {"pending": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0}
    for s, c in stats_result.all():
        stats[s] = c

    query = select(ScraperTask)
    if conditions:
        query = query.where(*conditions)

    # 排序（附加 id 兜底键，保证同秒创建的任务顺序稳定）
    sort_map = {
        "newest": (ScraperTask.created_at.desc(), ScraperTask.id.desc()),
        "oldest": (ScraperTask.created_at.asc(), ScraperTask.id.asc()),
        "most_found": (ScraperTask.items_found.desc(), ScraperTask.id.desc()),
        "most_added": (ScraperTask.items_added.desc(), ScraperTask.id.desc()),
    }
    primary, tiebreak = sort_map.get(
        sort, (ScraperTask.created_at.desc(), ScraperTask.id.desc())
    )
    query = query.order_by(primary, tiebreak)
    query = query.offset((page - 1) * size).limit(size)

    result = await db.execute(query)
    tasks = result.scalars().all()
    return list(tasks), total, stats


async def delete_single_scraper_task(db: AsyncSession, task_id: int) -> dict:
    """物理删除单条采集任务（素材的 scraper_task_id 自动置 NULL，不删除素材）。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="采集任务未找到")
    await db.delete(task)
    await db.commit()
    # 记录审计：物理删除采集任务历史属破坏性操作，留痕便于追溯
    await record_audit_log(
        action="delete_scraper_task",
        target_type="scraper_tasks",
        count=1,
        detail=f"删除采集任务 {task_id}",
    )
    return {"deleted": 1, "id": task_id}


async def clear_all_scraper_tasks(db: AsyncSession) -> dict:
    """物理删除所有采集任务历史记录。"""
    result = await db.execute(delete(ScraperTask))
    await db.commit()
    # 记录审计：清空采集任务历史属不可恢复的破坏性操作
    await record_audit_log(
        action="clear_scraper_tasks",
        target_type="scraper_tasks",
        count=result.rowcount,
        detail="清空全部采集任务历史记录",
    )
    return {"deleted": result.rowcount}


async def retry_failed_scraper_tasks(db: AsyncSession) -> dict:
    """重试所有失败的采集任务，使用相同配置重新创建任务。"""
    result = await db.execute(
        select(ScraperTask).where(ScraperTask.status == "failed")
    )
    failed_tasks = result.scalars().all()

    if not failed_tasks:
        raise HTTPException(status_code=404, detail="没有失败的采集任务")

    retried = 0
    new_tasks: list[ScraperTask] = []
    for task in failed_tasks:
        new_task = ScraperTask(
            platform=task.platform,
            status="pending",
            config=task.config,
        )
        db.add(new_task)
        await db.flush()
        await db.refresh(new_task)
        new_tasks.append(new_task)
        retried += 1

    # 先提交事务，确保子进程能看到新任务记录
    await db.commit()

    for new_task in new_tasks:
        await _safe_launch(db, new_task)

    # 记录审计：批量重试采集任务属破坏性操作，留痕便于追溯
    await record_audit_log(
        action="retry_scraper_tasks",
        target_type="scraper_tasks",
        count=retried,
        detail=f"重试 {retried} 个失败的采集任务",
    )

    return {"retried": retried, "message": f"已重新创建 {retried} 个采集任务"}


async def retry_single_task(db: AsyncSession, task_id: int) -> dict:
    """重试单个失败任务：沿用 resume_token 从断点续采，而非新建任务。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != "failed":
        raise HTTPException(
            status_code=400, detail=f"仅失败任务可续采（当前状态 {task.status}）"
        )

    task.status = "pending"
    task.error = None
    await db.commit()

    # 视为一次新的手动尝试，重置自动续采计数
    _scraper_retry_count.pop(task_id, None)

    await _safe_launch(db, task)
    # 记录审计：重试采集任务属破坏性操作，留痕便于追溯
    await record_audit_log(
        action="retry_scraper_task",
        target_type="scraper_tasks",
        count=1,
        detail=f"断点续采任务 {task_id}",
    )
    return {"message": f"任务 {task_id} 已重新加入队列（断点续采）", "task_id": task_id}
