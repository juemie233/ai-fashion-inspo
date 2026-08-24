"""自动聚类任务：创建与执行（由 worker 进程调用）。

任务类型：``tag_cluster_scan``
任务 result：scan_tag_clusters 的返回结构（候选合并组列表），
供前端展示候选组与 apply 时解析成员。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskQueue
from app.services.task_runners.common import utcnow

logger = logging.getLogger(__name__)


async def create_tag_cluster_scan_task(
    db: AsyncSession,
    threshold: float = 0.75,
    use_cooccurrence_boost: bool = True,
    min_group_size: int = 2,
) -> TaskQueue:
    """创建「自动聚类」任务记录，返回任务对象。"""
    task = TaskQueue(
        type="tag_cluster_scan",
        status="pending",
        progress=0,
        total=0,
        done=0,
        result={
            "threshold": threshold,
            "use_cooccurrence_boost": use_cooccurrence_boost,
            "min_group_size": min_group_size,
        },
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def execute_tag_cluster_scan(db: AsyncSession, task: TaskQueue) -> None:
    """执行自动聚类扫描（由 worker 调用），结果写入任务 result。"""
    from app.services.tag_cluster import scan_tag_clusters

    task.error = None
    task.progress = 10
    await db.commit()

    params = task.result or {}
    result = await scan_tag_clusters(
        db,
        threshold=float(params.get("threshold", 0.75)),
        use_cooccurrence_boost=bool(params.get("use_cooccurrence_boost", True)),
        min_group_size=int(params.get("min_group_size", 2)),
    )

    task.result = result
    task.progress = 100
    task.updated_at = utcnow()
    await db.commit()
    logger.info(f"自动聚类完成: #{task.id} 候选组 {result['total']} 个")
