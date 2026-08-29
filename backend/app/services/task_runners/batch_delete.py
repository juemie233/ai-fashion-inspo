"""批量删除任务：删文件、写墓碑、删数据库记录。

本模块包含「批量删除」（batch_delete）任务的创建与执行逻辑，
由 worker 进程（app/worker.py）通过 TASK_HANDLERS 分发表调度。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services.file_service import delete_files_counting
from app.services.scraper_seen_service import seal_urls
from app.services.task_runners.common import (
    _broadcast_task_event,
    _delete_inspiration_vectors,
    utcnow,
)

logger = logging.getLogger(__name__)


async def create_batch_delete_task(
    db: AsyncSession, inspiration_ids: list[str], label: str = ""
) -> TaskQueue:
    """创建「批量删除」任务记录，返回任务对象（供 API 创建任务后返回 task_id）。

    参数:
        db: 数据库会话
        inspiration_ids: 待删除的素材 ID 列表（API 层已按条件解析）
        label: 删除类型标签（untagged / analysis_failed / ids），用于完成后提示

    返回:
        新建的任务记录
    """
    task = TaskQueue(
        type="batch_delete",
        status="pending",
        # 清理类任务设为低优先级：删文件/删记录不紧急，不应插队阻塞
        # 批量分析等分析链路任务（worker 认领按 priority DESC, id ASC）
        priority=-5,
        progress=0,
        total=len(inspiration_ids),
        done=0,
        result={"inspiration_ids": inspiration_ids, "label": label},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def execute_batch_delete(db: AsyncSession, task: TaskQueue) -> None:
    """执行批量删除任务：删文件、写墓碑、删数据库记录（由 worker 调用）。

    单个素材删除是确定性操作，文件缺失跳过即可，不抛可恢复错误。
    """
    payload = task.result or {}
    inspiration_ids = payload.get("inspiration_ids") or []
    if not inspiration_ids:
        task.total = 0
        task.done = 0
        task.progress = 100
        # 空 ID 也给出与正常路径一致的 result 结构，前端据此展示「已删除 0 个」
        task.result = {
            "inspiration_ids": [],
            "label": payload.get("label", ""),
            "deleted_count": 0,
            "freed_bytes": 0,
        }
        await db.commit()
        return

    # 查待删除素材的文件路径与来源 URL
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.thumbnail_path, Inspiration.source_url)
        .where(Inspiration.id.in_(inspiration_ids))
    )
    files_to_delete = result.all()

    # 崩溃窗口防护：把「待清理清单」先写入任务 result 并提交。
    # 若 worker 在「删 DB 行 → 删文件/向量」之间崩溃，重试时行已不存在、
    # 查询不到清单，文件/向量会永久残留；预存清单后重试可从 result 恢复清理。
    pending_cleanup = [
        {"id": r[0], "file_path": r[1], "thumbnail_path": r[2]}
        for r in files_to_delete
    ]
    if pending_cleanup:
        task.result = {
            "inspiration_ids": inspiration_ids,
            "label": payload.get("label", ""),
            "pending_cleanup": pending_cleanup,
        }
        await db.commit()

    # 写入墓碑表（防止被删除素材的 URL 被重新采集）
    urls_to_seal = [r[3] for r in files_to_delete if r[3]]
    await seal_urls(db, urls_to_seal)

    # 先提交数据库删除（级联删除关联 tags 与 analysis_logs），再删磁盘文件，
    # 降低「文件已删但 DB 未删」的不一致窗口
    deleted_ids = [r[0] for r in files_to_delete]
    await db.execute(
        Inspiration.__table__.delete().where(Inspiration.id.in_(deleted_ids))
    )
    await db.commit()

    # 删除 LanceDB 向量，避免孤儿向量（由 vector_store 提供，未安装时静默返回）
    await _delete_inspiration_vectors(deleted_ids)

    # 清理视频素材的关键帧目录（非视频目录不存在，幂等；失败仅记日志）
    from app.services.video_service import cleanup_keyframes_batch

    await cleanup_keyframes_batch(deleted_ids)

    # 重跑兜底：正常查询可能已无行（首次执行已删），从 result 恢复清理清单
    cleanup_list = pending_cleanup or (task.result or {}).get("pending_cleanup") or []
    freed_bytes = 0
    for entry in cleanup_list:
        freed_bytes += delete_files_counting(
            entry.get("file_path"), entry.get("thumbnail_path")
        )

    task.result = {
        "inspiration_ids": inspiration_ids,
        "label": payload.get("label", ""),
        "deleted_count": len(deleted_ids),
        "freed_bytes": freed_bytes,
    }
    task.done = task.total
    task.progress = 100
    task.updated_at = utcnow()
    await db.commit()
    # 本任务无逐项进度循环，仅在完成点广播一次进度事件（终态 success 由 worker 统一广播）
    await _broadcast_task_event(task, "progress")
    logger.info(f"批量删除任务完成: #{task.id} 删除 {len(deleted_ids)} 个素材")
