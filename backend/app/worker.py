"""任务队列 worker：独立进程，轮询 task_queue 表并串行执行任务。

启动方式（在 backend 目录下）：
    python -m app.worker

与 API 服务共用同一个 SQLite 文件（WAL 模式已开启，读不阻塞写），
worker 与 API 之间通过 task_queue 表解耦。
"""

import asyncio
import logging
import os
import uuid
from datetime import timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import OperationalError

from app.database import async_session, init_db
from app.db_migrations import ensure_schema
from app.models.service_heartbeat import ServiceHeartbeat
from app.models.task import TaskQueue
from app.services.task_runner import (
    PermanentTaskError,
    RecoverableTaskError,
    TASK_HANDLERS,
    _is_recoverable_error,
    _schedule_retry,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# 轮询间隔（秒）：无任务时多久检查一次
_POLL_INTERVAL = 1.0

# 心跳间隔（秒）：worker 定期写入服务心跳并刷新 running 任务的心跳时间
_HEARTBEAT_INTERVAL = 10.0

# 心跳超时阈值（秒）：running 任务心跳超过该时长未更新，视为「认领它的 worker 已死」
_STALE_HEARTBEAT_THRESHOLD = 90.0


def _build_worker_id() -> str:
    """生成当前 worker 实例的唯一标识（pid + 随机后缀）。"""
    return f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def _claim_next_task(worker_id: str) -> int | None:
    """原子认领下一个待处理任务，返回任务 ID；无任务可认领时返回 None。

    认领规则：
    - 仅认领 status = pending 的任务
    - 若设置了 next_retry_at（重试退避），需等到该时间之后
    - 通过「先查询 + 条件更新」保证多 worker 实例下不会重复执行同一任务
    - 认领时记录 worker_id 与心跳时间，供心跳租约判定（替代无条件重置）
    - 多 worker 竞争写锁时 SQLite 可能报 database is locked，静默跳过本轮（下一轮再试）
    """
    now = utcnow()
    async with async_session() as db:
        result = await db.execute(
            select(TaskQueue.id)
            .where(
                TaskQueue.status == "pending",
                or_(
                    TaskQueue.next_retry_at.is_(None),
                    TaskQueue.next_retry_at <= now,
                ),
            )
            .order_by(TaskQueue.id.asc())
            .limit(1)
        )
        row = result.first()
        if not row:
            return None

        task_id = row[0]
        # 原子认领：仅当仍为 pending 时才置为 running，避免重复执行
        try:
            result = await db.execute(
                update(TaskQueue)
                .where(TaskQueue.id == task_id, TaskQueue.status == "pending")
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


async def _run_task(task_id: int) -> None:
    """串行执行单个任务，处理成功 / 自动重试 / 失败标记。

    参数:
        task_id: 任务 ID
    """
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        if not task:
            logger.warning(f"任务不存在: #{task_id}，跳过")
            return

        try:
            handler = TASK_HANDLERS.get(task.type)
            if handler is None:
                raise PermanentTaskError(f"未知任务类型: {task.type}")
            await handler(db, task)
            # 任务执行期间状态可能被外部变更（如取消接口），仅当仍为 running 时才标记 success
            await db.refresh(task)
            if task.status != "running":
                logger.info(
                    f"任务状态在执行期间被外部改为 {task.status}，跳过 success 覆盖: #{task.id}"
                )
                return
            task.status = "success"
            task.progress = 100
            task.error = None
            task.next_retry_at = None
            await db.commit()
            logger.info(f"任务完成: #{task.id} ({task.type})")
        except RecoverableTaskError as e:
            await _schedule_retry(db, task, str(e))
            await db.commit()
        except PermanentTaskError as e:
            task.status = "failed"
            task.error = str(e)
            task.next_retry_at = None
            logger.error(f"任务失败（永久错误，不重试）: #{task.id} ({task.type}): {e}")
            await db.commit()
        except Exception as e:
            msg = str(e) or e.__class__.__name__
            if _is_recoverable_error(msg):
                await _schedule_retry(db, task, msg)
            else:
                task.status = "failed"
                task.error = msg
                task.next_retry_at = None
                logger.error(f"任务失败: #{task.id} ({task.type}): {msg}")
            await db.commit()


async def _reset_stale_tasks() -> None:
    """启动时基于心跳租约重置遗留的 running 任务。

    worker 进程异常终止后，其认领的任务会卡在 running 状态且心跳停止；
    重启时仅重置「心跳超时（认领它的 worker 已死）」的 running 任务，
    存活 worker 正在执行（心跳新鲜）的任务不受影响。

    相比原先「无条件重置全部 running」，心跳租约能安全支持多 worker 部署：
    任一 worker 重启都不会误重置另一存活 worker 正在执行的任务。
    """
    now = utcnow()
    stale_before = now - timedelta(seconds=_STALE_HEARTBEAT_THRESHOLD)
    async with async_session() as db:
        result = await db.execute(
            update(TaskQueue)
            .where(
                TaskQueue.status == "running",
                or_(
                    TaskQueue.heartbeat_at.is_(None),
                    TaskQueue.heartbeat_at < stale_before,
                ),
            )
            .values(
                status="pending",
                error="进程异常终止：worker 心跳超时，任务已重置待重新执行",
                claimed_by=None,
                heartbeat_at=None,
                updated_at=now,
            )
        )
        await db.commit()
        if result.rowcount:
            logger.warning(f"已重置 {result.rowcount} 个心跳超时的遗留 running 任务为 pending")

        # 清理僵尸服务心跳（已死的 worker 实例留下的心跳行），避免表无限膨胀
        await db.execute(
            delete(ServiceHeartbeat).where(
                ServiceHeartbeat.service_type == "worker",
                ServiceHeartbeat.last_heartbeat_at < stale_before,
            )
        )
        await db.commit()


async def _write_heartbeat(worker_id: str) -> None:
    """写入本 worker 的服务心跳，并刷新其认领的 running 任务心跳时间。

    一次心跳同时完成两件事：
    - UPSERT service_heartbeats 行（供健康检查端点判断 worker 存活）
    - 更新 claimed_by = worker_id 的 running 任务 heartbeat_at（供 stale 判定）
    """
    now = utcnow()
    async with async_session() as db:
        hb = await db.get(ServiceHeartbeat, worker_id)
        if hb is None:
            hb = ServiceHeartbeat(
                service_id=worker_id,
                service_type="worker",
                pid=os.getpid(),
                started_at=now,
            )
            db.add(hb)
        hb.last_heartbeat_at = now
        await db.execute(
            update(TaskQueue)
            .where(TaskQueue.status == "running", TaskQueue.claimed_by == worker_id)
            .values(heartbeat_at=now)
        )
        await db.commit()


async def _heartbeat_loop(worker_id: str) -> None:
    """worker 心跳循环：独立 asyncio 任务，按固定间隔写心跳。"""
    while True:
        try:
            await _write_heartbeat(worker_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"worker 心跳写入失败: {e}")
        await asyncio.sleep(_HEARTBEAT_INTERVAL)


async def _worker_loop(worker_id: str) -> None:
    """worker 主循环：轮询 pending 任务并串行执行（同一时刻只跑 1 个任务）。"""
    logger.info(f"任务队列 worker 已启动（{worker_id}），开始轮询...")
    while True:
        try:
            task_id = await _claim_next_task(worker_id)
            if task_id is None:
                await asyncio.sleep(_POLL_INTERVAL)
                continue
            await _run_task(task_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"worker 主循环异常: {e}")
            await asyncio.sleep(_POLL_INTERVAL)


async def main() -> None:
    """worker 入口：确保表结构、重置遗留任务后启动主循环与心跳循环。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 关闭 SQLAlchemy 引擎的 SQL 日志（debug 模式下每秒轮询会刷屏）
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    # 注意：worker 不跑 Alembic 迁移，由服务端进程（app.main）统一负责。
    # 服务端与 worker 并发启动时同时跑 alembic upgrade 会竞争 SQLite 写锁导致死锁；
    # worker 仅做 create_all（建缺失表）+ ensure_schema（手写补列）兜底。
    await init_db()
    await ensure_schema()  # 手写迁移兜底

    worker_id = _build_worker_id()
    await _reset_stale_tasks()
    heartbeat_task = asyncio.create_task(_heartbeat_loop(worker_id))
    try:
        await _worker_loop(worker_id)
    finally:
        heartbeat_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("任务队列 worker 已停止")
