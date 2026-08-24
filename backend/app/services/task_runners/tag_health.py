"""标签健康度扫描任务：创建与执行（由 worker 进程调用）。

任务类型：``tag_health_scan``
任务 result：scan_tag_health 的返回结构（评分 + 各问题紧凑 ID 列表），
供 ``GET /api/tags/health/{issue_type}`` 分页读取明细。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskQueue
from app.services.task_runners.common import utcnow

logger = logging.getLogger(__name__)


async def create_tag_health_scan_task(
    db: AsyncSession, duplicate_threshold: float = 0.75
) -> TaskQueue:
    """创建「标签健康度扫描」任务记录，返回任务对象。

    扫描无需预加载标签：由 worker 执行时全库计算，
    创建时 total 未知（设为 0，执行阶段再更新）。
    """
    task = TaskQueue(
        type="tag_health_scan",
        status="pending",
        progress=0,
        total=0,
        done=0,
        result={"duplicate_threshold": duplicate_threshold},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def execute_tag_health_scan(db: AsyncSession, task: TaskQueue) -> None:
    """执行标签健康度扫描（由 worker 调用），结果写入任务 result。"""
    from app.services.tag_health import scan_tag_health

    task.error = None
    task.progress = 10
    await db.commit()

    threshold = float((task.result or {}).get("duplicate_threshold", 0.75))
    result = await scan_tag_health(db, duplicate_threshold=threshold)

    task.result = result
    task.progress = 100
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"标签健康度扫描完成: #{task.id} 标签 {result['total']} 个，评分 {result['score']}"
    )
