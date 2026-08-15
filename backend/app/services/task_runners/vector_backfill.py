"""向量回填任务：为新入库素材自动生成文本/图像向量。

本模块包含「向量回填」（vector_backfill）任务的创建与执行逻辑，
由 worker 进程（app/worker.py）通过 TASK_HANDLERS 分发表调度。

适用场景:
- 手动上传 / URL 导入 / 采集入库后，为素材补建向量（文本向量需等
  标签生成后才有内容，无标签时自动跳过，仅生成图像向量）
- AI 分析完成后的向量重建由 analyze_image 直接调用
  rebuild_inspiration_vectors，不走本队列（分析本身已是后台任务）

向量生成内部均静默降级（LanceDB 未安装 / CLIP 不可用 / Ollama 不可用 /
素材已删除时返回 False 不抛错），因此本任务不会因向量能力缺失而失败，
只影响统计计数。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services.task_runners.common import _utcnow
from app.services.vector_service import rebuild_inspiration_vectors

logger = logging.getLogger(__name__)


async def create_vector_backfill_task(
    db: AsyncSession, inspiration_ids: list[str]
) -> TaskQueue | None:
    """创建「向量回填」任务记录（去重、过滤已不存在的素材），返回任务对象。

    参数:
        db: 数据库会话
        inspiration_ids: 待回填向量的素材 ID 列表

    返回:
        新建的任务记录；无有效素材时返回 None（调用方无需入队）。
    """
    ids = list(dict.fromkeys(inspiration_ids))
    if not ids:
        return None

    # 过滤已不存在的素材，避免无谓入队
    result = await db.execute(
        select(Inspiration.id).where(Inspiration.id.in_(ids))
    )
    existing_ids = [i for i in ids if i in {row[0] for row in result.all()}]
    if not existing_ids:
        return None

    task = TaskQueue(
        type="vector_backfill",
        status="pending",
        progress=0,
        total=len(existing_ids),
        done=0,
        result={"inspiration_ids": existing_ids},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    logger.info(f"已创建向量回填任务: #{task.id}，{len(existing_ids)} 个素材")
    return task


async def execute_vector_backfill(db: AsyncSession, task: TaskQueue) -> None:
    """执行向量回填任务：逐条重建素材的文本/图像向量并维护进度。

    参数:
        db: 任务生命周期会话（用于更新任务进度与状态）
        task: 任务记录

    说明:
        - 素材在执行期间被删除时，rebuild_* 内部按「不存在」静默返回，
          不影响任务完成。
        - 任务幂等：upsert 语义，重复执行不会产生重复向量。
    """
    payload = task.result or {}
    inspiration_ids = payload.get("inspiration_ids") or []
    if not inspiration_ids:
        # 空任务：无素材可回填，直接标记完成
        task.total = 0
        task.done = 0
        task.progress = 100
        task.error = None
        await db.commit()
        return

    text_done = 0
    text_skipped = 0
    image_done = 0
    image_skipped = 0

    total = len(inspiration_ids)
    for idx, insp_id in enumerate(inspiration_ids, start=1):
        stats = await rebuild_inspiration_vectors(db, insp_id)
        if stats["text"]:
            text_done += 1
        else:
            text_skipped += 1
        if stats["image"]:
            image_done += 1
        else:
            image_skipped += 1

        task.done = idx
        task.progress = round(idx / total * 100)
        task.updated_at = _utcnow()
        await db.commit()
        logger.info(
            f"向量回填进度: #{task.id} {task.progress}% ({idx}/{total})"
        )

    task.result = {
        "inspiration_ids": inspiration_ids,
        "text_done": text_done,
        "text_skipped": text_skipped,
        "image_done": image_done,
        "image_skipped": image_skipped,
    }
    task.done = total
    task.progress = 100
    task.error = None
    task.updated_at = _utcnow()
    await db.commit()
    logger.info(
        f"向量回填任务执行完毕: #{task.id} "
        f"文本 {text_done}（跳过 {text_skipped}），"
        f"图像 {image_done}（跳过 {image_skipped}）"
    )
