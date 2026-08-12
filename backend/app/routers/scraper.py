"""采集引擎管理的 REST API 路由。"""

import asyncio
import json
import logging
import socket

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.scraper import ScraperTask
from app.schemas.scraper import ScraperTaskCreate, ScraperTaskOut

router = APIRouter(prefix="/api/scraper", tags=["scraper"])

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

    subprocess.Popen(
        [sys.executable, str(script), str(task_id)],
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )


@router.get("/sources")
async def scraper_sources():
    """列出所有可用的采集源及其状态。"""
    return {
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

    task = ScraperTask(
        platform=data.platform,
        status="pending",
        config=json.dumps(
            {
                "keywords": data.keywords,
                "max_count": data.max_count,
                "headless": data.headless,
                "cdp_port": data.cdp_port,
                "cookie_file": data.cookie_file,
            }
        ),
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    # 后台启动采集（子进程模式，完全隔离 Playwright）
    _launch_scraper_process(task.id)

    return ScraperTaskOut.model_validate(task)


@router.get("/tasks", response_model=list[ScraperTaskOut])
async def list_scraper_tasks(db: AsyncSession = Depends(get_db)):
    """获取最近的采集任务列表（最多20条）。"""
    result = await db.execute(
        select(ScraperTask).order_by(ScraperTask.created_at.desc()).limit(20)
    )
    tasks = result.scalars().all()
    return [ScraperTaskOut.model_validate(t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=ScraperTaskOut)
async def get_scraper_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """获取指定采集任务的状态。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="采集任务未找到")
    return ScraperTaskOut.model_validate(task)


@router.delete("/tasks", status_code=status.HTTP_200_OK)
async def clear_all_scraper_tasks(db: AsyncSession = Depends(get_db)):
    """清空所有采集任务历史记录。"""
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


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_scraper_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """取消一个等待中或正在运行的采集任务。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="采集任务未找到")
    if task.status in ("pending", "running"):
        task.status = "cancelled"
        await db.flush()


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
