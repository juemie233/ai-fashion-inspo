"""出图服务（`services/generation_service.py`）核心分支：参数校验/云端预算/历史分组/对账。

`test_unit_generation_reporting.py` 已覆盖报表/导出/清理；本文件补「创建前的守门」与
「历史列表的分组/筛选」这些**出错就静默给错数据或白花钱**的分支：

- 参数校验：尺寸/步数/张数/prompt 越界必须在建任务前 400（否则白等几十秒才失败）；
- 云端预算：按**本地日**统计、只算对应档位；超预算必须执行前二次拦截（创建时已拦一次，
  排队期间可能被别的批次用满）；
- 历史列表：kind/model/status/q（描述或负面词）/参考素材/时间区间/收藏/已入库 的筛选口径；
  **多姿态组**必须整组合并成一行（组内一批命中筛选时整组完整展示，不能只显示 2 个姿态）；
- 对账：running 批次关联任务缺失/终态/超时 → 标记 failed，避免历史永远「生成中」；
- 单张重出：从文件名解析原 seed 只作提示、新批次 count=1 且带上云端 size/watermark。
"""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.generation import GenerationRun
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services import generation_service as gen
from app.utils.time import utcnow


async def _run(**overrides) -> int:
    payload = {
        "kind": "t2i",
        "status": "done",
        "prompt": "法式通勤",
        "negative": None,
        "model_key": "z_image_int8",
        "total": 1,
        "done": 1,
        "failed": 0,
        "params_json": "{}",
        "batch_dir": "",
        "favorite": False,
    }
    payload.update(overrides)
    async with async_session() as db:
        run = GenerationRun(**payload)
        db.add(run)
        await db.commit()
        return run.id


async def _task(status: str) -> int:
    async with async_session() as db:
        task = TaskQueue(type="image_gen", status=status, total=1, done=0, result={})
        db.add(task)
        await db.commit()
        return task.id


async def _link_inspiration(run_id: int) -> None:
    import uuid

    async with async_session() as db:
        db.add(
            Inspiration(
                id=str(uuid.uuid4()),
                source_type="generated",
                file_path="generated/x.png",
                media_type="image",
                generation_run_id=run_id,
            )
        )
        await db.commit()


class TestValidateParams:
    SPEC = {
        "defaults": {
            "width": 1024,
            "height": 1024,
            "steps": 8,
            "cfg": 1.5,
            "sampler": "euler",
            "scheduler": "simple",
            "shift": 3.0,
        },
        "max_pixels": 1024 * 1024,
    }

    def test_size_bounds(self):
        with pytest.raises(HTTPException) as exc_info:
            gen._validate_params({"prompt": "x", "width": 200}, self.SPEC)
        assert "尺寸过小" in exc_info.value.detail

        with pytest.raises(HTTPException) as exc_info:
            gen._validate_params({"prompt": "x", "width": 2000, "height": 2000}, self.SPEC)
        assert "尺寸过大" in exc_info.value.detail

    def test_count_and_steps_bounds(self):
        for count in (0, 99):
            with pytest.raises(HTTPException) as exc_info:
                gen._validate_params({"prompt": "x", "count": count}, self.SPEC)
            assert "张数" in exc_info.value.detail
        for steps in (0, 200):
            with pytest.raises(HTTPException) as exc_info:
                gen._validate_params({"prompt": "x", "steps": steps}, self.SPEC)
            assert "步数" in exc_info.value.detail

    def test_prompt_required_and_length_capped(self):
        with pytest.raises(HTTPException) as exc_info:
            gen._validate_params({"prompt": "   "}, self.SPEC)
        assert "prompt 不能为空" in exc_info.value.detail

        with pytest.raises(HTTPException) as exc_info:
            gen._validate_params({"prompt": "字" * 2001}, self.SPEC)
        assert "prompt 过长" in exc_info.value.detail

    def test_seed_and_defaults(self):
        fixed = gen._validate_params({"prompt": " x ", "seed": 100}, self.SPEC)
        assert fixed["seeds"] == [100]
        assert fixed["prompt"] == "x"  # trim
        assert fixed["negative"] is None
        assert fixed["width"] == 1024

        multi = gen._validate_params({"prompt": "x", "seed": 100, "count": 3}, self.SPEC)
        assert multi["seeds"] == [100, 101, 102]

        auto = gen._validate_params({"prompt": "x", "seed": 0}, self.SPEC)
        assert auto["seed_base"] > 0  # 未指定 seed 时随机
        assert auto["seeds"] == [auto["seed_base"]]

        with_neg = gen._validate_params({"prompt": "x", "negative": " 模糊 "}, self.SPEC)
        assert with_neg["negative"] == "模糊"


class TestPoseAndArkPromptHelpers:
    def test_pose_text_prompt_framing_variants(self):
        base = {"name": "站立抚后脑", "angle": "正面"}
        assert gen._pose_text_prompt({**base, "system": "全身单人"}) == (
            "人物姿势：站立抚后脑（正面），全身照。"
        )
        assert gen._pose_text_prompt({**base, "system": "上半身"}) == (
            "人物姿势：站立抚后脑（正面），上半身构图（半身照）。"
        )
        assert gen._pose_text_prompt({**base, "system": "双人"}) == (
            "人物姿势：站立抚后脑（正面），双人合照。"
        )
        assert gen._pose_text_prompt({"name": ""}) == ""  # 缺名称返回空串

    def test_model_provider_and_ark_framing(self):
        assert gen._model_provider({}) == "comfyui"
        assert gen._model_provider({"provider": "ark"}) == "ark"
        assert gen._ark_framing({"system": "上半身"}) == "上半身"
        assert gen._ark_framing({"system": "双人"}) == "双人合照"
        assert gen._ark_framing({}) == "全身"

    def test_ark_prompt_includes_user_and_caps_length(self):
        text = gen._ark_prompt_text({"name": "站立", "system": "全身"}, "", None, "换成红色背景")
        assert "换成红色背景" in text  # 用户要求不能被丢弃
        assert "全身构图" in text

        long = gen._ark_prompt_text(
            {"name": "站立"},
            "姿势细节" * 100,
            "模糊" * 50,
            "用户要求" * 100,
        )
        assert len(long) <= 300  # 上限 300 字
        assert "用户要求" in long  # 预算不足时优先保留用户要求

    def test_rollback_created(self, monkeypatch):
        rolled: list[bool] = []

        class _Db:
            async def rollback(self):
                rolled.append(True)

        deleted: list[tuple] = []

        def _delete(file_path, thumb_path):
            deleted.append((file_path, thumb_path))

        monkeypatch.setattr(gen, "delete_files", _delete)

        import asyncio

        asyncio.run(gen._rollback_created(_Db(), []))
        assert rolled == [True]
        assert deleted == []

        def _boom(_file_path, _thumb):
            raise OSError("文件被占用")

        monkeypatch.setattr(gen, "delete_files", _boom)
        asyncio.run(gen._rollback_created(_Db(), [("a.png", None)]))  # 不抛
        assert len(rolled) == 2  # 清理失败也要回滚事务

    def test_merged_run_status(self):
        def runs(*statuses):
            return [SimpleNamespace(status=s) for s in statuses]

        assert gen._merged_run_status(runs("done", "running")) == "running"
        assert gen._merged_run_status(runs("failed", "queued")) == "queued"
        assert gen._merged_run_status(runs("failed", "failed")) == "failed"
        assert gen._merged_run_status(runs("cancelled", "cancelled")) == "cancelled"
        assert gen._merged_run_status(runs("done", "failed")) == "done"
        assert gen._merged_run_status([]) == "done"


class TestArkBudget:
    async def test_day_start_is_local_midnight_in_utc(self):
        from datetime import UTC, datetime

        day_start = gen._ark_budget_day_start()
        assert day_start.tzinfo is None  # 库里是 naive UTC

        local_midnight = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        assert day_start == local_midnight.astimezone(UTC).replace(tzinfo=None)

    async def test_used_counts_today_same_model_only(self, client):
        await _run(model_key="seedream")
        await _run(model_key="seedream")
        await _run(model_key="z_image_int8")
        await _run(model_key="seedream", created_at=utcnow() - timedelta(days=2))

        async with async_session() as db:
            assert await gen.ark_budget_used(db, "seedream") == 2

    async def test_exceeded_branches(self, client, monkeypatch):
        await _run(model_key="seedream")
        await _run(model_key="seedream")
        async with async_session() as db:
            monkeypatch.setattr(gen.settings, "seedream_daily_budget_cny", 0)
            assert await gen.ark_budget_exceeded(db, "seedream", 1.0) is None  # 未配预算

            monkeypatch.setattr(gen.settings, "seedream_daily_budget_cny", 1.0)
            message = await gen.ark_budget_exceeded(db, "seedream", 1.0)
            assert message is not None and "预算已用尽" in message
            assert await gen.ark_budget_exceeded(db, "seedream", 0.1) is None


class TestListRunsFilters:
    async def test_filters_shape(self, client):
        target = await _run(kind="i2i", model_key="qwen", status="done", prompt="法式通勤")
        await _run(kind="t2i", model_key="qwen", status="failed", prompt="运动风")
        await _run(
            kind="t2i",
            model_key="z_image_int8",
            status="done",
            prompt="复古港风",
            negative="模糊",
            favorite=True,
            created_at=utcnow() - timedelta(days=10),
        )
        imported_run = await _run(kind="t2i", model_key="qwen", status="done", prompt="入库批")
        await _link_inspiration(imported_run)

        async def ids(**filters):
            async with async_session() as db:
                result = await gen.list_runs(db, size=50, **filters)
            return {item["id"] for item in result["items"]}

        assert await ids(kind="i2i") == {target}
        assert await ids(model_key="z_image_int8", status="done") != {target}
        assert await ids(q="模糊") == {item for item in await ids(q="模糊")}  # 匹配负面词
        assert await ids(q="模糊")  # 非空
        assert await ids(favorite=True) != set()

        async with async_session() as db:
            by_neg = await gen.list_runs(db, size=50, q="模糊")
        assert any(item["id"] != imported_run for item in by_neg["items"])

        async with async_session() as db:
            want_imported = await gen.list_runs(db, size=50, has_imported=True)
        assert {item["id"] for item in want_imported["items"]} == {imported_run}

        since = utcnow() - timedelta(days=1)
        async with async_session() as db:
            recent = await gen.list_runs(db, size=50, since=since)
        assert imported_run in {item["id"] for item in recent["items"]}

        async with async_session() as db:
            by_ref = await gen.list_runs(db, size=50, reference_id="不存在")
        assert by_ref["total"] == 0


class TestListRunsGrouping:
    async def test_multi_pose_group_merged_into_one_row(self, client):
        g1 = await _run(group_id="g1", status="running", total=2, done=1)
        g2 = await _run(group_id="g1", status="failed", total=2, done=1, favorite=True)
        single = await _run(group_id=None)

        async with async_session() as db:
            result = await gen.list_runs(db, size=50)

        ids = [item["id"] for item in result["items"]]
        assert len(result["items"]) == 2  # 组 1 行 + 单批 1 行
        assert single in ids
        group_row = next(item for item in result["items"] if item.get("group_id") == "g1")
        assert group_row["run_ids"] == [g1, g2]
        assert group_row["total"] == 4  # 组内聚合
        assert group_row["done"] == 2
        assert group_row["status"] == "running"  # 有在跑的显示在跑
        assert group_row["favorite"] is True  # 组内任一收藏即收藏

    async def test_group_member_filter_returns_whole_group(self, client):
        await _run(group_id="g2", status="done", prompt="法式")
        await _run(group_id="g2", status="done", prompt="运动")

        async with async_session() as db:
            result = await gen.list_runs(db, size=50, q="法式")

        assert result["total"] == 1  # 一个组
        row = result["items"][0]
        assert len(row["run_ids"]) == 2  # 整组完整展示（不能只显示命中的那批）


class TestUpdateRunMeta:
    async def test_title_note_and_clear(self, client):
        run_id = await _run()
        async with async_session() as db:
            updated = await gen.update_run_meta(db, run_id, title=" 我的批次 ", note="备注")
            assert updated["title"] == "我的批次"
            original_updated_at = updated["updated_at"]

            cleared = await gen.update_run_meta(db, run_id, clear_title=True, clear_note=True)
            assert cleared["title"] in (None, "")
            assert cleared["note"] in (None, "")

            blank = await gen.update_run_meta(db, run_id, title="   ")
            assert blank["title"] in (None, "")

            # 只改标注不推进 updated_at（统计均耗时依赖它）
            assert blank["updated_at"] == original_updated_at

    async def test_limits_and_missing(self, client):
        run_id = await _run()
        async with async_session() as db:
            for kwargs, expected in (
                ({"title": "字" * 65}, "命名最多 64"),
                ({"note": "字" * 2001}, "备注最多 2000"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await gen.update_run_meta(db, run_id, **kwargs)
                assert expected in exc_info.value.detail

            with pytest.raises(HTTPException) as exc_info:
                await gen.update_run_meta(db, 999999, favorite=True)
            assert exc_info.value.status_code == 404

    async def test_race_rowcount_zero_is_404(self, client, monkeypatch):
        run_id = await _run()
        async with async_session() as db:
            monkeypatch.setattr(
                db, "execute", AsyncMock(return_value=SimpleNamespace(rowcount=0))
            )
            with pytest.raises(HTTPException) as exc_info:
                await gen.update_run_meta(db, run_id, favorite=True)
            assert exc_info.value.status_code == 404


class TestRegenerateAndReconcile:
    async def test_regenerate_404_and_payload(self, client, monkeypatch):
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await gen.regenerate_candidate(db, 999999, "01_seed-42.png")
        assert exc_info.value.status_code == 404

        run_id = await _run(
            params_json=json.dumps({"width": 768, "size": "1536x2048", "watermark": True}),
            reference_ids=json.dumps(["ref-1"]),
        )
        captured: dict = {}

        async def _fake_create(_db, payload, **_kwargs):
            captured.update(payload)
            return {"run_id": 5, "task_id": 6}

        monkeypatch.setattr(gen, "create_run", _fake_create)

        async with async_session() as db:
            result = await gen.regenerate_candidate(db, run_id, "03_seed-123456.png", seed=7)

        assert result == {
            "run_id": 5,
            "task_id": 6,
            "original_seed": 123456,
            "source_run_id": run_id,
        }
        assert captured["count"] == 1
        assert captured["seed"] == 7
        assert captured["size"] == "1536x2048"  # 云端档位字段必须带
        assert captured["watermark"] is True
        assert captured["reference_ids"] == ["ref-1"]

    async def test_reconcile_branches(self, client):
        # 1) 关联任务不存在
        no_task = await _run(status="running", task_id=None)
        # 2) 关联任务已终态
        terminal_task = await _task("success")
        terminal = await _run(status="running", task_id=terminal_task)
        # 3) 超时无更新
        stale = await _run(
            status="running",
            updated_at=utcnow() - timedelta(hours=999),
        )
        # 4) 健康批次不动
        healthy_task = await _task("running")
        healthy = await _run(status="running", task_id=healthy_task)

        async with async_session() as db:
            result = await gen.reconcile_stale_generation_runs(db)

        assert result["reconciled"] == 3
        async with async_session() as db:
            for run_id in (no_task, terminal, stale):
                row = await db.get(GenerationRun, run_id)
                assert row.status == "failed", run_id
                assert row.error
            assert (await db.get(GenerationRun, healthy)).status == "running"

    async def test_reconcile_nothing_to_do(self, client):
        await _run(status="done")
        async with async_session() as db:
            assert await gen.reconcile_stale_generation_runs(db) == {"reconciled": 0}
