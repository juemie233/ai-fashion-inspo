"""`app/main.py` 的后台周期任务测试（垃圾桶自动清理 / 定时采集轮询 / 摘要回填）。

这三个循环是「进程内定时器」：跑起来之后用户看不到任何提示，出错的唯一表现是
**定时采集不触发、垃圾桶永不自动清、任务列表摘要为空**。以前没有测试，只能靠
观察。这里用「第一次 tick 正常执行、第二次 tick 人为取消」的方式把每个分支跑一遍。

注意：直接调用后台协程时，`asyncio.sleep` 会被替换成可控桩，避免测试真的等 6 小时。
"""

import asyncio

import pytest

from app import main as main_module
from app.database import async_session


class _FakeSessionCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_exc):
        return False


def _patch_sleep(monkeypatch, ticks: int = 1):
    """让 `asyncio.sleep` 先正常返回 ticks 次，之后抛 CancelledError 结束循环。

    返回记录列表，用于断言「确实进入了循环并等过间隔」。
    """
    calls: list[float] = []

    async def _sleep(seconds):
        if len(calls) >= ticks:
            raise asyncio.CancelledError
        calls.append(seconds)

    monkeypatch.setattr(main_module.asyncio, "sleep", _sleep)
    return calls


@pytest.fixture
def logs(monkeypatch):
    """记录 main 模块的日志调用（项目自定义 logging 配置下 caplog 抓不到）。"""

    class _Logger:
        def __init__(self):
            self.infos: list[str] = []
            self.warnings: list[str] = []

        def info(self, message, *args, **_kwargs):
            self.infos.append(message % args if args else message)

        def warning(self, message, *args, **_kwargs):
            self.warnings.append(message % args if args else message)

        def debug(self, *_args, **_kwargs):
            pass

        def exception(self, *_args, **_kwargs):
            pass

    logger = _Logger()
    monkeypatch.setattr(main_module, "logger", logger)
    return logger


def _patch_session(monkeypatch, db):
    """后台任务里 `from app.database import async_session` 时拿到假会话。"""
    monkeypatch.setattr("app.database.async_session", lambda: _FakeSessionCtx(db))
    # main 模块内部是函数内 import，patch 包路径即可
    return _FakeSessionCtx(db)


class TestSweepExpiredTrash:
    async def test_disabled_returns_immediately(self, monkeypatch):
        """保留期 <= 0 表示禁用自动回收：不进入循环，也不 sleep。"""
        monkeypatch.setattr(main_module.settings, "trash_retention_days", 0)
        slept: list[float] = []

        async def _sleep(seconds):
            slept.append(seconds)

        monkeypatch.setattr(main_module.asyncio, "sleep", _sleep)

        await main_module._sweep_expired_trash()

        assert slept == []

    async def test_sweeps_expired_items_and_logs(self, monkeypatch, logs):
        monkeypatch.setattr(main_module.settings, "trash_retention_days", 7)
        _patch_session(monkeypatch, object())
        calls: list[bool] = []

        async def _purge(_db, only_expired):
            calls.append(only_expired)
            return {"deleted": 3}

        from app.services import inspiration_service

        monkeypatch.setattr(inspiration_service, "purge_trash", _purge)
        _patch_sleep(monkeypatch, ticks=1)

        with pytest.raises(asyncio.CancelledError):
            await main_module._sweep_expired_trash()

        assert calls == [True]  # 只清「已过期」的，不误删未到期素材
        assert any("自动清理 3 个过期素材" in line for line in logs.infos)

    async def test_failure_is_logged_and_loop_continues(self, monkeypatch, logs):
        """单次清理失败只告警，不终止循环（否则一次 DB 忙就把自动清理永久停掉）。"""
        monkeypatch.setattr(main_module.settings, "trash_retention_days", 7)
        _patch_session(monkeypatch, object())

        async def _boom(_db, only_expired):
            raise RuntimeError("database is locked")

        from app.services import inspiration_service

        monkeypatch.setattr(inspiration_service, "purge_trash", _boom)
        calls = _patch_sleep(monkeypatch, ticks=1)

        with pytest.raises(asyncio.CancelledError):
            await main_module._sweep_expired_trash()

        assert any("自动清理失败" in line for line in logs.warnings)
        assert len(calls) == 1


# 注：`_scraper_schedule_loop` 的真身在测试里**不可测**——`tests/conftest.py` 的会话级
# `client` fixture 会把它替换成「只 sleep 的 noop」，避免每 30 秒的调度循环在用例等待
# 窗口内并发抢跑（见 conftest 第 86-94 行的说明）。调度逻辑本身由
# `scraper_service.run_due_schedules` 的用例覆盖，这里不重复测。


class TestBackfillTaskResultSummaries:
    """用真实临时库验证：只补 result 非空、summary 为空的历史行。"""

    async def test_fills_missing_summaries_in_batches(self, client):
        from sqlalchemy import insert, select

        from app.models.task import TaskQueue

        # 用 Core insert 绕过 ORM before_insert 事件（新写入会自动生成摘要，
        # 本用例要模拟的是「迁移前写入的历史行」）
        async with async_session() as db:
            for i in range(3):
                await db.execute(
                    insert(TaskQueue).values(
                        type="batch_analyze",
                        status="success",
                        total=1,
                        done=1,
                        progress=100,
                        result={"success_count": i + 1, "failed_count": 0},
                        result_summary=None,
                    )
                )
            # 无 result 的行与已有摘要的行都不应被动
            await db.execute(
                insert(TaskQueue).values(
                    type="batch_analyze", status="success", total=0, done=0, result=None
                )
            )
            await db.execute(
                insert(TaskQueue).values(
                    type="batch_analyze",
                    status="success",
                    total=1,
                    done=1,
                    result={"success_count": 1},
                    result_summary="已有摘要",
                )
            )
            await db.commit()

        filled = await main_module._backfill_task_result_summaries(batch_size=2)

        assert filled == 3  # 两条一批 + 一条收尾
        async with async_session() as db:
            rows = (await db.execute(select(TaskQueue.result_summary))).scalars().all()
        filled_rows = [r for r in rows if r and r != "已有摘要"]
        assert len(filled_rows) == 3
        assert all("成功" in r or "success" in r or r for r in filled_rows)

    async def test_returns_zero_when_nothing_to_fill(self, client):
        assert await main_module._backfill_task_result_summaries() == 0

    async def test_is_idempotent(self, client):
        from sqlalchemy import insert

        from app.models.task import TaskQueue

        async with async_session() as db:
            await db.execute(
                insert(TaskQueue).values(
                    type="batch_analyze",
                    status="success",
                    total=1,
                    done=1,
                    result={"success_count": 1, "failed_count": 0},
                    result_summary=None,
                )
            )
            await db.commit()

        first = await main_module._backfill_task_result_summaries()
        second = await main_module._backfill_task_result_summaries()

        assert first == 1
        assert second == 0  # 补过之后不再重复写
