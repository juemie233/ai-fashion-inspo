"""采集引擎管理的 REST API 路由。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.scraper import ScraperTaskCreate, ScraperTaskOut
from app.services import scraper_service
from app.services.chrome_manager import chrome_manager

router = APIRouter(prefix="/api/scraper", tags=["scraper"])


@router.get("/sources")
async def scraper_sources():
    """列出所有可用的采集源及其状态。"""
    return await scraper_service.get_scraper_sources()


@router.get("/cdp-check/{port}")
async def check_cdp_endpoint(port: int):
    """检查指定端口的 Chrome 调试连接是否就绪。"""
    return await scraper_service.check_cdp(port)


# ============ Chrome 生命周期管理 ============


@router.post("/chrome/start")
async def chrome_start():
    """由后端拉起采集专用 Chrome（调试模式）。"""
    return chrome_manager.start()


@router.post("/chrome/stop")
async def chrome_stop():
    """停止由后端拉起的采集专用 Chrome。"""
    return chrome_manager.stop()


@router.get("/chrome/status")
async def chrome_status():
    """查询采集专用 Chrome 的连接状态。"""
    return chrome_manager.status()


# ============ Cookie 管理 ============


@router.get("/cookie-status")
async def cookie_status(platform: str = "xiaohongshu"):
    """检查指定平台的 Cookie 文件状态。"""
    return await scraper_service.get_cookie_status(platform)


@router.post("/cookie-import")
async def cookie_import(payload: dict):
    """导入平台 Cookie（JSON 格式，自动校验平台合法性）。"""
    return await scraper_service.import_cookies(payload)


# ============ 任务日志 ============


@router.get("/tasks/{task_id}/log")
async def task_log(task_id: int):
    """获取采集任务的日志内容（最近 200 行）。"""
    return await scraper_service.get_task_log(task_id)


# ============ 任务取消 ============


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """取消运行中或等待中的采集任务（发送终止信号给子进程）。"""
    return await scraper_service.cancel_scraper_task(db, task_id)


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
    task = await scraper_service.create_scraper_task(db, data)
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
    tasks = await scraper_service.list_scraper_tasks(
        db, platform, status, sort, page, size
    )
    return [ScraperTaskOut.model_validate(t) for t in tasks]


@router.delete("/tasks/{task_id}", status_code=status.HTTP_200_OK)
async def delete_single_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """物理删除单条采集任务（素材的 scraper_task_id 自动置 NULL，不删除素材）。"""
    return await scraper_service.delete_single_scraper_task(db, task_id)


@router.delete("/tasks", status_code=status.HTTP_200_OK)
async def clear_all_scraper_tasks(db: AsyncSession = Depends(get_db)):
    """物理删除所有采集任务历史记录。"""
    return await scraper_service.clear_all_scraper_tasks(db)


@router.post("/tasks/retry-failed")
async def retry_failed_scraper_tasks(db: AsyncSession = Depends(get_db)):
    """重试所有失败的采集任务，使用相同配置重新创建任务。"""
    return await scraper_service.retry_failed_scraper_tasks(db)


@router.post("/tasks/{task_id}/retry")
async def retry_single_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """重试单个失败任务，沿用断点续采（不重复采集已处理内容）。"""
    return await scraper_service.retry_single_task(db, task_id)


@router.get("/tasks/{task_id}/results")
async def task_results(
    task_id: int,
    page: int = 1,
    size: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """获取指定采集任务产出的素材列表（缩略图网格）。"""
    return await scraper_service.get_task_results(db, task_id, page, size)


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
    return await scraper_service.batch_delete_task_results(db, task_id, ids)
