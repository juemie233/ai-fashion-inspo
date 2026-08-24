"""网络图分析任务：创建与执行（由 worker 进程调用）。

任务类型：``tag_network_analyze``
任务 result：analyze_tag_network 的返回结构（节点含社区/中心度/桥接标记 + 边 + 社区摘要）。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskQueue
from app.services.task_runners.common import utcnow

logger = logging.getLogger(__name__)


async def create_tag_network_analyze_task(
    db: AsyncSession,
    limit: int = 100,
    min_count: int = 2,
    category: str | None = None,
    with_communities: bool = True,
    with_centrality: bool = True,
    max_edges_per_node: int = 0,
) -> TaskQueue:
    """创建「网络图分析」任务记录，返回任务对象。"""
    task = TaskQueue(
        type="tag_network_analyze",
        status="pending",
        progress=0,
        total=0,
        done=0,
        result={
            "limit": limit,
            "min_count": min_count,
            "category": category,
            "with_communities": with_communities,
            "with_centrality": with_centrality,
            "max_edges_per_node": max_edges_per_node,
        },
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def execute_tag_network_analyze(db: AsyncSession, task: TaskQueue) -> None:
    """执行网络图分析（由 worker 调用），结果写入任务 result。"""
    from app.services.tag_graph import analyze_tag_network

    task.error = None
    task.progress = 10
    await db.commit()

    params = task.result or {}
    result = await analyze_tag_network(
        db,
        limit=int(params.get("limit", 100)),
        min_count=int(params.get("min_count", 2)),
        category=params.get("category"),
        with_communities=bool(params.get("with_communities", True)),
        with_centrality=bool(params.get("with_centrality", True)),
        max_edges_per_node=int(params.get("max_edges_per_node", 0)),
    )

    task.result = result
    task.progress = 100
    task.updated_at = utcnow()
    await db.commit()
    logger.info(f"网络图分析完成: #{task.id} 节点 {len(result['nodes'])} 个")
