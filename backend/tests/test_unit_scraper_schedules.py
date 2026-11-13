"""定时采集计划（`services/scraper/schedules.py`）测试：CRUD、到期执行、手动立即执行。

这是「定时采集」的核心逻辑，出错的表现都很隐蔽：
- 关键词**轮换**写错 → 每次都用同一个词，采集结果永远重复；
- `next_run_at` 推进写成 `now + interval` → 服务停机恢复后节奏漂移（每天 08:00 的计划
  会变成 09:30）；
- 到期执行缺少**乐观锁** → 「立即执行」与调度循环同时触发，同一计划一秒内跑两次；
- 手动执行失败时把 `run_count`/`next_run_at` 改了却没启动任务 → 计划看起来跑过、实际没跑。

本文件覆盖这些分支（也覆盖「Chrome 未就绪时立即执行要返回明确 400」的预检）。
"""

import json
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.scraper import ScraperSchedule, ScraperTask
from app.schemas.scraper import ScraperScheduleCreate, ScraperScheduleUpdate
from app.services.scraper import schedules as sched_module
from app.utils.time import utcnow


@pytest.fixture
def launched(monkeypatch):
    """拦截 `_safe_launch`（真正起子进程的地方），返回被启动的任务列表。"""
    started: list[ScraperTask] = []

    async def _fake(_db, task):
        started.append(task)

    monkeypatch.setattr(sched_module, "_safe_launch", _fake)
    return started


async def _create(**overrides) -> ScraperSchedule:
    payload = {
        "platform": "xiaohongshu",
        "keywords": ["法式", "通勤"],
        "max_count": 20,
        "interval_minutes": 1440,
        "enabled": True,
    }
    payload.update(overrides)
    async with async_session() as db:
        return await sched_module.create_schedule(db, ScraperScheduleCreate(**payload))


class TestPureHelpers:
    def test_platform_normalized_and_validated(self):
        assert sched_module._validate_schedule_platform("  XiaOHongShu ") == "xiaohongshu"
        with pytest.raises(HTTPException) as exc_info:
            sched_module._validate_schedule_platform("weibo")
        assert exc_info.value.status_code == 400

    def test_build_config_rotates_keywords_by_run_count(self):
        """关键词按 run_count 轮换：第 0 次用第一个词，第 1 次用第二个……循环。"""
        sched = ScraperSchedule(
            platform="xiaohongshu",
            keywords=json.dumps(["法式", "通勤"]),
            max_count=30,
            sort_mode="popular",
            run_count=1,
        )

        config = sched_module._build_schedule_task_config(sched)

        assert config["keywords"] == ["通勤"]  # run_count=1 → 第二个
        assert config["max_count"] == 30
        assert config["headless"] is True
        assert config["sort_mode"] == "popular"

        sched.run_count = 2  # 回到第一个（2 % 2 == 0）
        assert sched_module._build_schedule_task_config(sched)["keywords"] == ["法式"]

    def test_build_config_douyin_has_no_sort_mode_or_cdp(self):
        sched = ScraperSchedule(
            platform="douyin",
            keywords=json.dumps(["穿搭"]),
            max_count=10,
            sort_mode="popular",  # 即使残留也不该带出去
            run_count=0,
        )

        config = sched_module._build_schedule_task_config(sched)

        assert config["cdp_port"] is None
        assert "sort_mode" not in config

    def test_advance_keeps_fixed_cadence_when_late(self):
        """停机错过多个周期后，仍按原节奏推进到下一个未来槽位（不漂移）。"""
        now = utcnow()
        due = now - timedelta(days=2, hours=5)  # 错过 2 次（每天一次）还多 5 小时
        nxt = sched_module._advance_next_run(1440, due, now)

        assert nxt > now
        # 推进后与到期点之间恰好是整数个间隔
        assert (nxt - due) % timedelta(minutes=1440) == timedelta(0)
        assert nxt - now < timedelta(minutes=1440)


class TestCreateAndList:
    async def test_create_enabled_sets_next_run(self, client):
        sched = await _create()

        assert sched.platform == "xiaohongshu"
        assert json.loads(sched.keywords) == ["法式", "通勤"]
        assert sched.enabled is True
        assert sched.next_run_at is not None
        assert sched.next_run_at > utcnow()

    async def test_create_disabled_has_no_next_run(self, client):
        sched = await _create(enabled=False)
        assert sched.next_run_at is None

    async def test_create_rejects_empty_keywords(self, client):
        with pytest.raises(HTTPException) as exc_info:
            await _create(keywords=["  ", ""])
        assert exc_info.value.status_code == 400

    async def test_create_rejects_unknown_sort_mode(self, client):
        with pytest.raises(HTTPException) as exc_info:
            await _create(sort_mode="random")
        assert exc_info.value.status_code == 400

    async def test_create_douyin_drops_sort_mode(self, client):
        sched = await _create(platform="douyin", sort_mode="popular")
        assert sched.sort_mode is None  # 排序仅小红书生效

    async def test_create_general_sort_mode_kept_as_is_for_xhs(self, client):
        sched = await _create(sort_mode="general")
        assert sched.sort_mode == "general"  # 创建路径保留 general（更新路径归一化）

    async def test_list_orders_by_id_desc(self, client):
        first = await _create()
        second = await _create(platform="douyin")

        async with async_session() as db:
            rows = await sched_module.list_schedules(db)

        assert [row.id for row in rows] == [second.id, first.id]


class TestUpdate:
    async def _get(self, schedule_id: int) -> ScraperSchedule:
        async with async_session() as db:
            return await db.get(ScraperSchedule, schedule_id)

    async def test_missing_schedule_404(self, client):
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await sched_module.update_schedule(db, 999999, ScraperScheduleUpdate())
        assert exc_info.value.status_code == 404

    async def test_keywords_trim_and_empty_rejected(self, client):
        sched = await _create()

        async with async_session() as db:
            updated = await sched_module.update_schedule(
                db, sched.id, ScraperScheduleUpdate(keywords=[" 新词 "])
            )
        assert json.loads(updated.keywords) == ["新词"]

        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await sched_module.update_schedule(
                    db, sched.id, ScraperScheduleUpdate(keywords=["  "])
                )
        assert exc_info.value.status_code == 400

    async def test_max_count_updated(self, client):
        sched = await _create()

        async with async_session() as db:
            updated = await sched_module.update_schedule(
                db, sched.id, ScraperScheduleUpdate(max_count=99)
            )
        assert updated.max_count == 99

    async def test_sort_mode_validation_and_general_normalized(self, client):
        sched = await _create(sort_mode="latest")

        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await sched_module.update_schedule(
                    db, sched.id, ScraperScheduleUpdate(sort_mode="random")
                )
        assert exc_info.value.status_code == 400

        async with async_session() as db:
            updated = await sched_module.update_schedule(
                db, sched.id, ScraperScheduleUpdate(sort_mode="general")
            )
        assert updated.sort_mode is None  # 「综合」归一化为 NULL，避免与字符串并存

    async def test_interval_change_while_enabled_recomputes_next_run(self, client):
        sched = await _create(interval_minutes=1440)

        async with async_session() as db:
            updated = await sched_module.update_schedule(
                db, sched.id, ScraperScheduleUpdate(interval_minutes=60)
            )
        assert updated.interval_minutes == 60
        assert updated.next_run_at is not None
        delta = updated.next_run_at - utcnow()
        assert timedelta(minutes=55) < delta <= timedelta(minutes=60)

    async def test_interval_change_while_disabled_keeps_next_run_none(self, client):
        sched = await _create(enabled=False)

        async with async_session() as db:
            updated = await sched_module.update_schedule(
                db, sched.id, ScraperScheduleUpdate(interval_minutes=60)
            )
        assert updated.next_run_at is None

    async def test_disable_clears_next_run_enable_restores(self, client):
        sched = await _create()

        async with async_session() as db:
            disabled = await sched_module.update_schedule(
                db, sched.id, ScraperScheduleUpdate(enabled=False)
            )
        assert disabled.enabled is False
        assert disabled.next_run_at is None

        async with async_session() as db:
            enabled = await sched_module.update_schedule(
                db, sched.id, ScraperScheduleUpdate(enabled=True)
            )
        assert enabled.enabled is True
        assert enabled.next_run_at is not None


class TestDelete:
    async def test_missing_404(self, client):
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await sched_module.delete_schedule(db, 999999)
        assert exc_info.value.status_code == 404

    async def test_deletes_row(self, client):
        sched = await _create()

        async with async_session() as db:
            result = await sched_module.delete_schedule(db, sched.id)

        assert result == {"deleted": 1, "id": sched.id}
        async with async_session() as db:
            assert await db.get(ScraperSchedule, sched.id) is None


class TestRunScheduleNow:
    async def test_missing_404(self, client, launched):
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await sched_module.run_schedule_now(db, 999999)
        assert exc_info.value.status_code == 404

    async def test_douyin_triggers_task_and_starts_it(self, client, launched):
        sched = await _create(platform="douyin")

        async with async_session() as db:
            result = await sched_module.run_schedule_now(db, sched.id)

        assert result["message"] == f"计划 {sched.id} 已触发"
        assert result["task_id"] == launched[0].id
        async with async_session() as db:
            task = await db.get(ScraperTask, result["task_id"])
            refreshed = await db.get(ScraperSchedule, sched.id)
        assert task.status == "pending"
        assert task.platform == "douyin"
        assert refreshed.last_task_id == task.id
        assert refreshed.run_count == 1
        assert refreshed.last_run_at is not None

    async def test_xiaohongshu_cdp_unavailable_returns_400(self, client, launched, monkeypatch):
        monkeypatch.setattr(
            sched_module, "_check_cdp", lambda _port: (False, "端口不通", False)
        )
        sched = await _create()

        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await sched_module.run_schedule_now(db, sched.id)

        assert exc_info.value.status_code == 400
        assert "Chrome 调试端口不可用" in exc_info.value.detail
        assert launched == []  # 预检失败不能起任务

    async def test_xiaohongshu_requires_real_chrome(self, client, launched, monkeypatch):
        monkeypatch.setattr(
            sched_module, "_check_cdp", lambda _port: (True, "360 极速浏览器", False)
        )
        sched = await _create()

        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await sched_module.run_schedule_now(db, sched.id)

        assert exc_info.value.status_code == 400
        assert "必须使用 Google Chrome" in exc_info.value.detail

    async def test_xiaohongshu_ok_path_and_due_schedule_advances(self, client, launched, monkeypatch):
        monkeypatch.setattr(sched_module, "_check_cdp", lambda _port: (True, "Chrome", True))
        sched = await _create()
        # 人为把计划置为「已到期」：手动执行后应推进到下一个未来槽位
        async with async_session() as db:
            row = await db.get(ScraperSchedule, sched.id)
            row.next_run_at = utcnow() - timedelta(minutes=5)
            await db.commit()

        async with async_session() as db:
            await sched_module.run_schedule_now(db, sched.id)

        async with async_session() as db:
            refreshed = await db.get(ScraperSchedule, sched.id)
        assert refreshed.next_run_at is not None
        assert refreshed.next_run_at > utcnow()
        assert launched and launched[0].platform == "xiaohongshu"


class TestRunDueSchedules:
    async def test_nothing_due_returns_zero(self, client, launched):
        await _create()  # 刚创建：下一分钟才到期（1440 分钟后）
        async with async_session() as db:
            assert await sched_module.run_due_schedules(db) == 0
        assert launched == []

    async def test_due_schedule_creates_task_and_advances(self, client, launched):
        sched = await _create(enabled=True)
        async with async_session() as db:
            row = await db.get(ScraperSchedule, sched.id)
            row.next_run_at = utcnow() - timedelta(minutes=10)
            await db.commit()

        async with async_session() as db:
            triggered = await sched_module.run_due_schedules(db)

        assert triggered == 1
        assert len(launched) == 1
        async with async_session() as db:
            refreshed = await db.get(ScraperSchedule, sched.id)
        assert refreshed.run_count == 1
        assert refreshed.last_task_id == launched[0].id
        assert refreshed.next_run_at > utcnow()  # 已推进到未来
        assert refreshed.last_run_at is not None

    async def test_second_pass_is_idempotent(self, client, launched):
        """同一到期点被处理后再跑一次不会重复触发（乐观锁推进后不再是 due）。"""
        sched = await _create(enabled=True)
        async with async_session() as db:
            row = await db.get(ScraperSchedule, sched.id)
            row.next_run_at = utcnow() - timedelta(minutes=10)
            await db.commit()

        async with async_session() as db:
            assert await sched_module.run_due_schedules(db) == 1
        async with async_session() as db:
            assert await sched_module.run_due_schedules(db) == 0

    async def test_task_creation_failure_still_advances_and_counts(
        self, client, launched, monkeypatch, caplog
    ):
        """建任务失败（如 DB 约束）也要推进 next_run_at，避免同一计划反复刷屏重试。"""
        sched = await _create(enabled=True)
        async with async_session() as db:
            row = await db.get(ScraperSchedule, sched.id)
            row.next_run_at = utcnow() - timedelta(minutes=10)
            await db.commit()

        from app.models.scraper import ScraperTask as TaskModel

        original_init = TaskModel.__init__

        def _boom(self, *args, **kwargs):
            raise RuntimeError("模拟建任务失败")

        monkeypatch.setattr(TaskModel, "__init__", _boom)

        async with async_session() as db:
            triggered = await sched_module.run_due_schedules(db)

        assert triggered == 1  # 仍计入触发（节奏照常推进）
        async with async_session() as db:
            refreshed = await db.get(ScraperSchedule, sched.id)
        assert refreshed.next_run_at > utcnow()
        assert launched == []  # 没有任务可启动
        monkeypatch.setattr(TaskModel, "__init__", original_init)

    async def test_disabled_or_no_next_run_schedules_are_ignored(self, client, launched):
        await _create(enabled=False)  # 停用：next_run_at 为 None
        async with async_session() as db:
            assert await sched_module.run_due_schedules(db) == 0
        assert launched == []
