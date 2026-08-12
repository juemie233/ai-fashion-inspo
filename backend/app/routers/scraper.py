"""采集引擎管理的 REST API 路由。"""

import asyncio
import json
import logging
import socket

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.scraper import ScraperTask
from app.schemas.scraper import ScraperTaskCreate, ScraperTaskOut

router = APIRouter(prefix="/api/scraper", tags=["scraper"])

# 运行中子进程 PID 映射
_scraper_pids: dict[int, int] = {}  # task_id → pid

# Chrome 调试模式启动命令模板（路径从配置读取）
CHROME_DEBUG_CMD = (
    '"{chrome}" '
    "--remote-debugging-port={port} "
    '--user-data-dir="{data_dir}"'
)


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
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).parent.parent.parent / "scripts" / "run_scraper.py"

    # 日志输出到文件，方便排查
    logs_dir = Path(__file__).parent.parent.parent / "storage" / "logs" / "scraper"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_f = open(logs_dir / f"task_{task_id}.log", "w", encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, "-u", str(script), str(task_id)],
        stdout=log_f,
        stderr=subprocess.STDOUT,
        env={**__import__("os").environ, "PYTHONUNBUFFERED": "1"},
    )
    _scraper_pids[task_id] = proc.pid


@router.get("/sources")
async def scraper_sources():
    """列出所有可用的采集源及其状态。"""
    from app.models.scraper import ScraperSeenURL
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


@router.get("/cdp-check/{port}")
async def check_cdp_endpoint(port: int):
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


# ============ Cookie 管理 ============

# Cookie 平台白名单
_COOKIE_PLATFORMS = {"xiaohongshu", "douyin"}


def _validate_cookie_platform(platform: str) -> str:
    """校验并标准化平台名，防止路径穿越。"""
    p = platform.strip().lower()
    if p not in _COOKIE_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}，允许: {_COOKIE_PLATFORMS}")
    return p


@router.get("/cookie-status")
async def cookie_status(platform: str = "xiaohongshu"):
    """检查指定平台的 Cookie 文件状态。"""
    from pathlib import Path
    from datetime import datetime

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


@router.post("/cookie-import")
async def cookie_import(payload: dict):
    """导入平台 Cookie（JSON 格式，自动校验平台合法性）。"""
    from pathlib import Path

    platform = _validate_cookie_platform(payload.get("platform", "xiaohongshu"))
    cookie_data = payload.get("cookies")

    if not cookie_data:
        raise HTTPException(status_code=400, detail="请提供 Cookie 数据")

    cookie_dir = Path(settings.storage_root) / "cookies"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = cookie_dir / f"{platform}_cookies.json"

    cookie_file.write_text(json.dumps(cookie_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"message": f"已导入 {platform} Cookie", "platform": platform}


# ============ 任务日志 ============


@router.get("/tasks/{task_id}/log")
async def task_log(task_id: int):
    """获取采集任务的日志内容（最近 200 行）。"""
    from pathlib import Path

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


# ============ 任务取消 ============


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: int, db: AsyncSession = Depends(get_db)):
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
    pid = _scraper_pids.pop(task_id, None)
    if pid:
        try:
            import signal
            os.kill(pid, signal.SIGTERM)
            logger.info(f"已发送 SIGTERM 给采集进程 PID={pid} (task {task_id})")
        except OSError:
            pass  # 进程已退出

    return {"message": f"任务 {task_id} 已取消"}


# ============ 创建任务 ============


@router.post(
    "/tasks", response_model=ScraperTaskOut, status_code=status.HTTP_201_CREATED
)
async def create_scraper_task(
    data: ScraperTaskCreate,
    db: AsyncSession = Depends(get_db),
):
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

    return ScraperTaskOut.model_validate(task)


@router.get("/tasks", response_model=list[ScraperTaskOut])
async def list_scraper_tasks(
    platform: str | None = None,
    status: str | None = None,
    sort: str = "newest",  # newest | oldest | most_found | most_added
    page: int = 1,
    size: int = 50,
    db: AsyncSession = Depends(get_db),
):
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
    return [ScraperTaskOut.model_validate(t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=ScraperTaskOut)
async def get_scraper_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """获取指定采集任务的状态。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="采集任务未找到")
    return ScraperTaskOut.model_validate(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_200_OK)
async def delete_single_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """物理删除单条采集任务（素材的 scraper_task_id 自动置 NULL，不删除素材）。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="采集任务未找到")
    await db.delete(task)
    await db.commit()
    return {"deleted": 1, "id": task_id}


@router.delete("/tasks", status_code=status.HTTP_200_OK)
async def clear_all_scraper_tasks(db: AsyncSession = Depends(get_db)):
    """物理删除所有采集任务历史记录。"""
    from sqlalchemy import delete

    result = await db.execute(delete(ScraperTask))
    await db.commit()
    return {"deleted": result.rowcount}


@router.post("/tasks/retry-failed")
async def retry_failed_scraper_tasks(db: AsyncSession = Depends(get_db)):
    """重试所有失败的采集任务，使用相同配置重新创建任务。"""
    result = await db.execute(
        select(ScraperTask).where(ScraperTask.status == "failed")
    )
    failed_tasks = result.scalars().all()

    if not failed_tasks:
        raise HTTPException(status_code=404, detail="没有失败的采集任务")

    retried = 0
    for task in failed_tasks:
        new_task = ScraperTask(
            platform=task.platform,
            status="pending",
            config=task.config,
        )
        db.add(new_task)
        await db.flush()
        await db.refresh(new_task)
        _launch_scraper_process(new_task.id)
        retried += 1

    await db.commit()
    return {"retried": retried, "message": f"已重新创建 {retried} 个采集任务"}


@router.get("/tasks/{task_id}/results")
async def task_results(
    task_id: int,
    page: int = 1,
    size: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """获取指定采集任务产出的素材列表（缩略图网格）。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="采集任务未找到")

    from app.models.inspiration import Inspiration

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
                "source_url": i.source_url,
                "is_favorite": i.is_favorite,
                "created_at": str(i.created_at) if i.created_at else None,
            }
            for i in items
        ],
    }


@router.post("/tasks/{task_id}/results/batch-delete")
async def task_results_batch_delete(
    task_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """批量删除采集任务产出的指定素材。

    请求体: {"ids": ["id1", "id2", ...]}
    """
    ids = payload.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="请提供要删除的素材 ID 列表")

    from app.models.inspiration import Inspiration

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
    for fid, fpath, thumb, _surl in files_to_delete:
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
        from app.models.scraper import ScraperSeenURL
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        for url in urls_to_seal:
            await db.execute(
                sqlite_insert(ScraperSeenURL)
                .values(source_url=url)
                .prefix_with("OR IGNORE")
            )

    # 从数据库删除（级联删除关联 tags 和 analysis_logs）
    await db.execute(
        Inspiration.__table__.delete().where(
            Inspiration.id.in_([r[0] for r in files_to_delete])
        )
    )
    await db.commit()

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


async def _run_scraper(task_id: int):
    """后台任务：通过子进程执行采集，隔离 Playwright 避免事件循环冲突。"""
    import logging
    import subprocess
    import sys
    from pathlib import Path

    logger = logging.getLogger(__name__)
    logger.info(f"采集任务 {task_id} 开始执行（子进程模式）")

    script = Path(__file__).parent.parent.parent / "scripts" / "run_scraper.py"
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script), str(task_id),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout:
            logger.info(f"采集任务 {task_id} 输出: {stdout.decode(errors='replace')[:500]}")
        if stderr:
            logger.warning(f"采集任务 {task_id} 错误: {stderr.decode(errors='replace')[:500]}")
        logger.info(f"采集任务 {task_id} 子进程退出码: {proc.returncode}")
    except Exception as e:
        logger.error(f"采集任务 {task_id} 启动失败: {e}")
