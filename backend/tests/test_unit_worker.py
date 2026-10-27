"""worker 主循环与任务生命周期（app/worker.py）的测试。

worker 决定「任务能不能被正确执行、卡住能不能被回收、失败该重试还是判死」。
它没有 HTTP 入口，坏了就是**全局**故障，所以下面每条都对应一次真实踩坑：

1. **认领的原子性与互斥**：多 worker 下同一任务不能被执行两次；重负载任务
   （GPU_EXCLUSIVE_TASK_TYPES）在组内已有 running 时不能被认领，但要允许先去干
   不吃 GPU 的活（否则整队列陪着等）。
2. **失败的两条路**：可恢复错误（Ollama 宕机/超时）→ 退避重试回 pending 且
   `retry_count+1`；永久错误（图片损坏/未知类型）→ 直接 failed。分错方向要么
   无限重试、要么一次抖动就判死。
3. **崩溃恢复**：心跳超时的遗留 running 任务要回 pending 并把这次崩溃计一次重试；
   重试已用尽的直接判 failed，否则「必崩的任务」会在每次重启时被重跑，永远跑不完。
4. **外部状态不被覆盖**：执行期间被改成 cancelled/paused 的任务，`_run_task`
   不能再改写成 success。
5. **失败处理前必须 rollback**：handle 中途 flush 的半成品（日志/复核记录）不能
   跟着「任务失败」一起落库，否则下游统计读到假数据。

任务执行器（TASK_HANDLERS）用替身，这里只验 worker 自己的状态机。
"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.service_heartbeat import ServiceHeartbeat
from app.models.task import TaskQueue
from app.services.task_runners.common import PermanentTaskError, RecoverableTaskError
from app.worker import (
    _claim_next_task,
    _reload_for_failure,
    _reset_stale_tasks,
    _run_task,
    _run_task_safe,
    _worker_loop,
    _write_heartbeat,
)
from app.utils.time import utcnow


async def _task(
    *,
    type_: str = "batch_analyze",
    status: str = "pending",
    priority: int = 0,
    retry_count: int = 0,
    max_retries: int = 2,
    claimed_by: str | None = None,
    heartbeat_at=None,
    next_retry_at=None,
) -> int:
    async with async_session() as db:
        t = TaskQueue(
            type=type_,
            status=status,
            priority=priority,
            progress=0,
            total=1,
            done=0,
            retry_count=retry_count,
            max_retries=max_retries,
            claimed_by=claimed_by,
            heartbeat_at=heartbeat_at,
            next_retry_at=next_retry_at,
            result={},
        )
        db.add(t)
        await db.commit()
        return t.id


async def _get(task_id: int) -> TaskQueue:
    async with async_session() as db:
        return await db.get(TaskQueue, task_id)


@pytest.fixture(autouse=True)
def _no_broadcast(monkeypatch):
    """广播是安全入口，但测试里没必要真的碰 WS。"""
    import app.worker as w

    async def _noop(task, event, **kwargs):
        return None

    monkeypatch.setattr(w, "_broadcast_task_event", _noop)


# ── 认领 ──


async def test_claim_takes_pending_task_and_marks_running():
    tid = await _task()

    claimed = await _claim_next_task("w1")

    assert claimed == tid
    row = await _get(tid)
    assert row.status == "running"
    assert row.claimed_by == "w1"
    assert row.heartbeat_at is not None


async def test_claim_returns_none_when_empty():
    assert await _claim_next_task("w1") is None


async def test_claim_honours_priority_then_fifo():
    """priority 大的先跑，同优先级按 id 升序（FIFO）。

    这里用「不吃 GPU」的任务类型——batch_analyze 在互斥组里，连续认领会互相挡住
    （互斥行为由 test_claim_blocks_second_gpu_task_while_one_runs 单独验证）。
    """
    low = await _task(type_="deduplicate", priority=0)
    high = await _task(type_="deduplicate", priority=5)

    assert await _claim_next_task("w1") == high
    assert await _claim_next_task("w1") == low


async def test_claim_skips_task_waiting_for_retry_backoff():
    """退避中的任务（next_retry_at 未到）不能被提前认领，否则退避形同虚设。"""
    tid = await _task(next_retry_at=utcnow() + timedelta(seconds=60))

    assert await _claim_next_task("w1") is None
    row = await _get(tid)
    assert row.status == "pending"


async def test_claim_allows_task_whose_backoff_expired():
    tid = await _task(next_retry_at=utcnow() - timedelta(seconds=1))
    assert await _claim_next_task("w1") == tid


async def test_claim_blocks_second_gpu_task_while_one_runs():
    """GPU 互斥：组内已有 running 时不再认领同类任务（避免两个重负载抢显存）。"""
    await _task(type_="image_gen", status="running", claimed_by="other")

    blocked = await _task(type_="batch_analyze")
    light = await _task(type_="deduplicate")  # 不吃 GPU

    # 只应认领不吃 GPU 的那个
    assert await _claim_next_task("w1") == light
    assert (await _get(blocked)).status == "pending"


async def test_claim_takes_gpu_task_when_group_idle():
    tid = await _task(type_="image_gen")
    assert await _claim_next_task("w1") == tid


async def test_claim_is_atomic_against_concurrent_update():
    """查询到认领之间的竞争：状态已被别人改走时（rowcount=0）返回 None，不误报成功。"""
    tid = await _task()
    async with async_session() as db:
        row = await db.get(TaskQueue, tid)
        row.status = "running"  # 模拟另一个 worker 抢先
        row.claimed_by = "other"
        await db.commit()

    assert await _claim_next_task("w1") is None


# ── 执行：成功 / 重试 / 失败 ──


@pytest.fixture
def handlers(monkeypatch):
    """替换 TASK_HANDLERS：每个用例按需设定 handler 行为。"""
    import app.worker as w

    calls: list[str] = []
    store: dict = {}

    def _install(fn):
        store["fn"] = fn
        monkeypatch.setitem(w.TASK_HANDLERS, "batch_analyze", fn)

        async def _handler(db, task):
            calls.append(task.type)
            return await fn(db, task)

        monkeypatch.setitem(w.TASK_HANDLERS, "batch_analyze", _handler)
        return calls

    return _install


async def test_run_task_success_sets_progress_100(handlers):
    async def _ok(db, task):
        return None

    handlers(_ok)
    tid = await _task(status="running", claimed_by="w1")

    await _run_task(tid)

    row = await _get(tid)
    assert row.status == "success"
    assert row.progress == 100
    assert row.error is None


async def test_run_task_recoverable_error_schedules_retry(handlers):
    """可恢复错误 → 回 pending + retry_count+1 + 设置退避时间（不是失败终态）。"""
    async def _recoverable(db, task):
        raise RecoverableTaskError("Ollama 宕机")

    handlers(_recoverable)
    tid = await _task(status="running", claimed_by="w1", retry_count=0)

    await _run_task(tid)

    row = await _get(tid)
    assert row.status == "pending"
    assert row.retry_count == 1
    assert row.next_retry_at is not None
    assert "Ollama" in (row.error or "")


async def test_run_task_permanent_error_marks_failed(handlers):
    async def _permanent(db, task):
        raise PermanentTaskError("图片损坏")

    handlers(_permanent)
    tid = await _task(status="running", claimed_by="w1")

    await _run_task(tid)

    row = await _get(tid)
    assert row.status == "failed"
    assert row.next_retry_at is None
    assert "图片损坏" in (row.error or "")


async def test_run_task_unknown_type_is_permanent_failure():
    """未知任务类型属永久错误：重试也不会变好。"""
    tid = await _task(status="running", claimed_by="w1", type_="不存在的类型")

    await _run_task(tid)

    row = await _get(tid)
    assert row.status == "failed"
    assert "未知任务类型" in (row.error or "")


async def test_run_task_generic_error_classified_by_message(handlers):
    """非预期异常按消息判定：含「超时」这类可恢复特征 → 重试；否则判失败。"""
    async def _timeout(db, task):
        raise RuntimeError("AI 模型响应超时 (30 秒)")

    handlers(_timeout)
    tid = await _task(status="running", claimed_by="w1")

    await _run_task(tid)

    assert (await _get(tid)).status == "pending"


async def test_run_task_does_not_override_external_cancel(handlers):
    """执行期间被取消：不能改写成 success（否则用户取消完还是绿的）。"""
    async def _cancel_during(db, task):
        row = await db.get(TaskQueue, task.id)
        row.status = "cancelled"
        await db.commit()

    handlers(_cancel_during)
    tid = await _task(status="running", claimed_by="w1")

    await _run_task(tid)

    row = await _get(tid)
    assert row.status == "cancelled"
    assert row.progress != 100


async def test_run_task_missing_task_is_noop():
    await _run_task(999999)  # 不应抛错


async def test_run_task_rolls_back_partial_writes_on_failure(handlers):
    """失败前 handler 已 flush 的半成品必须回滚——否则「任务失败但日志显示处理过」。"""
    from app.models.inspiration import AIAnalysisLog

    async def _write_then_fail(db, task):
        db.add(
            AIAnalysisLog(
                inspiration_id="半成品", model_name="m", log_type="analysis", error=None
            )
        )
        await db.flush()  # 已写入事务但未提交
        raise PermanentTaskError("崩了")

    handlers(_write_then_fail)
    tid = await _task(status="running", claimed_by="w1")

    await _run_task(tid)

    async with async_session() as db:
        rows = (
            await db.execute(
                select(AIAnalysisLog).where(AIAnalysisLog.inspiration_id == "半成品")
            )
        ).scalars().all()
    assert rows == []
    assert (await _get(tid)).status == "failed"


async def test_reload_for_failure_returns_none_when_task_deleted(handlers):
    """任务在失败处理前被删除（404 清理）：返回 None，调用方安全跳过终态写入。"""
    async with async_session() as db:
        db.add(TaskQueue(id=424242, type="batch_analyze", status="running", total=1, done=0))
        await db.commit()
        await db.delete(await db.get(TaskQueue, 424242))
        await db.commit()

        assert await _reload_for_failure(db, 424242) is None


async def test_run_task_safe_swallows_exceptions():
    """并发槽位里的执行协程异常不能冒泡（否则主循环崩、worker 退出）。"""
    import app.worker as w

    async def _boom(_tid):
        raise RuntimeError("handler 炸了")

    original = w._run_task
    w._run_task = _boom
    try:
        await _run_task_safe(1)  # 不应抛出
    finally:
        w._run_task = original


async def test_run_task_safe_reraises_cancelled():
    import app.worker as w

    async def _cancelled(_tid):
        raise asyncio.CancelledError()

    original = w._run_task
    w._run_task = _cancelled
    try:
        with pytest.raises(asyncio.CancelledError):
            await _run_task_safe(1)
    finally:
        w._run_task = original


# ── 崩溃恢复（心跳租约）──


async def test_reset_stale_requeues_task_and_counts_retry():
    old = utcnow() - timedelta(seconds=600)
    tid = await _task(status="running", claimed_by="dead", heartbeat_at=old, retry_count=0)

    await _reset_stale_tasks()

    row = await _get(tid)
    assert row.status == "pending"
    assert row.retry_count == 1  # 崩溃也算一次尝试
    assert row.claimed_by is None
    assert "心跳超时" in (row.error or "")


async def test_reset_stale_marks_failed_when_retries_exhausted():
    """重试已用尽的遗留任务直接判失败——否则每次重启都重跑，永远跑不完。"""
    old = utcnow() - timedelta(seconds=600)
    tid = await _task(
        status="running",
        claimed_by="dead",
        heartbeat_at=old,
        retry_count=2,
        max_retries=2,
    )

    await _reset_stale_tasks()

    row = await _get(tid)
    assert row.status == "failed"
    assert "重试次数已用尽" in (row.error or "")


async def test_reset_stale_keeps_live_worker_tasks():
    """存活 worker 正在跑（心跳新鲜）的任务不能被重置，否则多 worker 部署会互相打断。"""
    tid = await _task(status="running", claimed_by="alive", heartbeat_at=utcnow())

    await _reset_stale_tasks()

    row = await _get(tid)
    assert row.status == "running"
    assert row.claimed_by == "alive"


async def test_reset_stale_cleans_zombie_worker_heartbeats():
    """已死实例的心跳行要清掉，否则表无限膨胀、健康检查也可能误判。"""
    old = utcnow() - timedelta(seconds=600)
    async with async_session() as db:
        db.add(
            ServiceHeartbeat(
                service_id="dead-worker",
                service_type="worker",
                pid=1,
                started_at=old,
                last_heartbeat_at=old,
            )
        )
        await db.commit()

    await _reset_stale_tasks()

    async with async_session() as db:
        assert await db.get(ServiceHeartbeat, "dead-worker") is None


# ── 心跳 ──


async def test_write_heartbeat_upserts_and_refreshes_claimed_tasks():
    tid = await _task(status="running", claimed_by="w1", heartbeat_at=None)

    await _write_heartbeat("w1")

    async with async_session() as db:
        hb = await db.get(ServiceHeartbeat, "w1")
    assert hb is not None and hb.service_type == "worker"
    assert hb.last_heartbeat_at is not None
    assert (await _get(tid)).heartbeat_at is not None


async def test_write_heartbeat_leaves_other_workers_tasks_alone():
    tid = await _task(status="running", claimed_by="other", heartbeat_at=None)

    await _write_heartbeat("w1")

    assert (await _get(tid)).heartbeat_at is None


# ── 主循环 ──


async def test_worker_loop_claims_until_empty_then_sleeps(monkeypatch):
    """主循环：认领并派生执行协程；队列空时不再认领（不会空转打满 CPU）。"""
    import app.worker as w

    claims = {"n": 0}

    async def _claim(_worker_id):
        claims["n"] += 1
        return claims["n"] if claims["n"] <= 2 else None

    ran: list[int] = []

    async def _run(tid):
        ran.append(tid)

    real_sleep = asyncio.sleep
    rounds = {"n": 0}

    async def _fast_sleep(_s):
        rounds["n"] += 1
        if rounds["n"] > 2:
            raise asyncio.CancelledError()  # 跑完两轮后退出循环
        await real_sleep(0)

    monkeypatch.setattr(w, "_claim_next_task", _claim)
    monkeypatch.setattr(w, "_run_task_safe", _run)
    monkeypatch.setattr(w, "_reset_stale_tasks", _claim_none)
    monkeypatch.setattr(w, "settings", type("S", (), {"worker_concurrency": 4})())
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    try:
        with pytest.raises(asyncio.CancelledError):
            await _worker_loop("w1")
    finally:
        monkeypatch.setattr(asyncio, "sleep", real_sleep)

    await real_sleep(0.01)  # 让派生的执行协程跑完
    assert sorted(ran) == [1, 2]


async def _claim_none():
    return None


# ── 剩余边界：并发写锁、回滚失败、任务被删、心跳循环、启动兜底 ──


async def test_claim_silently_skips_when_database_locked(monkeypatch):
    """多 worker 竞争写锁时 SQLite 报 database is locked：静默跳过本轮，不记错误噪音。"""
    from sqlalchemy.exc import OperationalError

    import app.worker as w

    await _task()  # 有可认领任务，才会走到 UPDATE

    state = {"n": 0}

    async with async_session() as db:
        original = db.execute

        async def _locked(*args, **kwargs):
            state["n"] += 1
            if state["n"] >= 2:  # 第 1 次是 SELECT，第 2 次是原子认领 UPDATE
                raise OperationalError("UPDATE", {}, Exception("database is locked"))
            return await original(*args, **kwargs)

        db.execute = _locked  # type: ignore[method-assign]
        try:
            monkeypatch.setattr(w, "async_session", lambda: _Ctx(db))
            assert await _claim_next_task("w1") is None
        finally:
            db.execute = original  # type: ignore[method-assign]


class _Ctx:
    """把已打开的会话包成 async context manager。"""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *args):
        return False


async def test_reload_for_failure_survives_rollback_error(handlers):
    """rollback 本身失败也不能掩盖原始异常：仍要能拿到任务行去写终态。"""
    tid = await _task(status="running", claimed_by="w1")

    async with async_session() as db:
        async def _boom():
            raise RuntimeError("连接已断")

        db.rollback = _boom  # type: ignore[method-assign]
        fresh = await _reload_for_failure(db, tid)

    assert fresh is not None and fresh.id == tid


async def test_run_task_skips_terminal_write_when_task_vanished(handlers, monkeypatch):
    """任务在失败处理前被删除：不能抛错（否则执行协程崩、槽位丢失）。"""
    import app.worker as w

    async def _fail(db, task):
        raise PermanentTaskError("崩了")

    handlers(_fail)
    tid = await _task(status="running", claimed_by="w1")

    async def _gone(db, task_id):
        return None

    monkeypatch.setattr(w, "_reload_for_failure", _gone)
    await _run_task(tid)  # 不应抛错


async def test_heartbeat_loop_writes_then_sleeps(monkeypatch):
    """心跳循环：写一次心跳后 sleep；写失败只告警不退出（否则 worker 失去租约）。"""
    import app.worker as w

    calls = {"writes": 0, "fail_first": True}

    async def _write(worker_id):
        calls["writes"] += 1
        if calls["writes"] == 1 and calls["fail_first"]:
            raise RuntimeError("写心跳失败")

    real_sleep = asyncio.sleep

    async def _fast_sleep(_s):
        if calls["writes"] >= 2:
            raise asyncio.CancelledError()
        await real_sleep(0)

    monkeypatch.setattr(w, "_write_heartbeat", _write)
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    try:
        with pytest.raises(asyncio.CancelledError):
            await w._heartbeat_loop("w1")
    finally:
        monkeypatch.setattr(asyncio, "sleep", real_sleep)

    assert calls["writes"] >= 2  # 失败后被吞掉并继续下一轮


async def test_flush_pending_backfills_on_startup_enqueues(monkeypatch):
    """启动兜底：把攒批队列里的遗留待回填素材一次性入队。"""
    import app.services.task_runners.vector_backfill as vb
    import app.worker as w

    purged: list[bool] = []

    async def _purge(db):
        purged.append(True)

    class _Task:
        id = 7
        total = 3

    async def _flush(db, force=False):
        assert force is True
        return _Task()

    monkeypatch.setattr(vb, "purge_small_backfill_tasks", _purge)
    monkeypatch.setattr(vb, "flush_pending_vector_backfills", _flush)

    await w._flush_pending_backfills_on_startup()

    assert purged == [True]


async def test_flush_pending_backfills_errors_do_not_block_startup(monkeypatch):
    """清理/回填失败只记日志，不能拦住 worker 启动。"""
    import app.services.task_runners.vector_backfill as vb
    import app.worker as w

    async def _boom(db):
        raise RuntimeError("表被锁")

    monkeypatch.setattr(vb, "purge_small_backfill_tasks", _boom)

    await w._flush_pending_backfills_on_startup()  # 不应抛错


async def test_worker_id_is_unique_per_instance():
    from app.worker import _build_worker_id

    a, b = _build_worker_id(), _build_worker_id()
    assert a.startswith("worker-")
    assert a != b  # 多实例必须能区分（心跳租约按 id 判定）
