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
import random

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import Inspiration
from app.models.task import PendingVectorBackfill, TaskQueue
from app.services.task_runners.common import (
    PermanentTaskError,
    _broadcast_task_event,
    _chunked,
    utcnow,
)
from app.services.vector import store as vector_store
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
    db: AsyncSession, inspiration_ids: list[str], mode: str = "all"
) -> TaskQueue | None:
    """创建「向量回填」任务记录（去重、过滤已不存在的素材），返回任务对象。

    参数:
        db: 数据库会话
        inspiration_ids: 待回填向量的素材 ID 列表
        mode: "all"（文本+图像）| "text"（仅文本，用于公式版本升级后的全量重建）

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
        result={"inspiration_ids": existing_ids, "mode": mode},
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

    事务边界（重要）:
        - 登记本身**不提交**：pending 行随调用方事务一并落库（由调用方统一
          commit / rollback），避免 helper 隐式提交调用方未完成的变更
          （如新素材行、标签合并结果）；
        - 达阈值触发的 flush 内部会提交（任务创建必须落库），此时登记行与
          任务在同一提交点完成，调用方无需额外 commit。
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
        # 注意：此处不 commit——pending 行与调用方事务同生共死，由调用方统一提交

    # 累计达到阈值 → 立即创建批量任务（flush 内部提交并清空待回填表）
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

    幂等操作，可重复执行（Alembic 迁移已清理过时是 no-op）。

    边界约定（避免误删攒批新机制的任务）：
    - 仅清理**已终态**（success/failed/cancelled）的小任务：历史遗留小任务
      绝大多数已完成，直接删除不再淹没任务列表与统计；
    - pending 的小任务一律保留：可能是攒批 flush 刚创建的合法任务
      （待回填素材恰好 1 条时 total=1），删除会导致该素材向量永久缺失；
    - running 的小任务不处理：由 worker 心跳租约机制（_reset_stale_tasks）
      负责，多 worker 部署时另一实例启动不能取消存活 worker 正在执行的任务。

    参数:
        db: 数据库会话

    返回:
        删除的小任务数量。
    """
    result = await db.execute(
        delete(TaskQueue).where(
            TaskQueue.type == "vector_backfill",
            TaskQueue.total <= 1,
            TaskQueue.status.in_(["success", "failed", "cancelled"]),
        )
    )
    await db.commit()
    if result.rowcount:
        logger.info(f"已清理 {result.rowcount} 个历史向量回填小任务")
    return result.rowcount


# 批量落盘阈值：攒够这么多条向量才向 LanceDB 写一次（批量 add 语义见
# execute_vector_backfill 内注释）
_LANCE_FLUSH_SIZE = 200


async def _build_material_vectors(insp: Inspiration) -> tuple[list[float] | None, list[float] | None]:
    """构造单个素材的 (文本向量, 图像向量)；测试通过 mock 本函数控制成败。

    文本向量：无语义内容（无标签/作者/caption/主色）时为 None；
    图像向量：仅图片素材生成（文件缺失或 CLIP 不可用时为 None）。
    """
    from app.services.vector.embedding import (
        build_inspiration_text,
        generate_image_embedding,
        generate_text_embedding,
    )

    text = build_inspiration_text(insp)
    text_vec = await generate_text_embedding(text) if text else None
    image_vec: list[float] | None = None
    if insp.media_type == "image":
        full_path = settings.storage_root / insp.file_path
        if full_path.exists():
            image_vec = await generate_image_embedding(file_path=str(full_path))
    return text_vec, image_vec


async def _flush_vector_batches(
    pending_text: list[tuple[str, list[float]]],
    pending_image: list[tuple[str, list[float]]],
) -> tuple[int, list[str], int, list[str]]:
    """把攒批的文本/图像向量批量写入 LanceDB，返回 (文本成功数, 文本 ID, 图像成功数, 图像 ID)。

    写入行数与攒批数不符时记 warning（跳过的为维度不匹配/含 NaN 等非法向量）。
    幂等：batch_upsert 先删同批旧向量再插入，重复任务不会产生重复向量。
    """
    text_ids: list[str] = []
    image_ids: list[str] = []
    text_ok = image_ok = 0
    if pending_text:
        written = await vector_store.batch_upsert_vectors("text", pending_text)
        text_ok = written
        text_ids = [iid for iid, _ in pending_text]
        if written != len(pending_text):
            logger.warning(f"文本向量批量写入 {written}/{len(pending_text)} 条")
        pending_text.clear()
    if pending_image:
        written = await vector_store.batch_upsert_vectors("image", pending_image)
        image_ok = written
        image_ids = [iid for iid, _ in pending_image]
        if written != len(pending_image):
            logger.warning(f"图像向量批量写入 {written}/{len(pending_image)} 条")
        pending_image.clear()
    return text_ok, text_ids, image_ok, image_ids


async def execute_vector_backfill(db: AsyncSession, task: TaskQueue) -> None:
    """执行向量回填任务：逐条重建素材的文本/图像向量并维护进度。

    参数:
        db: 任务生命周期会话（用于更新任务进度与状态）
        task: 任务记录

    说明:
        - 素材在执行期间被删除时，rebuild_* 内部按「不存在」静默返回，
          不影响任务完成。
        - 任务幂等：upsert 语义，重复执行不会产生重复向量。
        - payload 支持 mode="text"：只重建文本向量（全量文本重建场景，
          如公式版本升级后），跳过图像向量避免无谓的 CLIP 全库编码；
          成功后把文本公式版本写入标记文件，管理页的「版本过期」提醒解除。
    """
    payload = task.result or {}
    inspiration_ids = payload.get("inspiration_ids") or []
    text_only = payload.get("mode") == "text"
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
    image_skipped = 0  # 非图片素材正常跳过；text_only 模式下不生成图像向量也计入此处
    image_failed = 0  # 图片素材但图像向量生成失败（CLIP 不可用/文件缺失/写入失败等）
    # 声称成功的素材 ID（供收尾落库验证：防「写入时成功、事后被删」的假成功）
    text_ids: list[str] = []
    image_ids: list[str] = []

    # 批量写入：向量攒够 _LANCE_FLUSH_SIZE 条才落盘一次。LanceDB 每次单条
    # upsert 都会生成新的 manifest + 数据文件，全量重建（数千条逐条写）会让
    # 目录膨胀出数千个小文件且文件数持续增长，导致备份永远无法收敛
    # （2026-08-29 备份连续 5 轮增量修复失败的根因）。批量 add 只产生极少数
    # fragment/manifest，与 backfill_all_vectors 的批量写入语义一致。
    pending_text: list[tuple[str, list[float]]] = []  # (素材 ID, 文本向量)
    pending_image: list[tuple[str, list[float]]] = []

    total = len(inspiration_ids)
    for idx, insp_id in enumerate(inspiration_ids, start=1):
        insp = await db.get(Inspiration, insp_id)
        is_image = insp is not None and insp.media_type == "image"

        # 文本向量：标签已随素材 eager load（Inspiration.tags lazy="selectin"）
        if insp is not None:
            text_vec, image_vec = await _build_material_vectors(insp)
            if text_vec:
                pending_text.append((insp_id, text_vec))
            else:
                text_skipped += 1

            if not text_only:
                if image_vec:
                    pending_image.append((insp_id, image_vec))
                elif is_image:
                    image_failed += 1
                else:
                    image_skipped += 1

        # 攒批落盘：任一队列达到阈值即批量写入（批量 add 只产生极少数 manifest）
        if len(pending_text) >= _LANCE_FLUSH_SIZE or len(pending_image) >= _LANCE_FLUSH_SIZE:
            flushed = await _flush_vector_batches(pending_text, pending_image)
            text_done += flushed[0]
            text_ids.extend(flushed[1])
            image_done += flushed[2]
            image_ids.extend(flushed[3])

        # 攒批更新进度（每 25 条提交一次，避免 3000+ 次 commit 拖慢任务）
        if idx % 25 == 0 or idx == total:
            task.done = idx
            task.progress = round(idx / total * 100)
            task.updated_at = utcnow()
            await db.commit()
            await _broadcast_task_event(task, "progress")
            logger.info(
                f"向量回填进度: #{task.id} {task.progress}% ({idx}/{total})"
            )

    # 收尾：清空残余批
    flushed = await _flush_vector_batches(pending_text, pending_image)
    text_done += flushed[0]
    text_ids.extend(flushed[1])
    image_done += flushed[2]
    image_ids.extend(flushed[3])

    # ── 落库验证（防假成功）──
    # 背景：历史上曾出现「任务声称全部写入成功，但向量库目录随后被外部
    # 删除/覆盖，管理页显示大量缺失向量」的假成功（2026-08 复现）。写入
    # 本身成功与「数据最终存在」是两回事，这里抽查读回验证，失败即任务
    # 报错，不再冒充完成——用户能看到失败原因而不是静默缺失。
    if text_ids:
        text_sample = random.sample(text_ids, min(20, len(text_ids)))
        missing_text = [
            iid
            for iid in text_sample
            if await vector_store.get_vector("text", iid) is None
        ]
        if missing_text:
            raise PermanentTaskError(
                f"向量落库验证失败：抽查 {len(text_sample)} 条文本向量中 "
                f"{len(missing_text)} 条未持久化（疑似向量库目录被外部删除/"
                f"覆盖，或写入未真正落盘）。请检查 backend/storage/lancedb 目录。"
            )
    if image_ids:
        image_sample = random.sample(image_ids, min(20, len(image_ids)))
        missing_image = [
            iid
            for iid in image_sample
            if await vector_store.get_vector("image", iid) is None
        ]
        if missing_image:
            raise PermanentTaskError(
                f"向量落库验证失败：抽查 {len(image_sample)} 条图像向量中 "
                f"{len(missing_image)} 条未持久化（疑似向量库目录被外部删除/"
                f"覆盖，或写入未真正落盘）。请检查 backend/storage/lancedb 目录。"
            )

    task.result = {
        "inspiration_ids": inspiration_ids,
        "mode": "text" if text_only else "all",
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
    if not text_only and image_done == 0 and image_failed > 0:
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
    # text_only 全量重建成功：把当前公式版本写入标记文件，管理页「版本过期」提醒解除。
    # 放在最终 commit 前，与完成态同点落盘；写入失败仅记日志不影响任务成功。
    if text_only:
        from app.services.vector.embedding import TEXT_EMBEDDING_FORMULA_VERSION

        vector_store.set_stored_text_formula_version(TEXT_EMBEDDING_FORMULA_VERSION)
    await db.commit()

    # 批量写入完成后压缩向量表：合并碎片文件、清理被取代的旧版本，
    # 防止目录文件数无限膨胀（失败仅记日志，不影响任务成功态）
    try:
        stats = await vector_store.compact_vectors()
        logger.info(f"向量表压缩完成: {stats}")
    except Exception as e:
        logger.warning(f"向量表压缩失败（忽略）: {e}")

    logger.info(
        f"向量回填任务执行完毕: #{task.id} "
        f"文本 {text_done}（跳过 {text_skipped}），"
        f"图像 {image_done}（跳过 {image_skipped}，失败 {image_failed}）"
    )
