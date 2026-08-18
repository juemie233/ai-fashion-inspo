"""向量回填任务：为新入库素材自动生成文本/图像向量。

本模块包含「向量回填」（vector_backfill）任务的创建与执行逻辑，
由 worker 进程（app/worker.py）通过 TASK_HANDLERS 分发表调度。

批量触发策略（攒批机制，替代「每素材一个任务」）：
- 素材入库 / 裁剪 / 标签变更等场景不再立即创建任务，而是调用
  enqueue_vector_backfills 把素材 ID 登记进待回填表
  （pending_vector_backfills，SQLite 持久化，进程重启不丢失）。
- 待回填素材累计达到 VECTOR_BACKFILL_BATCH_SIZE（100）时，
  flush_pending_vector_backfills 自动创建 1 个批量任务（total=实际数量）。
- 未达阈值时素材保留在待回填表：用户手动触发一键回填（admin 接口）或
  worker 启动兜底时会立即 flush，保证所有素材最终都能被回填、不丢失。
- AI 分析完成后的向量重建由 analyze_image 直接调用
  rebuild_inspiration_vectors（分析本身已是后台任务），不走本队列。

向量生成内部均静默降级（LanceDB 未安装 / CLIP 不可用 / Ollama 不可用 /
素材已删除时返回 False 不抛错），因此本任务不会因向量能力缺失而失败，
只影响统计计数。
"""

import logging

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.task import PendingVectorBackfill, TaskQueue
from app.services.task_runners.common import PermanentTaskError, _chunked, utcnow
from app.services.vector_service import rebuild_inspiration_vectors

logger = logging.getLogger(__name__)

# 批量回填触发阈值：待回填素材累计达到该数量时自动创建 1 个批量任务。
# 避免「每上传/裁剪/标签变更一个素材就创建一个 total=1 小任务」淹没任务队列。
VECTOR_BACKFILL_BATCH_SIZE = 100


async def _filter_existing_ids(
    db: AsyncSession, inspiration_ids: list[str]
) -> list[str]:
    """过滤出仍存在的素材 ID（去重）。

    分批 IN 查询（每批 500）：长 IN 子句（数千变量）在并发连接复用场景下
    实测会出现「查询只返回 1 行」导致任务 total=1 的问题，分批规避。
    """
    ids = list(dict.fromkeys(inspiration_ids))
    existing_ids: list[str] = []
    for chunk in _chunked(ids, 500):
        result = await db.execute(
            select(Inspiration.id).where(Inspiration.id.in_(chunk))
        )
        chunk_ids = {row[0] for row in result.all()}
        existing_ids.extend(i for i in chunk if i in chunk_ids)
    return existing_ids


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
    existing_ids = await _filter_existing_ids(db, inspiration_ids)
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


async def enqueue_vector_backfills(
    db: AsyncSession, inspiration_ids: list[str]
) -> TaskQueue | None:
    """登记待回填素材到攒批队列（幂等，同素材重复登记自动去重）。

    批量触发策略：
    - 素材 ID 写入 pending_vector_backfills 表（SQLite 持久化，重启不丢失），
      不立即创建任务——避免「每分析一个素材就生成一个 total=1 小任务」；
    - 待回填素材累计达到 VECTOR_BACKFILL_BATCH_SIZE（100）时，自动取出全部
      创建一个批量任务（total=实际数量）并清空待回填表；
    - 未达阈值时返回 None：素材保留在待回填表，等待后续攒批 / 手动触发
      一键回填 / worker 启动兜底，保证最终全部回填、不丢失。

    参数:
        db: 数据库会话
        inspiration_ids: 待回填向量的素材 ID 列表

    返回:
        达阈值时返回新建的批量任务；未达阈值或无有效素材时返回 None。
    """
    existing_ids = await _filter_existing_ids(db, inspiration_ids)
    if not existing_ids:
        return None

    # 已登记过的素材跳过（幂等，避免重复行）
    pending_result = await db.execute(
        select(PendingVectorBackfill.inspiration_id).where(
            PendingVectorBackfill.inspiration_id.in_(existing_ids)
        )
    )
    already = set(pending_result.scalars().all())
    new_ids = [i for i in existing_ids if i not in already]
    if new_ids:
        # INSERT ... ON CONFLICT DO NOTHING：并发登记同一素材时静默跳过冲突行，
        # 不会因唯一约束报错（也避免回滚误伤调用方事务中未提交的其它变更）
        stmt = sqlite_insert(PendingVectorBackfill).values(
            [{"inspiration_id": iid} for iid in new_ids]
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["inspiration_id"])
        await db.execute(stmt)
        await db.commit()

    # 累计达到阈值 → 立即创建批量任务（内部会清空待回填表）
    count = (
        await db.execute(select(func.count()).select_from(PendingVectorBackfill))
    ).scalar() or 0
    if count >= VECTOR_BACKFILL_BATCH_SIZE:
        return await flush_pending_vector_backfills(db, force=True)
    return None


async def flush_pending_vector_backfills(
    db: AsyncSession,
    force: bool = False,
    extra_ids: list[str] | None = None,
) -> TaskQueue | None:
    """把攒批队列中的待回填素材（可合并 extra_ids）创建为一个批量任务。

    触发策略：
    - force=True：无论数量多少立即创建任务（手动触发一键回填 / worker 启动
      兜底 / 累计达阈值时调用）；
    - force=False：仅当待回填数量达到阈值时才创建（当前无调用方使用，
      保留参数以备将来周期性触发）。

    先创建任务再删除待回填行：任务创建失败时待回填行保留，下次触发重试，
    保证素材不丢失（向量重建幂等，重复任务无害）。

    参数:
        db: 数据库会话
        force: 是否忽略阈值强制创建任务
        extra_ids: 额外合并的素材 ID（如手动回填时算出的缺失向量素材）

    返回:
        新建的任务记录；无素材可回填时返回 None。
    """
    result = await db.execute(select(PendingVectorBackfill.inspiration_id))
    pending_ids = [row[0] for row in result.all()]
    merged = list(dict.fromkeys([*pending_ids, *(extra_ids or [])]))
    if not merged:
        return None
    if not force and len(merged) < VECTOR_BACKFILL_BATCH_SIZE:
        return None

    task = await create_vector_backfill_task(db, merged)
    if task is not None:
        await db.execute(
            delete(PendingVectorBackfill).where(
                PendingVectorBackfill.inspiration_id.in_(pending_ids)
            )
        )
        await db.commit()
        logger.info(
            f"攒批向量回填已 flush: #{task.id}，{len(pending_ids)} 个待回填素材"
            f"{f' + 额外 {len(merged) - len(pending_ids)} 个' if len(merged) > len(pending_ids) else ''}"
        )
    return task


async def purge_small_backfill_tasks(db: AsyncSession) -> int:
    """清理历史遗留的向量回填「小任务」（total<=1，多为每素材一个的旧任务）。

    幂等操作，可重复执行（Alembic 迁移已清理过时是 no-op）：
    - 非运行中的小任务直接删除，避免继续淹没任务列表与统计；
    - 运行中的小任务标记为 cancelled（不删除执行中的行，避免与 worker 并发写冲突）。

    参数:
        db: 数据库会话

    返回:
        删除的小任务数量。
    """
    result = await db.execute(
        delete(TaskQueue).where(
            TaskQueue.type == "vector_backfill",
            TaskQueue.total <= 1,
            TaskQueue.status != "running",
        )
    )
    await db.execute(
        update(TaskQueue)
        .where(
            TaskQueue.type == "vector_backfill",
            TaskQueue.total <= 1,
            TaskQueue.status == "running",
        )
        .values(
            status="cancelled",
            error="历史小任务已清理（批量回填机制上线）",
            updated_at=utcnow(),
        )
    )
    await db.commit()
    if result.rowcount:
        logger.info(f"已清理 {result.rowcount} 个历史向量回填小任务")
    return result.rowcount


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
    image_skipped = 0  # 非图片素材正常跳过
    image_failed = 0  # 图片素材但图像向量生成失败（CLIP 不可用/文件缺失/写入失败等）

    total = len(inspiration_ids)
    for idx, insp_id in enumerate(inspiration_ids, start=1):
        insp = await db.get(Inspiration, insp_id)
        is_image = insp is not None and insp.media_type == "image"
        stats = await rebuild_inspiration_vectors(db, insp_id)
        if stats["text"]:
            text_done += 1
        else:
            text_skipped += 1
        if stats["image"]:
            image_done += 1
        elif is_image:
            image_failed += 1
        else:
            image_skipped += 1

        task.done = idx
        task.progress = round(idx / total * 100)
        task.updated_at = utcnow()
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
        "image_failed": image_failed,
    }
    # 统计结果先落库：即使下面判定失败抛出任务级异常，失败详情也能在任务记录中查到
    await db.commit()

    # 防假成功：存在图片素材但图像向量全部生成失败（系统性故障，如 CLIP 不可用 /
    # LanceDB 未安装 / 图片文件缺失），任务不能冒充「完成」，交由 worker 标记失败。
    # 判定在写「完成态」之前：异常抛出时任务仍为 running，避免「先 commit 完成态
    # 再抛异常」在进程崩溃时残留假完成。
    if image_done == 0 and image_failed > 0:
        detail = (
            f"向量回填失败：{image_failed} 个图片素材的图像向量全部生成失败"
            f"（成功 {image_done}）。常见原因：CLIP 模型不可用、LanceDB 未安装、"
            f"图片文件缺失或写入失败"
        )
        raise PermanentTaskError(detail)

    task.done = total
    task.progress = 100
    task.error = None
    task.updated_at = utcnow()
    await db.commit()

    logger.info(
        f"向量回填任务执行完毕: #{task.id} "
        f"文本 {text_done}（跳过 {text_skipped}），"
        f"图像 {image_done}（跳过 {image_skipped}，失败 {image_failed}）"
    )
