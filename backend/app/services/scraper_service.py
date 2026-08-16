"""采集服务：编排采集任务、下载图片、入库、触发 AI 分析，以及采集引擎管理。"""

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.scraper import ScraperSchedule, ScraperSeenURL, ScraperTask
from app.schemas.scraper import (
    ScraperScheduleCreate,
    ScraperScheduleUpdate,
    ScraperTaskCreate,
)
from app.services.audit_service import record_audit_log
from app.services.file_service import move_to_trash
from app.services.inspiration_service import _resolve_trash_reason
from app.services.scraper_seen_service import seal_urls
from app.utils.time import format_utc, utcnow

logger = logging.getLogger(__name__)

# 运行中子进程映射
_scraper_pids: dict[int, int] = {}  # task_id → pid
_scraper_handles: dict[int, object] = {}  # task_id → Popen 对象（用于日志句柄回收）
_scraper_retry_count: dict[int, int] = {}  # task_id → 自动续采已重试次数

# Chrome 调试模式启动命令模板（路径从配置读取）
CHROME_DEBUG_CMD = (
    '"{chrome}" '
    "--remote-debugging-port={port} "
    '--user-data-dir="{data_dir}"'
)

# Cookie 平台白名单
_COOKIE_PLATFORMS = {"xiaohongshu", "douyin"}


# ============ Chrome/CDP 与子进程管理 ============


def _check_cdp(port: int, timeout: float = 2.0) -> tuple[bool, str, bool]:
    """检测 Chrome 调试端口是否可用。

    Args:
        port: CDP 端口号
        timeout: 连接超时（秒）

    Returns:
        (是否可用, 详情信息, 是否为 Google Chrome)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex(("127.0.0.1", port))
        if result == 0:
            import urllib.request
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/json/version",
                    headers={"User-Agent": "Chrome"},
                )
                with urllib.request.urlopen(req, timeout=1) as resp:
                    data = json.loads(resp.read().decode())
                    browser = data.get("Browser", "Unknown")
                    is_chrome = "Chrome" in browser and "360" not in browser
                    if is_chrome:
                        return True, f"已连接 {browser} (端口 {port})", True
                    else:
                        return True, (
                            f"端口 {port} 上运行的是 {browser}，而非 Google Chrome。"
                            f"CDP 采集必须使用 Google Chrome，请关闭当前浏览器后重新启动 Chrome 调试模式。"
                        ), False
            except Exception:
                return True, f"端口 {port} 可达，但未能确认调试协议", False
        else:
            return False, f"端口 {port} 无响应（请先在命令行中启动调试 Chrome）", False
    except socket.timeout:
        return False, f"端口 {port} 连接超时", False
    except Exception as e:
        return False, f"端口检测异常: {e}", False
    finally:
        sock.close()


def _launch_scraper_process(task_id: int) -> None:
    """启动独立子进程执行采集，完全隔离 Playwright。"""
    script = Path(__file__).parent.parent.parent / "scripts" / "run_scraper.py"

    # 日志输出到文件，方便排查
    logs_dir = Path(__file__).parent.parent.parent / "storage" / "logs" / "scraper"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_f = open(logs_dir / f"task_{task_id}.log", "w", encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, "-u", str(script), str(task_id)],
        stdout=log_f,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    _scraper_pids[task_id] = proc.pid
    _scraper_handles[task_id] = (proc, log_f)

    def _reap() -> None:
        """后台线程等待子进程退出后回收句柄，异常退出时自动续采。"""
        returncode = proc.wait()
        log_f.close()
        _scraper_pids.pop(task_id, None)
        _scraper_handles.pop(task_id, None)

        # 正常退出（0）视为完成，重置续采计数；非 0 视为异常，尝试自动续采
        if returncode == 0:
            _scraper_retry_count.pop(task_id, None)
        else:
            _maybe_auto_retry(task_id)

    threading.Thread(target=_reap, daemon=True).start()


def _maybe_auto_retry(task_id: int) -> None:
    """任务子进程异常退出时，若任务仍可续采且未超重试上限，自动重新拉起。"""
    async def _load() -> ScraperTask | None:
        async with async_session() as db:
            return await db.get(ScraperTask, task_id)
    try:
        task = asyncio.run(_load())
    except Exception as e:
        logger.warning(f"自动续采前加载任务失败（放弃本次续采）: {task_id} — {e}")
        return

    # 用户取消的任务不自动重试
    if task is None or task.status == "cancelled":
        return

    retried = _scraper_retry_count.get(task_id, 0)
    if retried >= settings.scraper_task_auto_retry:
        return

    _scraper_retry_count[task_id] = retried + 1
    logger.warning(
        f"采集任务 {task_id} 异常退出，自动续采（{retried + 1}/{settings.scraper_task_auto_retry}）"
    )
    # 延时片刻，给 ChromeManager 崩溃重启留出时间，避免立刻重连失败
    time.sleep(3)
    # 期间若已被其它入口（如手动续采）重新拉起，则不再重复启动
    if task_id in _scraper_pids:
        return
    _launch_scraper_process(task_id)


async def has_active_scraper_tasks() -> bool:
    """是否存在进行中的采集任务（running/pending，跨进程权威状态）。

    供 ChromeManager 空闲判定使用：仅看进程内 ``_scraper_pids`` 会在多 worker
    或 API 进程重启后误判「无活动采集」而提前关闭 Chrome，打断正在进行的采集。
    采集子进程会同步写库更新任务状态，DB 是权威来源。
    """
    async with async_session() as db:
        result = await db.execute(
            select(func.count(ScraperTask.id)).where(
                ScraperTask.status.in_(["running", "pending"])
            )
        )
        return (result.scalar() or 0) > 0


def _validate_cookie_platform(platform: str) -> str:
    """校验并标准化平台名，防止路径穿越。"""
    p = platform.strip().lower()
    if p not in _COOKIE_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}，允许: {_COOKIE_PLATFORMS}")
    return p


async def check_cdp(port: int) -> dict:
    """检查指定端口的 Chrome 调试连接是否就绪。
    端口探测是阻塞 socket 操作（最长约 3 秒），放入线程池避免卡住事件循环。
    """
    ok, detail, is_chrome = await asyncio.to_thread(_check_cdp, port)
    return {
        "available": ok,
        "is_google_chrome": is_chrome,
        "detail": detail,
        "startup_command": CHROME_DEBUG_CMD.format(
            chrome=settings.chrome_executable,
            port=port,
            data_dir=settings.chrome_user_data_dir,
        ),
    }


# ============ 采集源与 Cookie 管理 ============


async def get_scraper_sources() -> dict:
    """列出所有可用的采集源及其状态。"""
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
                "status": "limited",
                "features": ["search_web"],
                "note": "网页版功能有限，完整支持需要移动端自动化",
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


async def get_cookie_status(platform: str = "xiaohongshu") -> dict:
    """检查指定平台的 Cookie 文件状态。"""
    platform = _validate_cookie_platform(platform)
    cookie_dir = Path(settings.storage_root) / "cookies"
    cookie_file = cookie_dir / f"{platform}_cookies.json"

    if not cookie_file.exists():
        return {
            "platform": platform,
            "exists": False,
            "size_bytes": 0,
            "modified": None,
            "valid": False,
            "hint": f"尚未导入 {platform} 的 Cookie，采集可能无法获取完整数据",
        }

    stat = cookie_file.stat()
    age_hours = (datetime.now().timestamp() - stat.st_mtime) / 3600

    return {
        "platform": platform,
        "exists": True,
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "age_hours": round(age_hours, 1),
        "valid": age_hours < 72,  # Cookie 通常在 72 小时内有效
        "hint": "Cookie 可用" if age_hours < 72 else f"Cookie 已过期 {round(age_hours)} 小时，建议重新导入",
    }


async def import_cookies(payload: dict) -> dict:
    """导入平台 Cookie（JSON 格式，自动校验平台合法性）。"""
    platform = _validate_cookie_platform(payload.get("platform", "xiaohongshu"))
    cookie_data = payload.get("cookies")

    if not cookie_data:
        raise HTTPException(status_code=400, detail="请提供 Cookie 数据")

    cookie_dir = Path(settings.storage_root) / "cookies"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = cookie_dir / f"{platform}_cookies.json"

    cookie_file.write_text(json.dumps(cookie_data, ensure_ascii=False, indent=2), encoding="utf-8")
    count = len(cookie_data) if isinstance(cookie_data, list) else 0
    return {
        "message": f"已导入 {platform} Cookie",
        "platform": platform,
        "imported": count,
        "valid": True,  # 刚写入的文件视为有效
    }


async def delete_cookies(platform: str) -> dict:
    """删除指定平台的 Cookie 文件（不影响已导入的素材与任务）。"""
    platform = _validate_cookie_platform(platform)
    cookie_dir = Path(settings.storage_root) / "cookies"
    cookie_file = cookie_dir / f"{platform}_cookies.json"

    if not cookie_file.exists():
        raise HTTPException(status_code=404, detail="Cookie 文件不存在")

    cookie_file.unlink()
    return {"message": f"已删除 {platform} Cookie", "platform": platform}


# ============ 任务日志与取消 ============


async def get_task_log(task_id: int) -> dict:
    """获取采集任务的日志内容（最近 200 行）。"""
    log_file = Path(settings.storage_root) / "logs" / "scraper" / f"task_{task_id}.log"
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")

    content = log_file.read_text(encoding="utf-8", errors="replace")
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

    # 向子进程发送 SIGTERM
    pid = _scraper_pids.get(task_id)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"已发送 SIGTERM 给采集进程 PID={pid} (task {task_id})")
        except OSError:
            pass  # 进程已退出

    return {"message": f"任务 {task_id} 已取消"}


# ============ 浏览器插件任务记录 ============


def _utcnow() -> datetime:
    """当前 UTC 时间（无时区信息，与数据库 DateTime 一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_extension_task(db: AsyncSession, payload: dict) -> ScraperTask:
    """为浏览器插件的一次采集会话创建任务记录（running 状态）。

    插件在批量上传图片前调用，获得 task_id 后随每次上传附带；
    上传结束后调用 complete_extension_task 汇总计数并标记完成。
    """
    config = {
        "mode": "extension",
        "source_url": payload.get("source_url"),
        "origin_platform": payload.get("platform") or "browser_extension",
    }
    task = ScraperTask(
        platform="browser_extension",
        status="running",
        config=json.dumps(config, ensure_ascii=False),
        started_at=_utcnow(),
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    await db.commit()
    return task


async def complete_extension_task(db: AsyncSession, task_id: int, payload: dict) -> dict:
    """汇总浏览器插件采集会话的发现/入库数量并标记任务完成。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.platform != "browser_extension":
        raise HTTPException(status_code=400, detail="仅浏览器插件任务可使用此接口")

    items_found = int(payload.get("items_found") or 0)
    items_added = int(payload.get("items_added") or 0)
    skipped = max(0, items_found - items_added)

    task.status = "completed"
    task.items_found = items_found
    task.items_added = items_added
    task.finished_at = _utcnow()
    # 组装最小漏斗：与手动采集任务的漏斗结构对齐，前端漏斗弹窗可直接展示
    task.diagnostics = json.dumps({
        "per_search": [{
            "keyword": payload.get("source_url") or "插件采集",
            "sort_type": "extension",
            "batch_added": items_added,
            "batch_skipped_existing": skipped,
        }],
        "summary": {
            "total_found": items_found,
            "skipped_url_seen": 0,
            "skipped_content_dup": 0,
            "skipped_http_error": 0,
            "skipped_network_error": skipped,
            "total_added": items_added,
        },
    }, ensure_ascii=False)
    if items_added == 0 and items_found > 0:
        task.error = "全部图片上传失败（可能内容重复或后端异常）"
    await db.commit()
    return {"message": "已记录插件采集", "task_id": task_id, "items_added": items_added}


# ============ 定时采集计划 ============

_SCHEDULE_PLATFORMS = {"xiaohongshu", "douyin"}
_SCHEDULE_SORT_MODES = {"general", "latest", "popular"}


def _validate_schedule_platform(platform: str) -> str:
    """校验计划平台合法性。"""
    p = platform.strip().lower()
    if p not in _SCHEDULE_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}，允许: {_SCHEDULE_PLATFORMS}")
    return p


def _build_schedule_task_config(sched: ScraperSchedule) -> dict:
    """由计划构造采集任务配置（小红书定时任务使用配置中的调试端口走 CDP）。"""
    config: dict = {
        "keywords": json.loads(sched.keywords or "[]"),
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
        next_run_at=_utcnow() + timedelta(minutes=data.interval_minutes) if data.enabled else None,
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
            sched.next_run_at = _utcnow() + timedelta(minutes=sched.interval_minutes)
        elif not data.enabled:
            sched.next_run_at = None
    elif interval_changed and sched.enabled:
        sched.next_run_at = _utcnow() + timedelta(minutes=sched.interval_minutes)

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
        ok, detail, is_chrome = _check_cdp(settings.chrome_debug_port)
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

    now = _utcnow()
    sched.last_task_id = task.id
    sched.last_run_at = now
    sched.run_count += 1
    # 保持原有节奏：仅当计划已到期时，从到期点推进到未来，而非从手动执行时间重置
    if sched.enabled and sched.next_run_at is not None and sched.next_run_at <= now:
        sched.next_run_at = _advance_next_run(sched.interval_minutes, sched.next_run_at, now)
    await db.commit()

    _launch_scraper_process(task.id)
    return {"message": f"计划 {schedule_id} 已触发", "task_id": task.id}


async def run_due_schedules(db: AsyncSession) -> int:
    """执行所有到期的定时采集计划（由后端调度循环周期性调用）。

    创建任务失败（如 Chrome 未启动）时记录日志并照常推进 next_run_at，
    避免同一计划反复重试刷屏；具体失败原因可从对应任务记录中查看。

    通过条件 UPDATE（乐观锁）原子推进 next_run_at：仅当 next_run_at 仍为
    本循环读取到的到期值时生效。若「立即执行」已在同一次到期点抢先推进，
    rowcount 为 0，跳过本次避免重复触发两次。
    """
    now = _utcnow()
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
            _launch_scraper_process(launched_id)
        triggered += 1
    return triggered


# ============ 统计看板 ============


async def get_scraper_stats(days: int = 30) -> dict:
    """聚合近 N 天的采集任务统计：总量、成功率、按平台与按日分布。"""
    since = _utcnow() - timedelta(days=days)
    _total = func.count(ScraperTask.id)
    _completed = func.sum(case((ScraperTask.status == "completed", 1), else_=0))
    _failed = func.sum(case((ScraperTask.status == "failed", 1), else_=0))
    _found = func.coalesce(func.sum(ScraperTask.items_found), 0)
    _added = func.coalesce(func.sum(ScraperTask.items_added), 0)

    async with async_session() as db:
        overall = (await db.execute(
            select(_total, _completed, _failed, _found, _added)
            .where(ScraperTask.created_at >= since)
        )).one()

        platform_rows = (await db.execute(
            select(ScraperTask.platform, _total, _found, _added, _completed)
            .where(ScraperTask.created_at >= since)
            .group_by(ScraperTask.platform)
        )).all()

        # SQLite date() 直接作用于 UTC 时间戳列，按自然日聚合
        day_rows = (await db.execute(
            select(func.date(ScraperTask.created_at), _total, _added, _failed)
            .where(ScraperTask.created_at >= since)
            .group_by(func.date(ScraperTask.created_at))
            .order_by(func.date(ScraperTask.created_at))
        )).all()

    total = int(overall[0] or 0)
    completed = int(overall[1] or 0)
    failed = int(overall[2] or 0)

    return {
        "days": days,
        "total_tasks": total,
        "completed": completed,
        "failed": failed,
        "success_rate": round(completed / total * 100, 1) if total else 0,
        "total_found": int(overall[3] or 0),
        "total_added": int(overall[4] or 0),
        "by_platform": [
            {
                "platform": p,
                "tasks": int(t or 0),
                "found": int(f or 0),
                "added": int(a or 0),
                "completed": int(c or 0),
            }
            for p, t, f, a, c in platform_rows
        ],
        "by_day": [
            {
                "date": d,
                "tasks": int(t or 0),
                "added": int(a or 0),
                "failed": int(f or 0),
            }
            for d, t, a, f in day_rows
        ],
    }


# ============ 任务 CRUD ============


async def create_scraper_task(db: AsyncSession, data: ScraperTaskCreate) -> ScraperTask:
    """创建并启动一个新的采集任务。

    CDP 模式下会预先检测 Chrome 调试端口，不可用时返回明确的错误提示。
    """
    # CDP 模式：预检 Chrome 调试端口（仅小红书使用 CDP；抖音走独立 Playwright 浏览器）
    if data.cdp_port is not None and data.platform == "xiaohongshu":
        ok, detail, is_chrome = _check_cdp(data.cdp_port)
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

    # 后台启动采集（子进程模式，完全隔离 Playwright）
    _launch_scraper_process(task.id)

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
    return {"deleted": 1, "id": task_id}


async def clear_all_scraper_tasks(db: AsyncSession) -> dict:
    """物理删除所有采集任务历史记录。"""
    result = await db.execute(delete(ScraperTask))
    await db.commit()
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
    new_task_ids: list[int] = []
    for task in failed_tasks:
        new_task = ScraperTask(
            platform=task.platform,
            status="pending",
            config=task.config,
        )
        db.add(new_task)
        await db.flush()
        await db.refresh(new_task)
        new_task_ids.append(new_task.id)
        retried += 1

    # 先提交事务，确保子进程能看到新任务记录
    await db.commit()

    for task_id in new_task_ids:
        _launch_scraper_process(task_id)

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

    _launch_scraper_process(task_id)
    return {"message": f"任务 {task_id} 已重新加入队列（断点续采）", "task_id": task_id}


# ============ 任务结果管理 ============


async def get_task_results(
    db: AsyncSession,
    task_id: int,
    page: int = 1,
    size: int = 50,
) -> dict:
    """获取指定采集任务产出的素材列表（缩略图网格）。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="采集任务未找到")

    # 计数（排除垃圾桶中的软删除素材，与素材库正常列表口径一致）
    count_result = await db.execute(
        select(func.count(Inspiration.id)).where(
            Inspiration.scraper_task_id == task_id,
            Inspiration.deleted_at.is_(None),
        )
    )
    total = count_result.scalar() or 0

    # 分页
    items_result = await db.execute(
        select(Inspiration)
        .where(
            Inspiration.scraper_task_id == task_id,
            Inspiration.deleted_at.is_(None),
        )
        .order_by(Inspiration.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    items = items_result.scalars().all()

    def _fmt(dt) -> str | None:
        return format_utc(dt)

    return {
        "task": {
            "id": task.id,
            "platform": task.platform,
            "status": task.status,
            "config": task.config,
            "items_found": task.items_found,
            "items_added": task.items_added,
            "error": task.error,
            "started_at": _fmt(task.started_at),
            "finished_at": _fmt(task.finished_at),
            "created_at": _fmt(task.created_at),
        },
        "total": total,
        "page": page,
        "size": size,
        "items": [
            {
                "id": i.id,
                "file_path": i.file_path,
                "thumbnail_path": i.thumbnail_path,
                "media_type": i.media_type,
                "source_url": i.source_url,
                "is_favorite": i.is_favorite,
                "created_at": str(i.created_at) if i.created_at else None,
            }
            for i in items
        ],
    }


async def batch_delete_task_results(
    db: AsyncSession,
    task_id: int,
    ids: list[str],
    reason: str | None = None,
) -> dict:
    """批量将采集任务产出的指定素材移入垃圾桶（软删除，30 天内可恢复）。

    请求体: {"ids": ["id1", "id2", ...], "reason": "不喜欢"}

    与素材库单条软删除语义一致：标记 deleted_at / trash_reason、文件移入
    storage/trash/、向量保留（负样本学习依赖垃圾桶素材向量）。软删除即写入
    来源 URL 墓碑，采集器后续遇到该 URL 会直接跳过，不再重复采集。
    """
    if not ids:
        raise HTTPException(status_code=400, detail="请提供要删除的素材 ID 列表")

    # 仅处理属于该任务的素材；已在垃圾桶中的计入 skipped，不重复软删除
    result = await db.execute(
        select(Inspiration).where(
            Inspiration.id.in_(ids),
            Inspiration.scraper_task_id == task_id,
        )
    )
    inspirations = result.scalars().all()

    trashed_items: list[Inspiration] = []
    skipped = 0
    for insp in inspirations:
        if insp.deleted_at is not None:
            skipped += 1
            continue
        insp.deleted_at = utcnow()
        insp.trash_reason = _resolve_trash_reason(reason, insp)
        trashed_items.append(insp)

    # 先提交软删除标记与来源 URL 墓碑（同一事务），提交成功后再移动文件，避免
    # 「文件已移走但事务回滚/失败」导致 DB 仍指向原路径的悬空记录
    if trashed_items:
        await seal_urls(db, [insp.source_url for insp in trashed_items if insp.source_url])
        await db.commit()

    # 移动文件到垃圾桶目录；失败仅记日志不阻断软删除（恢复时按 DB 路径自愈）
    paths_changed = False
    for insp in trashed_items:
        try:
            new_file = move_to_trash(insp.file_path, insp.id)
            if new_file:
                insp.file_path = new_file
                paths_changed = True
        except OSError as e:
            logger.warning(f"移动主文件到垃圾桶失败 {insp.id}: {e}")
        try:
            new_thumb = move_to_trash(insp.thumbnail_path, insp.id, suffix="_thumb")
            if new_thumb:
                insp.thumbnail_path = new_thumb
                paths_changed = True
        except OSError as e:
            logger.warning(f"移动缩略图到垃圾桶失败 {insp.id}: {e}")

    if paths_changed:
        await db.commit()

    # 更新任务计数：只统计未删除素材（与结果列表口径一致）
    remaining_result = await db.execute(
        select(func.count(Inspiration.id)).where(
            Inspiration.scraper_task_id == task_id,
            Inspiration.deleted_at.is_(None),
        )
    )
    remaining = remaining_result.scalar() or 0

    task = await db.get(ScraperTask, task_id)
    if task:
        task.items_added = remaining
        await db.commit()

    # 批量移入垃圾桶纳入审计，便于追溯批量整理动作
    if trashed_items:
        detail = f"采集任务 {task_id} 结果移入垃圾桶"
        if skipped:
            detail += f"，跳过 {skipped} 个（已在垃圾桶）"
        await record_audit_log(
            action="batch_trash",
            count=len(trashed_items),
            detail=detail,
        )

    return {
        "trashed_count": len(trashed_items),
        "skipped": skipped,
        "remaining": remaining,
    }
