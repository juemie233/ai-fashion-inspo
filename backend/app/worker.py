"""任务队列 worker：独立进程，轮询 task_queue 表并执行任务。

启动方式（在 backend 目录下）：
    python -m app.worker

与 API 服务共用同一个 SQLite 文件（WAL 模式已开启，读不阻塞写），
worker 与 API 之间通过 task_queue 表解耦。
任务认领按 priority DESC, id ASC（高优先级先执行，同优先级 FIFO）；
同时执行的任务数由 settings.worker_concurrency 控制（默认 1 串行）。
重负载任务（GPU_EXCLUSIVE_TASK_TYPES：向量回填 / 质量审核 / 批量分析 /
人脸扫描与匹配）全局串行。注意：默认 ``worker_concurrency=1`` 时重负载任务会占满
唯一槽位，轻量任务也要排队；只有并发 >= 2 或多 worker 进程时，“轻量任务不受影响”
才成立。见 GPU_EXCLUSIVE_TASK_TYPES 的说明。

边界：``asyncio.to_thread`` 里的阻塞调用无法被 ``task.cancel()`` 中断；取消
任务是协作式的，执行器必须在批次/检查点读取 status 才会停。线程已进入 GPU
推理时，即使任务被取消/标记 failed，该线程仍可能继续跑完当前调用。这是
Python 线程模型限制，不是任务状态机可以完全消除的竞态。
"""

import asyncio
import logging
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import and_, case, delete, exists, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, init_db
from app.db_migrations import ensure_schema
from app.logging_config import (
    TEXT_FORMAT,
    ContextFormatter,
    run_id_var,
    setup_logging,
    task_id_var,
)
from app.models.service_heartbeat import ServiceHeartbeat
from app.models.task import TaskQueue
from app.models.task_run_log import TaskRunLog
from app.services.task_runner import (
    GPU_EXCLUSIVE_TASK_TYPES,
    PermanentTaskError,
    RecoverableTaskError,
    TASK_HANDLERS,
    _broadcast_task_event,
    _is_recoverable_error,
    _schedule_retry,
)
from app.utils.daily_log import DailyFileHandler
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# 轮询间隔（秒）：无任务时多久检查一次
_POLL_INTERVAL = 1.0

# 心跳间隔（秒）：worker 定期写入服务心跳并刷新 running 任务的心跳时间。
# 可由 WORKER_HEARTBEAT_INTERVAL_SECONDS 覆盖（默认 10s；系统测试会调小以加快验证）。
_HEARTBEAT_INTERVAL = float(getattr(settings, "worker_heartbeat_interval_seconds", 10.0))

# 心跳超时阈值（秒）：running 任务心跳超过该时长未更新，视为「认领它的 worker 已死」。
# 可由 WORKER_STALE_HEARTBEAT_THRESHOLD_SECONDS 覆盖（默认 90s）。
_STALE_HEARTBEAT_THRESHOLD = float(
    getattr(settings, "worker_stale_heartbeat_threshold_seconds", 90.0)
)

# 任务看门狗检查间隔（秒）：低频扫「本 worker 认领的 running 任务是否长时间没进度」
_STALL_CHECK_INTERVAL = 30.0

# 看门狗已取消的任务 id（_run_task_safe 据此区分「看门狗取消」与「进程关闭取消」，
# 只对前者写 failed 终态；关闭时保留 running 交给下次启动的 _reset_stale_tasks）
_WATCHDOG_CANCELLED: set[int] = set()

# task_id -> 本次执行台账行 id（看门狗收尾时补 finished_at 用；_run_task_safe 兜底清理）
_RUN_LOG_IDS: dict[int, int] = {}

# 当前 worker 派生的执行协程：关闭时先取消它们，再把仍 running 的任务放回 pending。
_ACTIVE_TASKS: dict[asyncio.Task, int] = {}

# 僵尸任务清扫间隔（秒）：worker 运行期周期性执行 _reset_stale_tasks。
# 仅靠启动时的一次清扫有漏洞：关闭服务后 90s 内快速重启（restart.sh 常见
# 耗时），遗留 running 任务的心跳仍「新鲜」，启动清扫会跳过；此后无人再
# 检查，新 worker 不认领 running 任务 → 任务永久卡死。运行期持续清扫，
# 心跳一过期即被重置回 pending 重新执行。
_STALE_SWEEP_INTERVAL = 30.0

# 重负载任务互斥组由 app.services.task_runner.TASK_REGISTRY 的
# gpu_exclusive 标记派生，worker 不再维护第二份名单；新增任务类型只在
# task_runner 登记一次，避免「handler 登记了但忘了加互斥」。
# 组内任务全局串行，代价是一个卡住则全组等；进程崩溃由心跳租约兜底。
def _build_worker_id() -> str:
    """生成当前 worker 实例的唯一标识（pid + 随机后缀）。"""
    return f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def _claim_next_task(worker_id: str) -> int | None:
    """原子认领下一个待处理任务，返回任务 ID；无任务可认领时返回 None。

    认领规则：
    - 仅认领 status = pending 的任务
    - 若设置了 next_retry_at（重试退避），需等到该时间之后
    - 按 priority DESC, id ASC 排序：高优先级任务先执行，同优先级保持 FIFO
    - **重负载互斥**（GPU_EXCLUSIVE_TASK_TYPES）：组内已有任务在 running 时，
      不认领组内任务（改去认领不吃 GPU 的任务，或本轮什么都不认领），避免两个
      重负载任务同时抢 GPU / 整批打满 CPU
    - 通过「先查询 + 条件更新」保证多 worker 实例下不会重复执行同一任务
    - 认领时记录 worker_id 与心跳时间，供心跳租约判定（替代无条件重置）
    - 多 worker 竞争写锁时 SQLite 可能报 database is locked，静默跳过本轮（下一轮再试）
    """
    now = utcnow()
    async with async_session() as db:
        # 互斥组是否有任务正在跑（同一条件既用于选任务，也用于原子认领）
        exclusive_running = (
            select(TaskQueue.id)
            .where(
                TaskQueue.status == "running",
                TaskQueue.type.in_(GPU_EXCLUSIVE_TASK_TYPES),
            )
            .exists()
        )
        claimable = or_(
            TaskQueue.type.not_in(GPU_EXCLUSIVE_TASK_TYPES),
            ~exclusive_running,
        )
        result = await db.execute(
            select(TaskQueue.id, claimable.label("claimable"))
            .where(
                TaskQueue.status == "pending",
                or_(
                    TaskQueue.next_retry_at.is_(None),
                    TaskQueue.next_retry_at <= now,
                ),
            )
            # 可认领的排前面：互斥组忙时把不吃 GPU 的任务挑出来执行，不让它们
            # 陪着一起等；同为可认领/不可认领时仍按 priority DESC, id ASC
            .order_by(
                case((claimable, 1), else_=0).desc(),
                TaskQueue.priority.desc(),
                TaskQueue.id.asc(),
            )
            .limit(1)
        )
        row = result.first()
        # 兼容只返回 id 的旧测试替身；真实查询返回 (id, claimable)
        claimable_ok = bool(row[1]) if row and len(row) > 1 else bool(row)
        if not row or not claimable_ok:
            # 队首任务被互斥组挡住时直接返回，避免带条件的 UPDATE
            # 白白获取 SQLite 写锁；下一轮再看互斥组是否释放。
            return None

        task_id = row[0]
        # 原子认领：仅当仍为 pending **且互斥条件仍成立**时才置为 running。
        # 后一个条件防的是「查询到这里之间，另一个进程抢走了互斥组名额」。
        try:
            result = await db.execute(
                update(TaskQueue)
                .where(
                    TaskQueue.id == task_id,
                    TaskQueue.status == "pending",
                    claimable,
                )
                .values(
                    status="running",
                    claimed_by=worker_id,
                    heartbeat_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
        except OperationalError as e:
            # SQLite 写锁：多 worker/API 同时写入导致认领失败，属正常竞争。
            # 静默跳过本轮认领（不记 error 噪音），下一轮轮询再试。
            if "database is locked" in str(e).lower():
                return None
            raise
        if result.rowcount == 0:
            return None
        return task_id


# 任务运行台账日志目录（与 supervisor 的 logs/ 同级）。
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


@lru_cache(maxsize=1)
def _gpu_model() -> str | None:
    """探测显卡型号（只探一次）；多卡时用 "; " 拼接型号，探测失败时返回 None。"""
    override = os.environ.get("GPU_MODEL", "").strip()
    if override:
        return override[:128]
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0:
            names = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if names:
                return "; ".join(dict.fromkeys(names))[:128]
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("_gpu_model: 忽略异常（预期降级）：%s", exc)
        return None
    return None


async def _commit_with_retry(
    db: AsyncSession,
    *,
    attempts: int = 3,
    base_delay: float = 0.1,
) -> None:
    """提交终态/台账，遇到 SQLite database is locked 短暂退避重试。

    终态写入是任务状态机的最后一步：一次瞬时锁失败不能让任务停在不一致状态。
    只重试 locked；其它 DB 错误原样抛出，由调用方决定回滚/降级。
    """
    last_exc: OperationalError | None = None
    for attempt in range(attempts):
        try:
            await db.commit()
            return
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            last_exc = exc
            await asyncio.sleep(base_delay * (2 ** attempt))
    if last_exc is not None:  # pragma: no cover - 循环逻辑保证到达时已抛出
        raise last_exc


async def _start_run_log(db: AsyncSession | None, task: TaskQueue) -> int | None:
    """任务开始：写一条运行台账（started_at + GPU + attempt）。

    使用独立数据库会话，避免台账失败回滚主任务会话导致 task 过期/成功被覆盖。
    ``db`` 参数仅为兼容既有调用与测试保留，不参与写库。
    """
    try:
        async with async_session() as log_db:
            row = TaskRunLog(
                task_id=task.id,
                task_type=task.type,
                attempt=int(task.retry_count or 0),
                started_at=utcnow(),
                item_count=0,
                total_count=int(task.total or 0),
                status="running",
                gpu_model=await asyncio.to_thread(_gpu_model),
            )
            log_db.add(row)
            await _commit_with_retry(log_db)
            await log_db.refresh(row)
            # 本次执行的账本行 id：日志格式里的 run= 字段据此与 task_run_logs 对齐
            run_id_var.set(str(row.id))
            _RUN_LOG_IDS[task.id] = row.id
            logger.info(
                "任务开始: #%s (%s) total=%s attempt=%s gpu=%s",
                task.id,
                task.type,
                task.total,
                task.retry_count,
                row.gpu_model or "unknown",
            )
            return row.id
    except Exception as exc:  # noqa: BLE001 —— 台账失败不能阻断任务执行
        logger.warning("写任务运行台账失败（忽略）: %s", exc)
        return None


async def _finish_run_log(
    db: AsyncSession | None,
    run_log_id: int | None,
    *,
    status: str,
    item_count: int,
    error: str | None = None,
) -> bool:
    """任务结束：补 finished_at / 状态 / 实际张数；返回是否成功落库。

    使用独立数据库会话：台账写失败不能影响主任务的成功终态（也不回滚主会话）。
    ``db`` 参数仅为兼容既有调用与测试保留。
    """
    if run_log_id is None:
        return False
    try:
        async with async_session() as log_db:
            row = await log_db.get(TaskRunLog, run_log_id)
            if row is None:
                return True
            row.finished_at = utcnow()
            row.status = status
            row.item_count = int(item_count or 0)
            row.error = (error or "")[:2000] or None
            await _commit_with_retry(log_db)
            seconds = (
                (row.finished_at - row.started_at).total_seconds()
                if row.started_at and row.finished_at
                else 0
            )
            logger.info(
                "任务结束: #%s (%s) status=%s done=%s/%s elapsed=%.1fs gpu=%s",
                row.task_id,
                row.task_type,
                status,
                row.item_count,
                row.total_count,
                seconds,
                row.gpu_model or "unknown",
            )
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("写任务运行台账收尾失败（忽略）: %s", exc)
        return False


async def _reload_for_failure(
    db: AsyncSession, task_id: int
) -> TaskQueue | None:
    """失败处理前先回滚 handler 的未提交写入，再重新取任务行。

    为什么必须先 rollback（2026-10-20 审查发现的通用缺陷）：各执行器常在中途
    ``db.add(...)`` + ``flush()``、稍后逐轮 commit（例如 quality_check 先写
    AIAnalysisLog / AIQualityReview 再提交）。若在 flush 之后抛错，直接
    ``task.status = "failed"`` + commit，会把这些**半成品**一起落库——任务显示失败，
    但日志/复核记录却写了「处理过」，下游统计与复审都会读到假数据。

    rollback 之后原 task 实例已过期（属性访问需要异步刷新），所以必须 ``db.get`` 重新取；
    任务若已被删除（如 404 清理），返回 None 让调用方安全跳过终态写入。
    """
    try:
        await db.rollback()
    except Exception as exc:  # noqa: BLE001 —— 回滚失败也不能掩盖原始异常
        logger.warning(f"任务 #{task_id} 失败后 rollback 出错（继续标记终态）: {exc}")
    fresh = await db.get(TaskQueue, task_id)
    if fresh is None:
        logger.warning(f"任务 #{task_id} 在失败处理时已不存在（可能被删除），跳过终态写入")
    return fresh


def _is_foreign_task(task: TaskQueue | None, worker_id: str | None) -> bool:
    """任务是否已被外部取消/暂停/接管，或已经进入其它终态。"""
    if task is None:
        return True
    if task.status != "running":
        return True
    return bool(worker_id and task.claimed_by != worker_id)


async def _finish_foreign_run_log(
    db: AsyncSession | None,
    run_log_id: int | None,
    task: TaskQueue | None,
    *,
    error: str,
) -> None:
    """旧协程发现自己已失去任务归属时，只收尾自己的台账，不动任务行。"""
    if task is None:
        return
    try:
        await _finish_run_log(
            db,
            run_log_id,
            status=task.status if task.status in ("cancelled", "paused") else "failed",
            item_count=task.done,
            error=error,
        )
    except Exception:  # noqa: BLE001 —— 台账失败不能把旧协程/主循环带崩
        logger.exception("旧协程台账收尾失败（忽略）")


async def _run_task(task_id: int, worker_id: str | None = None) -> None:
    """执行单个任务，处理成功 / 自动重试 / 失败标记。

    在独立 asyncio 协程中运行（并发数由 worker_concurrency 控制，默认 1），
    多任务并发执行时各自持有独立数据库会话，互不影响。

    参数:
        task_id: 任务 ID
        worker_id: 认领它的 worker 实例；缺省时按任务行 claimed_by 兼容旧调用
    """
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        if not task:
            logger.warning(f"任务不存在: #{task_id}，跳过")
            return
        if worker_id is None:
            worker_id = task.claimed_by
        if worker_id and task.claimed_by != worker_id:
            logger.warning(
                "任务 #%s 已被 %s 接管，跳过旧 worker %s 的执行",
                task_id,
                task.claimed_by,
                worker_id,
            )
            return

        # 广播「开始执行」事件（安全入口，无 WS 连接时静默降级）
        await _broadcast_task_event(task, "running")
        # 运行台账：开始时间 / GPU 型号（结束时间在终态补）
        run_log_id = await _start_run_log(db, task)

        try:
            handler = TASK_HANDLERS.get(task.type)
            if handler is None:
                raise PermanentTaskError(f"未知任务类型: {task.type}")
            await handler(db, task)
            # 任务执行期间状态可能被外部变更（如取消/暂停接口），仅当仍为 running 时才标记 success
            await db.refresh(task)
            if task.status != "running" or (worker_id and task.claimed_by != worker_id):
                logger.info(
                    "任务状态/归属在执行期间被外部变更（status=%s, claimed_by=%s），"
                    "跳过 success 覆盖: #%s",
                    task.status,
                    task.claimed_by,
                    task.id,
                )
                # 外部已置为 cancelled：补一次终态广播（paused 不在事件契约内，不广播）
                if task.status == "cancelled":
                    await _broadcast_task_event(task, "cancelled", error=task.error)
                await _finish_foreign_run_log(
                    db, run_log_id, task, error="任务执行期间被外部取消/暂停/接管"
                )
                return
            # 成功终态用带条件的 SQL 更新：refresh 与 commit 之间仍可能被
            # 取消/暂停/新 worker 接管；OR 更新不能把外部状态覆盖成 success。
            await db.flush()
            success_stmt = update(TaskQueue).where(
                TaskQueue.id == task.id,
                TaskQueue.status == "running",
            )
            if worker_id:
                success_stmt = success_stmt.where(
                    TaskQueue.claimed_by == worker_id
                )
            success_result = await db.execute(
                success_stmt.values(
                    status="success",
                    progress=100,
                    error=None,
                    next_retry_at=None,
                    updated_at=utcnow(),
                )
            )
            if success_result.rowcount == 0:
                await db.rollback()
                current = await db.get(
                    TaskQueue, task.id, populate_existing=True
                )
                if current and current.status == "cancelled":
                    await _broadcast_task_event(
                        current, "cancelled", error=current.error
                    )
                await _finish_foreign_run_log(
                    db,
                    run_log_id,
                    current,
                    error="任务完成时已被外部取消/暂停/接管",
                )
                return
            await _commit_with_retry(db)
            task.status = "success"
            task.progress = 100
            task.error = None
            task.next_retry_at = None
            await _finish_run_log(
                db, run_log_id, status="success", item_count=task.done
            )
            await _broadcast_task_event(task, "success")
            logger.info(f"任务完成: #{task.id} ({task.type})")
        except RecoverableTaskError as e:
            # 退避重试回 pending：非终态，不广播（前端经轮询/进度事件感知）
            task = await _reload_for_failure(db, task_id)
            if task is None:
                return
            if _is_foreign_task(task, worker_id):
                logger.info(
                    "任务 #%s 失败处理时已被外部取消/暂停/接管，保留外部状态",
                    task_id,
                )
                await _finish_foreign_run_log(
                    db, run_log_id, task, error="执行失败但任务已被外部接管"
                )
                return
            await _schedule_retry(db, task, str(e))
            await _commit_with_retry(db)
            await _finish_run_log(
                db, run_log_id, status="retry", item_count=task.done, error=str(e)
            )
        except PermanentTaskError as e:
            task = await _reload_for_failure(db, task_id)
            if task is None:
                return
            if _is_foreign_task(task, worker_id):
                logger.info(
                    "任务 #%s 失败处理时已被外部取消/暂停/接管，保留外部状态",
                    task_id,
                )
                await _finish_foreign_run_log(
                    db, run_log_id, task, error="执行失败但任务已被外部接管"
                )
                return
            task.status = "failed"
            task.error = str(e)
            task.next_retry_at = None
            logger.exception(f"任务失败（永久错误，不重试）: #{task.id} ({task.type}): {e}")
            await _commit_with_retry(db)
            await _finish_run_log(
                db, run_log_id, status="failed", item_count=task.done, error=str(e)
            )
            await _broadcast_task_event(task, "failed", error=str(e))
        except Exception as e:
            task = await _reload_for_failure(db, task_id)
            if task is None:
                return
            if _is_foreign_task(task, worker_id):
                logger.info(
                    "任务 #%s 失败处理时已被外部取消/暂停/接管，保留外部状态",
                    task_id,
                )
                await _finish_foreign_run_log(
                    db, run_log_id, task, error="执行失败但任务已被外部接管"
                )
                return
            msg = str(e) or e.__class__.__name__
            if _is_recoverable_error(msg):
                await _schedule_retry(db, task, msg)
            else:
                task.status = "failed"
                task.error = msg
                task.next_retry_at = None
                logger.exception(f"任务失败: #{task.id} ({task.type}): {msg}")
            await _commit_with_retry(db)
            await _finish_run_log(
                db,
                run_log_id,
                status="failed" if task.status == "failed" else "retry",
                item_count=task.done,
                error=msg,
            )
            # 仅真正进入 failed 终态时广播；重试回 pending 不广播
            if task.status == "failed":
                await _broadcast_task_event(task, "failed", error=msg)


async def _run_task_safe(task_id: int, worker_id: str | None = None) -> None:
    """并发槽位中执行单个任务：捕获所有异常，防止执行协程崩溃拖垮主循环。"""
    # 任务上下文：task_id / run_id 写进 contextvar，任务 runner 内所有日志自动带上
    task_token = task_id_var.set(f"#{task_id}")
    run_token = run_id_var.set("-")
    try:
        if worker_id is None:
            await _run_task(task_id)
        else:
            await _run_task(task_id, worker_id)
    except asyncio.CancelledError:
        # 看门狗取消：写 failed 终态 + 补台账结束时间，再放行异常让槽位释放。
        # 进程关闭（asyncio.run 收尾）取消时不写终态：任务保持 running，下次启动
        # 由 _reset_stale_tasks 按心跳超时重置，语义与历史一致。
        if task_id in _WATCHDOG_CANCELLED:
            try:
                finalized = await _fail_stalled_task(task_id)
            except Exception:  # noqa: BLE001 —— 收尾失败不能把 worker 主循环带崩
                # P1-4：收尾失败时**保留**标记，由主循环 watchdog 周期重试；
                # 先 discard 会让任务永远留在 running（没有下一次处理机会）。
                logger.exception("看门狗收尾失败（保留标记待重试）: #%s", task_id)
            else:
                if finalized is not False:
                    _WATCHDOG_CANCELLED.discard(task_id)
        raise
    except Exception:
        # _run_task 的失败收尾本身也可能因为 database is locked 抛出来；
        # 此时协程即将结束，但任务行仍是 running，必须交给已有的看门狗重试通道
        # 收尾为 failed，否则心跳会一直续租、任务永久卡死并占住互斥组。
        logger.exception(f"任务执行协程异常退出: #{task_id}")
        _WATCHDOG_CANCELLED.add(task_id)
        try:
            finalized = await _fail_stalled_task(task_id, reason="任务执行协程异常退出")
        except Exception:  # noqa: BLE001 —— 保留标记，主循环继续重试
            logger.exception("执行协程异常退出后的收尾失败（保留标记待重试）: #%s", task_id)
        else:
            if finalized is not False:
                _WATCHDOG_CANCELLED.discard(task_id)
    finally:
        # 看门狗收尾重试仍需要 run_log_id；只有已成功收尾/未进入看门狗时才清理。
        if task_id not in _WATCHDOG_CANCELLED:
            _RUN_LOG_IDS.pop(task_id, None)
        run_id_var.reset(run_token)
        task_id_var.reset(task_token)


async def _stalled_task_ids(worker_id: str, stall_seconds: int) -> list[int]:
    """找出本 worker 认领、但超过 ``stall_seconds`` 没有进度更新的 running 任务。

    进度证据是 ``task_queue.updated_at``：执行器写进度/结果/状态时 ORM 会刷新它；
    **心跳只写 ``heartbeat_at``，并显式保留 ``updated_at``（见 ``_heartbeat_thread_worker``）**，
    所以这个列代表「执行器自己有没有动」，不会被执行器存活的证据（心跳）掩盖。
    这是 2026-11-03 评审 P0-1 的修复：此前心跳 UPDATE 触发 ``onupdate=utcnow``，
    把 ``updated_at`` 每 10 秒刷新一次，看门狗永远看不到卡死。
    """
    cutoff = utcnow() - timedelta(seconds=stall_seconds)
    async with async_session() as db:
        rows = (
            await db.execute(
                select(TaskQueue.id).where(
                    TaskQueue.status == "running",
                    TaskQueue.claimed_by == worker_id,
                    or_(
                        TaskQueue.updated_at.is_(None),
                        TaskQueue.updated_at < cutoff,
                    ),
                )
            )
        ).scalars().all()
    return list(rows)


async def _fail_stalled_task(task_id: int, reason: str | None = None) -> bool:
    """看门狗取消/异常退出后的收尾：标记 failed、补台账、广播失败事件。

    为什么不在取消前写库：取消前写会让卡死的执行协程与新写操作共用 SQLite 写锁，
    极端情况下反而卡住收尾；先 cancel 释放它持有的会话/锁，再由本函数用新会话收尾。
    """
    if reason:
        message = f"{reason}，已由 worker 强制收尾（释放并发槽位；可重新提交，已产生的数据保留）"
    else:
        minutes = max(1, int(settings.task_stall_timeout_seconds) // 60)
        message = (
            f"任务超过 {minutes} 分钟无进度更新，已由看门狗强制终止（释放并发槽位；"
            f"可重新提交，已产生的数据保留）"
        )
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        if task is None:
            return True
        if task.status == "running":
            task.status = "failed"
            task.error = message
            task.next_retry_at = None
            task.updated_at = utcnow()
            await _commit_with_retry(db)
            finished = await _finish_run_log(
                db,
                _RUN_LOG_IDS.get(task_id),
                status="failed",
                item_count=task.done,
                error=message,
            )
            if finished:
                _RUN_LOG_IDS.pop(task_id, None)
            await _broadcast_task_event(task, "failed", error=message)
            if not finished:
                return False
        logger.error(
            "任务看门狗终止: #%s (%s) 已 %s 秒无进度更新，状态=%s",
            task.id,
            task.type,
            settings.task_stall_timeout_seconds,
            task.status,
        )
        return True


async def _reset_stale_tasks(exclude_worker: str | None = None) -> None:
    """基于心跳租约重置遗留的 running 任务。

    worker 进程异常终止后，其认领的任务会卡在 running 状态且心跳停止；
    重启时仅重置「心跳超时（认领它的 worker 已死）」的 running 任务，
    存活 worker 正在执行（心跳新鲜）的任务不受影响。

    相比原先「无条件重置全部 running」，心跳租约能安全支持多 worker 部署：
    任一 worker 重启都不会误重置另一存活 worker 正在执行的任务。

    **重试次数上限（2026-10-20 审查发现）**：重置同时把 `retry_count + 1`。此前只改状态
    不加计数，一个「必崩」的任务会在每次 worker 重启时被重新执行，永远跑不完、也永
    不进入 failed；现在用尽 `max_retries` 的直接判失败，与 `_schedule_retry` 的口径一致。
    """
    now = utcnow()
    stale_before = now - timedelta(seconds=_STALE_HEARTBEAT_THRESHOLD)
    stale_cond = and_(
        TaskQueue.status == "running",
        or_(
            TaskQueue.heartbeat_at.is_(None),
            TaskQueue.heartbeat_at < stale_before,
        ),
    )
    # 本 worker 仍在跑的协程由进程内看门狗负责；周期清扫不能因为一次心跳写锁拥塞
    # 就把自己的任务重置/重排（否则长任务会白跑一遍，GPU 互斥也被破坏）。
    if exclude_worker:
        stale_cond = and_(
            stale_cond,
            or_(
                TaskQueue.claimed_by.is_(None),
                TaskQueue.claimed_by != exclude_worker,
            ),
        )
    async with async_session() as db:
        # 1) 重试次数已用尽：崩溃也算一次失败，直接判失败（否则无限重启循环）
        exhausted = await db.execute(
            update(TaskQueue)
            .where(stale_cond, TaskQueue.retry_count >= TaskQueue.max_retries)
            .values(
                status="failed",
                error="进程异常终止：worker 心跳超时且重试次数已用尽，任务标记失败",
                claimed_by=None,
                heartbeat_at=None,
                updated_at=now,
            )
        )
        # 2) 其余：回 pending 并把这次崩溃计为一次重试
        result = await db.execute(
            update(TaskQueue)
            .where(stale_cond)
            .values(
                status="pending",
                error="进程异常终止：worker 心跳超时，任务已重置待重新执行",
                retry_count=TaskQueue.retry_count + 1,
                claimed_by=None,
                heartbeat_at=None,
                updated_at=now,
            )
        )
        await db.commit()
        if exhausted.rowcount:
            logger.error(
                f"已把 {exhausted.rowcount} 个心跳超时且重试耗尽的遗留任务标记为 failed"
            )
        if result.rowcount:
            logger.warning(
                f"已重置 {result.rowcount} 个心跳超时的遗留 running 任务为 pending（重试次数 +1）"
            )

        # 清理僵尸服务心跳（已死的 worker 实例留下的心跳行），避免表无限膨胀
        await db.execute(
            delete(ServiceHeartbeat).where(
                ServiceHeartbeat.service_type == "worker",
                ServiceHeartbeat.last_heartbeat_at < stale_before,
            )
        )
        await db.commit()


def _write_heartbeat_sync(worker_id: str, conn: sqlite3.Connection) -> None:
    """同步写一次服务心跳与 running 任务心跳。

    由独立线程调用；不回滚/提交错误由线程循环处理。``updated_at`` 显式保留原值，
    避免心跳掩盖执行器进度（看门狗依赖它判断卡死）。
    """
    now = utcnow()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S.%f")
    conn.execute(
        """
        INSERT INTO service_heartbeats
            (service_id, service_type, pid, started_at, last_heartbeat_at)
        VALUES (?, 'worker', ?, ?, ?)
        ON CONFLICT(service_id) DO UPDATE SET
            last_heartbeat_at = excluded.last_heartbeat_at,
            pid = excluded.pid
        """,
        (worker_id, os.getpid(), stamp, stamp),
    )
    conn.execute(
        "UPDATE task_queue SET heartbeat_at = ?, updated_at = updated_at "
        "WHERE status = 'running' AND claimed_by = ?",
        (stamp, worker_id),
    )
    conn.commit()


def _heartbeat_thread_worker(worker_id: str, stop_event: threading.Event) -> None:
    """独立线程的心跳循环：不依赖 worker 主事件循环是否被 GPU 任务阻塞。"""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(settings.db_file_path), timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        while not stop_event.is_set():
            try:
                _write_heartbeat_sync(worker_id, conn)
            except Exception as exc:  # noqa: BLE001 —— 单次失败不能杀死心跳线程
                logger.warning("worker 心跳写入失败: %s", exc)
                try:
                    conn.rollback()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("心跳写失败后 rollback 也失败（忽略）：%s", exc)
            if stop_event.wait(_HEARTBEAT_INTERVAL):
                break
    except Exception:  # noqa: BLE001 —— 线程退出只记录，不影响主进程
        logger.exception("worker 心跳线程异常退出")
    finally:
        if conn is not None:
            conn.close()


async def _heartbeat_loop(worker_id: str) -> None:
    """心跳生命周期协程：启动独立线程，取消时停止并回收线程。"""
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_heartbeat_thread_worker,
        args=(worker_id, stop_event),
        name=f"worker-heartbeat-{worker_id}",
        daemon=True,
    )
    thread.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        stop_event.set()
        await asyncio.to_thread(thread.join, 5.0)


async def _shutdown_release_tasks(worker_id: str) -> None:
    """进程正常退出：把本 worker 仍 running 的任务放回 pending，且不加重试次数。

    仅在执行协程已被取消后调用；数据库提交失败只记日志（下次启动的
    _reset_stale_tasks 仍会按心跳超时兜底）。同时删除本 worker 的心跳行。
    """
    try:
        async with async_session() as db:
            result = await db.execute(
                update(TaskQueue)
                .where(
                    TaskQueue.status == "running",
                    TaskQueue.claimed_by == worker_id,
                )
                .values(
                    status="pending",
                    error="worker 正常退出，任务已释放待重新认领",
                    claimed_by=None,
                    heartbeat_at=None,
                    updated_at=utcnow(),
                )
            )
            await db.execute(
                delete(ServiceHeartbeat).where(
                    ServiceHeartbeat.service_id == worker_id
                )
            )
            await _commit_with_retry(db)
            if result.rowcount:
                logger.warning(
                    "worker 正常退出：已释放 %s 个 running 任务为 pending（不计重试次数）",
                    result.rowcount,
                )
    except Exception:  # noqa: BLE001 —— 退出路径不能把进程卡死
        logger.exception("worker 退出时释放任务失败（交给下次启动的租约清扫）")
    await _finalize_orphan_run_logs()


async def _worker_loop(worker_id: str) -> None:
    """worker 主循环：按 worker_concurrency 并发认领并执行任务。

    每个任务在独立 asyncio 协程中执行（互不阻塞），同时执行的任务数上限由
    settings.worker_concurrency 控制（.env 可配，默认 1 保持串行行为）。
    并发化后既有语义保持不变：
    - 心跳租约：心跳循环按 claimed_by 统一刷新本实例认领的全部 running 任务；
    - 暂停/恢复/取消：各执行器按任务状态自查，_run_task 终态前二次复查兜底；
    - 崩溃恢复：进程退出后遗留 running 任务由 _reset_stale_tasks 按心跳超时重置；
    - 多实例：原子认领（条件 UPDATE）保证同一任务不会被重复执行。
    注意：并发调大会增加 Ollama 显存压力与 SQLite 写锁竞争，建议 1~2。
    """
    logger.info(f"任务队列 worker 已启动（{worker_id}），开始轮询...")
    # task -> task_id：看门狗要按 id 匹配「是否卡死」，不能只有 Task 集合
    running: dict[asyncio.Task, int] = {}
    last_stale_sweep = 0.0  # 启动后首轮即清扫（main 启动清扫后的兜底补位）
    last_stall_check = 0.0
    while True:
        try:
            # 周期性清扫心跳超时的遗留 running 任务（修复：仅启动时清扫一次，
            # 90s 内快速重启会让遗留任务永久卡在 running——见 _STALE_SWEEP_INTERVAL）
            if time.monotonic() - last_stale_sweep >= _STALE_SWEEP_INTERVAL:
                last_stale_sweep = time.monotonic()
                await _reset_stale_tasks(exclude_worker=worker_id)
                # 重置后的任务台账也要收尾，否则运行记录会留下永久“进行中”
                await _finalize_orphan_run_logs()
            # 任务看门狗：本 worker 认领的任务长时间没进度 → cancel + 写 failed。
            # 低频（30s）执行，查询只涉及本 worker 的少数 running 行。
            stall_seconds = int(settings.task_stall_timeout_seconds or 0)
            if (
                stall_seconds > 0
                and time.monotonic() - last_stall_check >= _STALL_CHECK_INTERVAL
            ):
                last_stall_check = time.monotonic()
                stalled = set(await _stalled_task_ids(worker_id, stall_seconds))
                if stalled:
                    for task, task_id in list(running.items()):
                        if task_id in stalled and not task.done():
                            logger.error(
                                "[看门狗] 任务 #%s 超过 %s 秒无进度更新，强制取消并标记失败",
                                task_id,
                                stall_seconds,
                            )
                            _WATCHDOG_CANCELLED.add(task_id)
                            task.cancel()
            # 回收已完成的执行协程（异常已在 _run_task_safe 内记录，不会丢失槽位）
            running = {
                task: task_id for task, task_id in running.items() if not task.done()
            }
            for task in list(_ACTIVE_TASKS):
                if task.done():
                    _ACTIVE_TASKS.pop(task, None)
            # P1-4：看门狗收尾失败的任务标记不会丢；协程已结束后在这里重试写终态，
            # 避免 DB 瞬时锁导致任务永久停在 running。
            await _retry_pending_watchdog_finalize(set(running.values()))
            concurrency = max(1, int(settings.worker_concurrency))
            while len(running) < concurrency:
                task_id = await _claim_next_task(worker_id)
                if task_id is None:
                    break
                child = asyncio.create_task(_run_task_safe(task_id, worker_id))
                running[child] = task_id
                _ACTIVE_TASKS[child] = task_id
            await asyncio.sleep(_POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"worker 主循环异常: {e}")
            await asyncio.sleep(_POLL_INTERVAL)


async def _flush_pending_backfills_on_startup() -> None:
    """启动时兜底：清理历史 1/1 小任务，并把攒批队列中的遗留待回填素材入队。

    攒批机制的兜底保障：进程重启后，未达阈值（100）的待回填素材不会丢失——
    启动时一次性 flush 成批量任务执行（任务创建失败/清理异常仅记日志，不阻断启动）。
    """
    from app.services.task_runners.vector_backfill import (
        flush_pending_vector_backfills,
        purge_small_backfill_tasks,
    )

    async with async_session() as db:
        try:
            await purge_small_backfill_tasks(db)
            task = await flush_pending_vector_backfills(db, force=True)
            if task is not None:
                logger.info(
                    f"启动时已把遗留待回填素材入队: #{task.id}，共 {task.total} 个"
                )
        except Exception as e:
            logger.warning(f"启动时清理/回填遗留任务失败（忽略，不影响 worker 启动）: {e}")


def _cleanup_old_analyze_logs() -> None:
    """删除超过保留期的 ``logs/analyze-YYYY-MM-DD.log``（默认 30 天）。

    按天日志只增不减，长期跑下去会把 logs/ 撑大；worker 启动时顺手清一次即可
    （worker 重启频率远高于保留期，不需要额外定时器）。
    """
    keep_days = max(0, settings.log_retention_days)
    if keep_days <= 0 or not _LOG_DIR.is_dir():
        return
    cutoff = datetime.now().timestamp() - keep_days * 86400
    removed = 0
    for path in _LOG_DIR.glob("analyze-*.log"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as exc:  # 文件被占用时跳过，不能因为清理失败拖垮启动
            logger.warning("清理过期按天日志失败（忽略）：%s（%s）", path.name, exc)
    if removed:
        logger.info("[日志清理] 删除 %s 个超过 %s 天的按天分析日志", removed, keep_days)


async def _cleanup_old_run_logs() -> None:
    """删除超过保留期的任务运行台账行（默认 90 天）。

    只按 ``started_at`` 删历史行，不动 running 中的行；清理失败只告警不阻断启动。
    """
    keep_days = max(0, settings.task_run_log_retention_days)
    if keep_days <= 0:
        return
    cutoff = utcnow() - timedelta(days=keep_days)
    try:
        async with async_session() as db:
            result = await db.execute(
                delete(TaskRunLog).where(
                    TaskRunLog.started_at < cutoff,
                    TaskRunLog.status != "running",
                )
            )
            await db.commit()
            if result.rowcount:
                logger.info(
                    "[台账清理] 删除 %s 条超过 %s 天的任务运行台账",
                    result.rowcount,
                    keep_days,
                )
    except Exception as exc:  # noqa: BLE001 —— 台账清理失败不能阻断 worker 启动
        logger.warning("清理过期任务运行台账失败（忽略）：%s", exc)


async def _retry_pending_watchdog_finalize(running_task_ids: set[int]) -> None:
    """重试「看门狗收尾失败」的任务（P1-4）。

    取消协程已经结束、但 ``_fail_stalled_task`` 因 DB 锁/异常失败时，任务仍是
    running；标记保留在 ``_WATCHDOG_CANCELLED``，由主循环在每次看门狗周期重试，
    直到写库成功才清标记——避免「先丢标记、再收尾失败」导致永久 running 僵尸。
    """
    for task_id in list(_WATCHDOG_CANCELLED):
        if task_id in running_task_ids:
            continue
        try:
            finalized = await _fail_stalled_task(task_id)
        except Exception:  # noqa: BLE001 —— 下一轮继续重试
            logger.exception("看门狗收尾重试失败（保留标记）: #%s", task_id)
            continue
        if finalized is not False:
            _WATCHDOG_CANCELLED.discard(task_id)


async def _finalize_orphan_run_logs() -> None:
    """启动时收尾「任务已不存在或已非 running」的 running 台账。

    2026-11-02 现场留下一条 task_id=745 的 running 台账（任务行已被删除），
    永远等不到 finished_at；不清理的话「运行记录」里会一直显示"进行中"。

    2026-11-03 评审 P1-3：不能只按「开始超过 2 小时」收尾——多 worker 部署时
    另一实例正在跑的长任务会被误标 failed，台账与任务状态矛盾。现在用相关子查询
    只收尾**任务行不存在、或任务本身已不是 running**的台账；任务仍 running 的
    （无论多久）一律不动，由实际执行方负责收尾。
    """
    task_still_running = exists(
        select(TaskQueue.id).where(
            TaskQueue.id == TaskRunLog.task_id,
            TaskQueue.status == "running",
        )
    )
    try:
        async with async_session() as db:
            result = await db.execute(
                update(TaskRunLog)
                .where(TaskRunLog.status == "running", ~task_still_running)
                .values(
                    status="failed",
                    finished_at=utcnow(),
                    error="进程异常终止：台账未收尾（worker 启动时补齐）",
                )
            )
            await db.commit()
            if result.rowcount:
                logger.warning(
                    "[台账收尾] 把 %s 条任务已结束/不存在的 running 台账标记为 failed",
                    result.rowcount,
                )
    except Exception as exc:  # noqa: BLE001 —— 台账收尾失败不能阻断 worker 启动
        logger.warning("收尾孤儿任务运行台账失败（忽略）：%s", exc)


def _setup_logging() -> None:
    """统一日志（root handler + 级别/格式）+ 按天 ``analyze-YYYY-MM-DD.log``。

    历史问题（P1-5）：``basicConfig`` 只配 root 根级别，且运行中的 worker 早于
    台账功能提交，导致 ``task_run_logs`` 为空、``analyze-*.log`` 不生成，排查时
    只能猜「代码是否生效」。现在启动时把 root handlers 与按天日志目录打进日志，
    下次重启即可从 ``worker.log`` 直接确认配置已加载。
    """
    setup_logging("worker")
    root = logging.getLogger()
    if not any(isinstance(handler, DailyFileHandler) for handler in root.handlers):
        handler = DailyFileHandler(_LOG_DIR, prefix="analyze")
        handler.setFormatter(ContextFormatter(TEXT_FORMAT))
        root.addHandler(handler)
    logger.info(
        "[worker] 日志就绪：root handlers=%s，按天日志目录=%s（LOG_LEVEL=%s）",
        [type(handler).__name__ for handler in root.handlers],
        _LOG_DIR,
        logging.getLevelName(root.level),
    )
    # P1-5：启动时打印生效的看门狗参数，排障时不用反查 .env
    logger.info(
        "[worker] 看门狗阈值=%ss，心跳间隔=%.0fs，并发槽位=%s（TASK_STALL_TIMEOUT_SECONDS / "
        "WORKER_STALE_HEARTBEAT_THRESHOLD_SECONDS 见文档）",
        settings.task_stall_timeout_seconds,
        _HEARTBEAT_INTERVAL,
        settings.worker_concurrency,
    )


async def _shutdown_worker(worker_id: str) -> None:
    """正常关闭：取消本 worker 的执行协程，再释放 running 任务（不加重试次数）。"""
    active = [task for task in _ACTIVE_TASKS if not task.done()]
    for task in active:
        task.cancel()
    if active:
        await asyncio.gather(*active, return_exceptions=True)
    _ACTIVE_TASKS.clear()
    await _shutdown_release_tasks(worker_id)


async def main() -> None:
    """worker 入口：确保表结构、重置遗留任务后启动主循环与心跳循环。"""
    _setup_logging()
    # setup_logging 已把 sqlalchemy.engine 压到 WARNING（LOG_LEVEL=DEBUG 时保留）
    # 注意：worker 不跑 Alembic 迁移，由服务端进程（app.main）统一负责。
    # 服务端与 worker 并发启动时同时跑 alembic upgrade 会竞争 SQLite 写锁导致死锁；
    # worker 仅做 create_all（建缺失表）+ ensure_schema（手写补列）兜底。
    await init_db()
    await ensure_schema()  # 手写迁移兜底

    # 日志/台账保留期清理（P1-4）：启动时各做一次，失败只告警不阻断
    _cleanup_old_analyze_logs()
    await _cleanup_old_run_logs()

    worker_id = _build_worker_id()
    # 先重置任务，再收尾台账：否则崩溃遗留的 running 任务会让其旧台账被
    # _finalize_orphan_run_logs 误判为“仍在运行”，要等下一次重启才补 failed。
    await _reset_stale_tasks()
    await _finalize_orphan_run_logs()
    await _flush_pending_backfills_on_startup()
    heartbeat_task = asyncio.create_task(_heartbeat_loop(worker_id))
    try:
        await _worker_loop(worker_id)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            logger.debug("worker 心跳任务已取消")
        await _shutdown_worker(worker_id)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("任务队列 worker 已停止")
