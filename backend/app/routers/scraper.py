"""采集引擎管理的 REST API 路由。"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.scraper import ScraperTask
from app.schemas.scraper import ScraperTaskCreate, ScraperTaskOut

router = APIRouter(prefix="/api/scraper", tags=["scraper"])


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


@router.post(
    "/tasks", response_model=ScraperTaskOut, status_code=status.HTTP_201_CREATED
)
async def create_scraper_task(
    data: ScraperTaskCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建并启动一个新的采集任务。"""
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
