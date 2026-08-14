"""任务队列 worker：独立进程，轮询 task_queue 表并串行执行任务。

启动方式（在 backend 目录下）：
    python -m app.worker

与 API 服务共用同一个 SQLite 文件（WAL 模式已开启，读不阻塞写），
worker 与 API 之间通过 task_queue 表解耦。
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import OperationalError

from app.database import async_session, init_db
from app.db_migrations import ensure_schema
from app.models.task import TaskQueue
from app.services.task_runner import (
    PermanentTaskError,
    RecoverableTaskError,
    TASK_HANDLERS,
    _is_recoverable_error,
    _schedule_retry,
)

logger = logging.getLogger(__name__)

# 轮询间隔（秒）：无任务时多久检查一次
_POLL_INTERVAL = 1.0


def _utcnow() -> datetime:
    """返回当前 UTC 时间（naive datetime）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _claim_next_task() -> int | None:
    """原子认领下一个待处理任务，返回任务 ID；无任务可认领时返回 None。

    认领规则：
    - 仅认领 status = pending 的任务
    - 若设置了 next_retry_at（重试退避），需等到该时间之后
    - 通过「先查询 + 条件更新」保证多 worker 实例下不会重复执行同一任务
    - 多 worker 竞争写锁时 SQLite 可能报 database is locked，静默跳过本轮（下一轮再试）
    """
    now = _utcnow()
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
                .values(status="running", updated_at=now)
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
    """启动时重置遗留的 running 任务为 pending。

    worker 进程异常终止时，正在执行的任务会卡在 running 状态；
    重启后将其重置回 pending，由本进程重新认领执行。

    注意：当前为单 worker 部署（见 scripts/restart.sh，仅启动一个 app.worker 进程），
    重启时旧进程必已退出，因此无条件重置是安全的。若未来引入多 worker 并发执行，
    此处的无条件重置会误重置「另一存活 worker 正在执行」的任务，届时需改为
    基于心跳租约的判定（记录执行中的 worker 心跳，仅重置超过心跳超时的 running）。
    """
    now = _utcnow()
    async with async_session() as db:
        result = await db.execute(
            update(TaskQueue)
            .where(TaskQueue.status == "running")
            .values(
                status="pending",
                error="进程异常终止：worker 重启，任务已重置待重新执行",
                updated_at=now,
            )
        )
        await db.commit()
        if result.rowcount:
            logger.warning(f"已重置 {result.rowcount} 个遗留 running 任务为 pending")


async def _worker_loop() -> None:
    """worker 主循环：轮询 pending 任务并串行执行（同一时刻只跑 1 个任务）。"""
    logger.info("任务队列 worker 已启动，开始轮询...")
    while True:
        try:
            task_id = await _claim_next_task()
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
    """worker 入口：确保表结构、重置遗留任务后启动主循环。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 关闭 SQLAlchemy 引擎的 SQL 日志（debug 模式下每秒轮询会刷屏）
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    await init_db()
    await ensure_schema()
    await _reset_stale_tasks()
    await _worker_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("任务队列 worker 已停止")
