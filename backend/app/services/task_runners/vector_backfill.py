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

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import NamedTuple

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag
from app.models.task import PendingVectorBackfill, TaskQueue
from app.services.task_runners.common import (
    PermanentTaskError,
    _broadcast_task_event,
    _chunked,
    utcnow,
)
from app.services.vector import store as vector_store

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

# 进度提交 / 状态检查的间隔（条）：每这么多条提交一次进度并读一次任务状态。
# 25 是既有口径（避免 3000+ 次 commit 拖慢任务）；暂停与取消挂在同一个检查点上，
# 因此点下按钮后最多再处理 25 条就生效。
_PROGRESS_EVERY = 25

# 图像编码的批量大小（张）：一次交给 CLIP 的图片数。25 落在单卡 GPU 的舒适区间，
# 比逐张 encode 快数倍（batch=1 时 GPU 大量时间在等数据搬运；实测 3.3×）。
# **刻意与 _PROGRESS_EVERY 分开**：进度提交间隔是可调的运营参数（想少 commit 就调大），
# 不该顺带决定 CLIP 一次吃多少张图——两者绑死时把进度间隔调到 200 就是显存尖峰。
_ENCODE_BATCH_SIZE = 25

# 统计字段（累计口径：暂停恢复后要把前几轮的计数带上）
# text_failed：有语义内容但文本嵌入失败——必须与 text_skipped（本来就无文本可嵌入）
# 分开计，否则失败会被当成「这条没文本」静默吞掉（2026-09-27 的故障形态）。
_COUNT_KEYS = (
    "text_done",
    "text_skipped",
    "text_failed",
    "image_done",
    "image_skipped",
    "image_failed",
)

# 执行器认定的「用户要求停」状态：只有这两个状态会让执行器中断收尾。
# 为什么不用「!= running」当判据：pending 的语义是「排队等待被 worker 认领」，
# 不是「停下」。两点原因——
# ① 暂停后立刻点「继续」时接口把任务置回 pending，此刻继续算下去正好符合
#   用户意图（再停一次、再由 worker 重新认领纯属白绕一圈）；
# ② 内部直接调用执行器（测试 / 脚本）时任务本就是 pending，若把 pending 当停止
#    信号，收尾的落库验证 / 防假成功 / 版本标记会被静默跳过（曾据此漏测）。
# 生产链路上 worker 先原子认领（pending → running）再调用本执行器，因此这里只
# 需要识别显式停止信号即可。
_STOP_STATUSES = ("paused", "cancelled")


def _embed_concurrency() -> int:
    """窗口内文本嵌入的并发度（settings.vector_embed_concurrency，最小 1）。

    运行时动态读取（而非导入期快照），便于测试与配置热调。
    默认 1 的原因见 config.py 该字段注释：Ollama 默认串行处理嵌入请求，客户端
    并发实测更慢（8 路 0.6×），必须先把服务端 OLLAMA_NUM_PARALLEL 调大才有意义。
    """
    return max(1, int(settings.vector_embed_concurrency))


def _previous_totals(payload: dict) -> dict[str, int]:
    """取上一轮（暂停/重试前）已累计的统计，缺省为 0。

    为什么需要：「暂停 → 继续」只处理断点之后的素材，本轮计数天然不完整；
    结果里必须写**累计值**，否则用户看到「文本 500」会以为前面的 2500 条白做了。
    """
    return {key: int(payload.get(key) or 0) for key in _COUNT_KEYS}


class _ItemVectors(NamedTuple):
    """单条素材的向量构造结果（窗口批量编码的返回元素）。

    text_failed 用来区分两种「没有文本向量」：本来就无语义内容（合法跳过，记
    text_skipped）与有内容但嵌入失败（记 text_failed，需登记回队列重试）。
    """

    text: list[float] | None
    image: list[float] | None
    text_failed: bool = False


@dataclass
class _RunStats:
    """本轮执行的计数与待落盘缓冲（累计口径由 base 兜住，见 _previous_totals）。"""

    base: dict[str, int]
    text_done: int = 0
    text_skipped: int = 0
    text_failed: int = 0
    image_done: int = 0
    image_skipped: int = 0
    image_failed: int = 0
    # 本轮声称成功写入的素材 ID（供收尾落库验证：防「写入时成功、事后被删」的假成功）
    text_ids: list[str] = field(default_factory=list)
    image_ids: list[str] = field(default_factory=list)
    # 有文本却嵌入失败的素材：收尾时重新登记回待回填队列，避免向量永久缺失
    failed_text_ids: list[str] = field(default_factory=list)
    # 攒批缓冲：向量攒够 _LANCE_FLUSH_SIZE 条才落盘一次
    pending_text: list[tuple[str, list[float]]] = field(default_factory=list)
    pending_image: list[tuple[str, list[float]]] = field(default_factory=list)

    async def flush(self, force: bool = False) -> None:
        """攒批落盘：达到阈值（或 force）时才写 LanceDB，并累加成功计数。"""
        if not force and (
            len(self.pending_text) < _LANCE_FLUSH_SIZE
            and len(self.pending_image) < _LANCE_FLUSH_SIZE
        ):
            return
        flushed = await _flush_vector_batches(self.pending_text, self.pending_image)
        self.text_done += flushed[0]
        self.text_ids.extend(flushed[1])
        self.image_done += flushed[2]
        self.image_ids.extend(flushed[3])

    def result(self, payload: dict, remaining: int) -> dict:
        """组装任务 result：计数一律写**累计值**（base + 本轮）。

        暂停恢复后本轮只处理了断点之后的素材，写本轮数字会让用户以为前几轮白做了。
        """
        return {
            **payload,
            "mode": "text" if payload.get("mode") == "text" else "all",
            "text_done": self.base["text_done"] + self.text_done,
            "text_skipped": self.base["text_skipped"] + self.text_skipped,
            "text_failed": self.base["text_failed"] + self.text_failed,
            "image_done": self.base["image_done"] + self.image_done,
            "image_skipped": self.base["image_skipped"] + self.image_skipped,
            "image_failed": self.base["image_failed"] + self.image_failed,
            "remaining": remaining,
        }


async def _finalize_interrupted(
    db: AsyncSession, task: TaskQueue, payload: dict, stats: _RunStats, total: int
) -> None:
    """暂停/取消收尾：把已算好但未落盘的向量写掉，记录累计统计，然后停下。

    为什么要落盘：攒批上限 200 条，检查点停下的那一刻可能还压着上百条**已经算完**
    的向量（文本 embedding 或 CLIP 图像编码都不便宜）。不写就全作废、恢复时重算
    ——那等于「暂停要付双倍代价」，用户会认为暂停没生效。

    失败登记：本轮嵌入失败的文本素材重新登记回待回填队列——暂停不该让它们丢失。

    为什么不标 100% / 不改状态：暂停与取消的**状态由接口侧写**，这里只负责
    「保存进度并停下」；worker 收尾时见 ``status != running`` 不会覆盖成 success
    （见 app/worker.py 成功分支的状态复查），所以 paused/cancelled 会被保留。
    """
    await stats.flush(force=True)
    if stats.failed_text_ids:
        await _recover_failed_ids(db, stats.failed_text_ids)
    task.result = stats.result(payload, remaining=max(0, total - int(task.done or 0)))
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"向量回填任务被外部状态中断: #{task.id} status={task.status} "
        f"进度 {task.done}/{task.total}，已算向量已落盘，恢复时从断点续算"
    )


async def _recover_failed_ids(db: AsyncSession, inspiration_ids: list[str]) -> None:
    """失败任务收尾：把任务涉及的素材重新登记回待回填队列。

    背景：flush 创建任务时会清空待回填表；若任务随后**永久失败**（如 CLIP
    不可用 / Ollama 不可用 / 落库验证失败），这些素材的登记已被清除，既不
    自动重试也不存在于任何队列，只有手动「一键向量化」才能找回——用户视角
    就是「上传新素材后向量几乎全部缺失」。

    这里在抛任务级异常前把素材重新登记（幂等，不触发攒批 flush——否则
    「失败 → 重建任务 → 再失败」会在 worker 内无限循环），能力恢复后由
    下一次 flush（手动触发 / worker 启动 / 攒批达阈值）自动重试，素材永不丢失。
    """
    existing_ids = await _filter_existing_ids(db, inspiration_ids)
    if not existing_ids:
        return
    stmt = sqlite_insert(PendingVectorBackfill).values(
        [{"inspiration_id": iid} for iid in existing_ids]
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["inspiration_id"])
    await db.execute(stmt)
    await db.commit()
    logger.warning(
        "向量回填任务失败，已将 %d 个素材重新登记回待回填队列（下次 flush 自动重试）",
        len(existing_ids),
    )


async def _load_window_inspirations(
    db: AsyncSession, inspiration_ids: list[str]
) -> dict[str, Inspiration]:
    """一次 IN 查询加载一个窗口的素材（含两级标签关系）。

    - 显式 selectinload 两级标签关系（Inspiration.tags → InspirationTag.tag）：
      build_inspiration_text 会同步访问 t.tag.name，而 InspirationTag.tag 是默认
      lazy="select"——异步会话下隐式懒加载会抛 MissingGreenlet
      （"greenlet_spawn has not been called"）。此前逐条 db.get 只 eager load 了
      一级 tags，二级 .tag 未加载即触发此错误。
    - 过滤已删除素材：垃圾桶素材不重建向量（与旧 rebuild_* 路径语义一致）。
    - 一个窗口一条查询（≤_PROGRESS_EVERY 个 ID）：逐条 execute 会把每条素材的
      往返都压在关键路径上；25 个 ID 远低于「长 IN 子句只返回 1 行」的规模阈值
      （分批 500 的原因见 _filter_existing_ids 的说明）。
    """
    result = await db.execute(
        select(Inspiration)
        .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
        .where(
            Inspiration.id.in_(inspiration_ids),
            Inspiration.deleted_at.is_(None),
        )
    )
    return {insp.id: insp for insp in result.scalars().all()}


async def _build_material_vectors(
    inss: list[Inspiration], with_images: bool = True
) -> list[_ItemVectors]:
    """构造**一个窗口**素材的向量：文本逐条嵌入、图像整窗一次批量 CLIP。

    接缝说明：测试 mock 本函数控制成败（入参是窗口里的素材列表，返回等长的
    _ItemVectors 列表），因此执行器本身不直接依赖 Ollama / CLIP。

    为什么要批量：CLIP 逐张 encode（batch=1）时 GPU 大量时间在等数据搬运，
    整窗一次前向能快数倍（见 embedding.generate_image_embeddings）。

    参数:
        inss: 本窗口的素材（已排除不存在/已删除的）
        with_images: mode="text"（全量重建文本）时为 False——跳过图像编码，
            否则「只重建文本」会白跑一遍全库 CLIP

    返回:
        与 inss 等长的结果列表
    """
    from app.services.vector.embedding import (
        build_inspiration_text,
        generate_image_embeddings,
        generate_text_embedding,
    )

    texts = [build_inspiration_text(insp) for insp in inss]
    # 文本嵌入：默认顺序执行（_embed_concurrency() 默认 1）。并发度可用
    # VECTOR_EMBED_CONCURRENCY 调大，但**先要把 Ollama 侧 OLLAMA_NUM_PARALLEL 调大**
    # ——实测服务端默认串行时，客户端 8 路并发比顺序慢 1.7 倍（详见 config.py）。
    # gather 保序：text_vecs[i] 与 texts[i] 一一对应。
    sem = asyncio.Semaphore(_embed_concurrency())

    async def _embed(text: str) -> list[float] | None:
        if not text:
            return None
        async with sem:
            return await generate_text_embedding(text)

    text_vecs = list(await asyncio.gather(*(_embed(text) for text in texts)))

    image_vecs: list[list[float] | None] = [None] * len(inss)
    if with_images:
        image_paths = [
            str(settings.storage_root / insp.file_path)
            for insp in inss
            if insp.media_type == "image"
        ]
        if image_paths:
            # 按 _ENCODE_BATCH_SIZE 切块（与进度提交间隔解耦，见该常量注释）
            by_path: dict[str, list[float] | None] = {}
            for start in range(0, len(image_paths), _ENCODE_BATCH_SIZE):
                chunk = image_paths[start : start + _ENCODE_BATCH_SIZE]
                encoded = await generate_image_embeddings(chunk)
                # 等长契约由 generate_image_embeddings 保证；万一返回偏短，缺失的路径
                # 取不到向量 → 该素材计入 image_failed（而不是整窗抛错中断任务）
                by_path.update(dict(zip(chunk, encoded, strict=False)))
            for i, insp in enumerate(inss):
                if insp.media_type == "image":
                    image_vecs[i] = by_path.get(str(settings.storage_root / insp.file_path))

    return [
        _ItemVectors(
            text=text_vecs[i],
            image=image_vecs[i],
            # 有语义内容却拿不到向量 = 嵌入失败（generate_text_embedding 内部已重试过）
            text_failed=bool(texts[i]) and text_vecs[i] is None,
        )
        for i in range(len(inss))
    ]


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
    """执行向量回填任务：按窗口批量重建素材的文本/图像向量并维护进度。

    参数:
        db: 任务生命周期会话（用于更新任务进度与状态）
        task: 任务记录

    说明:
        - 素材在执行期间被删除时按「跳过」计，不影响任务完成。
        - 任务幂等：upsert 语义，重复执行不会产生重复向量。
        - payload 支持 mode="text"：只重建文本向量（全量文本重建场景，
          如公式版本升级后），跳过图像向量避免无谓的 CLIP 全库编码；
          成功后把文本公式版本写入标记文件，管理页的「版本过期」提醒解除。
        - **窗口批量**：每 _PROGRESS_EVERY 条为一个窗口，一次 IN 查询加载素材、
          文本逐条嵌入、图像整窗一次 CLIP 前向（见 _build_material_vectors）。
          逐张 encode 时 GPU 大量时间在等数据搬运，批量前向是回填提速的关键。
        - **可暂停 / 可取消**：每个窗口边界（每 _PROGRESS_EVERY 条）提交进度后读
          一次任务状态，一旦被置为 paused / cancelled（_STOP_STATUSES）就把已算
          向量落盘并返回，由 worker 按 DB 最新状态决定下一步；paused 后「继续」由
          接口把任务放回 pending 重新认领，本函数从 ``task.done`` 断点续算（不重跑
          已处理素材——图像向量要逐张跑 CLIP 编码，全量重来等于暂停白等）。
        - **文本嵌入失败不再静默**：失败计入 text_failed（与「本来就无文本」的
          text_skipped 分开），收尾时重新登记回待回填队列；本轮「全失败零成功」则
          抛永久错误，不让任务冒充成功（与图像链路同口径）。
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

    # 计数与待落盘缓冲：base 是累计口径的底（恢复执行时本轮只处理断点之后的素材，
    # 结果里要带上前几轮的计数），pending_* 是攒批缓冲。
    # 为什么攒批写入：LanceDB 每次单条 upsert 都会生成新的 manifest + 数据文件，
    # 全量重建（数千条逐条写）会让目录膨胀出数千个小文件且文件数持续增长，导致备份
    # 永远无法收敛（2026-08-29 备份连续 5 轮增量修复失败的根因）。批量 add 只产生
    # 极少数 fragment/manifest，与 backfill_all_vectors 的批量写入语义一致。
    stats = _RunStats(base=_previous_totals(payload))

    total = len(inspiration_ids)
    # 断点续算：从上次提交的 done 继续（暂停恢复 / 失败重试都走这里）
    start_idx = max(0, min(int(task.done or 0), total))

    # 进入执行前先看一次状态：只拦「明确要求停」的 paused/cancelled。
    # 这里**故意不拦 pending**：正常流程里 pending 表示「等待或重新排队执行」，
    # 内部直接调用执行器（测试 / 脚本）时任务也常是 pending；循环内用同一个
    # _STOP_STATUSES 判据，语义前后一致。
    await db.refresh(task)
    if task.status in _STOP_STATUSES:
        logger.info(
            f"向量回填任务开始前已被外部置为 {task.status}: #{task.id}，本次不执行"
        )
        return

    window_start = start_idx
    while window_start < total:
        window_ids = inspiration_ids[window_start : window_start + _PROGRESS_EVERY]
        # 一次 IN 查询加载整窗素材（逐条 execute 会把每条素材的往返都压在关键路径上）
        by_id = await _load_window_inspirations(db, window_ids)
        present = [by_id[i] for i in window_ids if i in by_id]
        built = (
            await _build_material_vectors(present, with_images=not text_only)
            if present
            else []
        )
        vec_by_id = {insp.id: built[i] for i, insp in enumerate(present)}

        for insp_id in window_ids:
            insp = by_id.get(insp_id)
            vectors = vec_by_id.get(insp_id)
            if vectors is None:
                # 素材不存在或已删除（垃圾桶）：计入跳过（与旧 rebuild_* 路径口径一致）
                stats.text_skipped += 1
                stats.image_skipped += 1
                continue

            if vectors.text:
                stats.pending_text.append((insp_id, vectors.text))
            elif vectors.text_failed:
                # 有语义内容却嵌入失败：计入失败并登记，收尾时重新入队重试
                # （旧实现把它并进 text_skipped，于是向量缺了一片而任务显示成功）
                stats.text_failed += 1
                stats.failed_text_ids.append(insp_id)
            else:
                stats.text_skipped += 1

            if not text_only:
                if vectors.image:
                    stats.pending_image.append((insp_id, vectors.image))
                elif insp is not None and insp.media_type == "image":
                    stats.image_failed += 1
                else:
                    stats.image_skipped += 1

        # 攒批落盘：任一队列达到阈值即批量写入（批量 add 只产生极少数 manifest）
        await stats.flush()

        # 进度在窗口边界提交（每 _PROGRESS_EVERY 条一次，避免数千次 commit 拖慢任务）
        done_now = window_start + len(window_ids)
        task.done = done_now
        task.progress = round(done_now / total * 100)
        task.updated_at = utcnow()
        await db.commit()
        await _broadcast_task_event(task, "progress")
        logger.info(f"向量回填进度: #{task.id} {task.progress}% ({done_now}/{total})")

        # 状态检查：外部可能把任务改成 paused（暂停）/ cancelled（取消）。
        # 必须紧跟在 commit 之后 refresh：本地未提交的 done/progress 会被丢弃。
        await db.refresh(task)
        if task.status in _STOP_STATUSES:
            await _finalize_interrupted(db, task, payload, stats, total)
            return
        window_start = done_now

    # 收尾：清空残余批
    await stats.flush(force=True)

    # ── 落库验证（防假成功）──
    # 背景：历史上曾出现「任务声称全部写入成功，但向量库目录随后被外部
    # 删除/覆盖，管理页显示大量缺失向量」的假成功（2026-08 复现）。写入
    # 本身成功与「数据最终存在」是两回事，这里抽查读回验证，失败即任务
    # 报错，不再冒充完成——用户能看到失败原因而不是静默缺失。
    if stats.text_ids:
        text_sample = random.sample(stats.text_ids, min(20, len(stats.text_ids)))
        missing_text = [
            iid
            for iid in text_sample
            if await vector_store.get_vector("text", iid) is None
        ]
        if missing_text:
            # 失败前重新登记：防「任务失败 + 队列已清空」导致素材永久丢失
            await _recover_failed_ids(db, inspiration_ids)
            raise PermanentTaskError(
                f"向量落库验证失败：抽查 {len(text_sample)} 条文本向量中 "
                f"{len(missing_text)} 条未持久化（疑似向量库目录被外部删除/"
                f"覆盖，或写入未真正落盘）。请检查 backend/storage/lancedb 目录。"
            )
    if stats.image_ids:
        image_sample = random.sample(stats.image_ids, min(20, len(stats.image_ids)))
        missing_image = [
            iid
            for iid in image_sample
            if await vector_store.get_vector("image", iid) is None
        ]
        if missing_image:
            await _recover_failed_ids(db, inspiration_ids)
            raise PermanentTaskError(
                f"向量落库验证失败：抽查 {len(image_sample)} 条图像向量中 "
                f"{len(missing_image)} 条未持久化（疑似向量库目录被外部删除/"
                f"覆盖，或写入未真正落盘）。请检查 backend/storage/lancedb 目录。"
            )

    task.result = stats.result(payload, remaining=0)
    # 统计结果先落库：即使下面判定失败抛出任务级异常，失败详情也能在任务记录中查到
    await db.commit()

    # 防假成功：存在图片素材但图像向量全部生成失败（系统性故障，如 CLIP 不可用 /
    # LanceDB 未安装 / 图片文件缺失），任务不能冒充「完成」，交由 worker 标记失败。
    # 判定在写「完成态」之前：异常抛出时任务仍为 running，避免「先 commit 完成态
    # 再抛异常」在进程崩溃时残留假完成。
    # 判据用**本轮**计数（不是累计）：恢复后的剩余批全失败同样是系统性故障，
    # 不能因为「前面几轮成功过」就放过。
    if not text_only and stats.image_done == 0 and stats.image_failed > 0:
        detail = (
            f"向量回填失败：{stats.image_failed} 个图片素材的图像向量全部生成失败"
            f"（本轮成功 {stats.image_done}）。常见原因：CLIP 模型不可用、LanceDB 未安装、"
            f"图片文件缺失或写入失败"
        )
        await _recover_failed_ids(db, inspiration_ids)
        raise PermanentTaskError(detail)

    # 文本侧同口径：有内容要嵌入却一条都没成功（典型是 Ollama 被 VLM / 人脸任务
    # 抢占、请求整体超时），同样不能冒充完成——否则用户看到「成功」而文本向量缺一片。
    if stats.text_failed > 0 and stats.text_done == 0:
        detail = (
            f"向量回填失败：{stats.text_failed} 个素材的文本向量全部生成失败"
            f"（本轮成功 0）。常见原因：Ollama 未启动、嵌入模型缺失，"
            f"或被其它 AI 任务抢占导致请求超时"
        )
        await _recover_failed_ids(db, stats.failed_text_ids)
        raise PermanentTaskError(detail)

    # 非系统性的零散文本失败：重新登记回待回填队列。旧实现是「一次超时就永久缺一条
    # 文本向量」，这里改为登记，能力恢复后由下一次 flush（手动 / 攒批达到阈值 /
    # worker 启动兜底）自动重试，素材不丢。
    if stats.failed_text_ids:
        logger.warning(
            "向量回填有 %d 个素材文本嵌入失败，已重新登记待回填队列（本轮成功 %d）",
            len(stats.failed_text_ids),
            stats.text_done,
        )
        await _recover_failed_ids(db, stats.failed_text_ids)

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
        compact_stats = await vector_store.compact_vectors()
        logger.info(f"向量表压缩完成: {compact_stats}")
    except Exception as e:
        logger.warning(f"向量表压缩失败（忽略）: {e}")

    logger.info(
        f"向量回填任务执行完毕: #{task.id} "
        f"文本 {task.result['text_done']}（跳过 {task.result['text_skipped']}，"
        f"失败 {task.result['text_failed']}），"
        f"图像 {task.result['image_done']}"
        f"（跳过 {task.result['image_skipped']}，失败 {task.result['image_failed']}）"
    )
