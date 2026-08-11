"""采集引擎管理的 REST API 路由。"""

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.scraper import ScraperTask
from app.schemas.scraper import ScraperTaskCreate, ScraperTaskOut

router = APIRouter(prefix="/api/scraper", tags=["scraper"])


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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """创建并启动一个新的采集任务。"""
    task = ScraperTask(
        platform=data.platform,
        status="pending",
        config=json.dumps(
            {"keywords": data.keywords, "max_count": data.max_count}
        ),
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    # 后台启动采集
    background_tasks.add_task(_run_scraper, task.id)

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
    """后台任务：根据平台执行采集。"""
    from app.services.scraper_service import run_scraper_task

    logger = logging.getLogger(__name__)
    logger.info(f"采集任务 {task_id} 开始执行")
    try:
        await run_scraper_task(task_id)
    except Exception as e:
        logger.error(f"采集任务 {task_id} 异常: {e}")
