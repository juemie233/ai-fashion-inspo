"""人脸聚合聚类任务：创建与执行（由 worker 进程调用）。

任务类型：``face_cluster``
任务 result：cluster_unmatched_faces 的返回结构（分组统计 + 分组列表），
供扫描页聚合 Tab 展示与展开加载明细。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskQueue
from app.services.task_runners.common import utcnow

logger = logging.getLogger(__name__)


async def create_face_cluster_task(
    db: AsyncSession,
    threshold: float = 0.5,
    min_group_size: int = 2,
) -> TaskQueue:
    """创建「人脸聚合聚类」任务记录，返回任务对象。"""
    task = TaskQueue(
        type="face_cluster",
        status="pending",
        progress=0,
        total=0,
        done=0,
        result={
            "threshold": threshold,
            "min_group_size": min_group_size,
        },
        max_retries=1,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def execute_face_cluster(db: AsyncSession, task: TaskQueue) -> None:
    """执行人脸聚合聚类（由 worker 调用），结果写入任务 result。"""
    from app.services.face_cluster import cluster_unmatched_faces

    task.error = None
    task.progress = 10
    await db.commit()

    params = task.result or {}
    result = await cluster_unmatched_faces(
        db,
        threshold=float(params.get("threshold", 0.5)),
        min_group_size=int(params.get("min_group_size", 2)),
    )

    task.result = result
    task.total = result["total_faces"]
    task.done = result["total_faces"]
    task.progress = 100
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"人脸聚合聚类完成: #{task.id} 人脸 {result['total_faces']} "
        f"（method={result['method']}）组 {result['group_count']} 个"
    )
