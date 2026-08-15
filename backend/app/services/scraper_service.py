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
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.scraper import ScraperSeenURL, ScraperTask
from app.schemas.scraper import ScraperTaskCreate

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


def utcnow() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _launch_scraper_process(task_id: int):
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

    def _reap():
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


def _maybe_auto_retry(task_id: int):
    """任务子进程异常退出时，若任务仍可续采且未超重试上限，自动重新拉起。"""
    async def _load():
        async with async_session() as db:
            return await db.get(ScraperTask, task_id)
    try:
        task = asyncio.run(_load())
    except Exception:
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


def _validate_cookie_platform(platform: str) -> str:
    """校验并标准化平台名，防止路径穿越。"""
    p = platform.strip().lower()
    if p not in _COOKIE_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}，允许: {_COOKIE_PLATFORMS}")
    return p


async def check_cdp(port: int) -> dict:
    """检查指定端口的 Chrome 调试连接是否就绪。"""
    ok, detail, is_chrome = _check_cdp(port)
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
    return {"message": f"已导入 {platform} Cookie", "platform": platform}


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


# ============ 任务 CRUD ============


async def create_scraper_task(db: AsyncSession, data: ScraperTaskCreate) -> ScraperTask:
    """创建并启动一个新的采集任务。

    CDP 模式下会预先检测 Chrome 调试端口，不可用时返回明确的错误提示。
    """
    # CDP 模式：预检 Chrome 调试端口
    if data.cdp_port is not None:
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
) -> list[ScraperTask]:
    """获取采集任务列表，支持筛选和排序。"""
    query = select(ScraperTask)

    if platform:
        query = query.where(ScraperTask.platform == platform)
    if status:
        query = query.where(ScraperTask.status == status)

    # 排序
    sort_map = {
        "newest": ScraperTask.created_at.desc(),
        "oldest": ScraperTask.created_at.asc(),
        "most_found": ScraperTask.items_found.desc(),
        "most_added": ScraperTask.items_added.desc(),
    }
    query = query.order_by(sort_map.get(sort, ScraperTask.created_at.desc()))
    query = query.offset((page - 1) * size).limit(size)

    result = await db.execute(query)
    tasks = result.scalars().all()
    return list(tasks)


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

    # 计数
    count_result = await db.execute(
        select(func.count(Inspiration.id)).where(
            Inspiration.scraper_task_id == task_id
        )
    )
    total = count_result.scalar() or 0

    # 分页
    items_result = await db.execute(
        select(Inspiration)
        .where(Inspiration.scraper_task_id == task_id)
        .order_by(Inspiration.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    items = items_result.scalars().all()

    def _fmt(dt):
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ') if dt else None

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
) -> dict:
    """批量删除采集任务产出的指定素材。

    请求体: {"ids": ["id1", "id2", ...]}
    """
    if not ids:
        raise HTTPException(status_code=400, detail="请提供要删除的素材 ID 列表")

    # 仅删除属于该任务的素材
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.thumbnail_path, Inspiration.source_url)
        .where(
            Inspiration.id.in_(ids),
            Inspiration.scraper_task_id == task_id,
        )
    )
    files_to_delete = result.all()

    storage_root = settings.storage_root
    freed_bytes = 0
    for _fid, fpath, thumb, _surl in files_to_delete:
        for p in (fpath, thumb):
            if p:
                full = storage_root / p
                try:
                    if full.exists():
                        freed_bytes += full.stat().st_size
                        full.unlink()
                except Exception:
                    pass

    # 写入墓碑表（防止重复采集）
    urls_to_seal = [r[3] for r in files_to_delete if r[3]]
    if urls_to_seal:
        for url in urls_to_seal:
            await db.execute(
                sqlite_insert(ScraperSeenURL)
                .values(source_url=url)
                .prefix_with("OR IGNORE")
            )

    # 从数据库删除（级联删除关联 tags 和 analysis_logs）
    deleted_ids = [r[0] for r in files_to_delete]
    await db.execute(
        Inspiration.__table__.delete().where(Inspiration.id.in_(deleted_ids))
    )
    await db.commit()

    # 同步删除向量库中的文本/图像向量（LanceDB 未安装时静默跳过），
    # 避免批量删除后产生孤儿向量
    from app.services import vector_store

    await vector_store.delete_inspiration_vectors_batch(deleted_ids)

    # 更新任务计数
    remaining_result = await db.execute(
        select(func.count(Inspiration.id)).where(
            Inspiration.scraper_task_id == task_id
        )
    )
    remaining = remaining_result.scalar() or 0

    task = await db.get(ScraperTask, task_id)
    if task:
        task.items_added = remaining
        await db.commit()

    return {
        "deleted_count": len(files_to_delete),
        "freed_bytes": freed_bytes,
        "remaining": remaining,
    }
