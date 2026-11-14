"""AI 分析队列/历史删除/结果对比/应用标签接口测试（`routers/ai_analysis.py`）。

队列接口此前完全没覆盖，它们是前端「排队缩略图 + 取消排队 + 运行中进度」的数据源：

- `queue/pending`：合并「排队中」与「进行中」两类素材，带缩略图/文件路径，未在状态表
  里的素材用空信息占位而不是崩；暂停状态透传；
- 取消排队：排队中的可取消（取消 asyncio 任务并移除），**正在执行的返回 409**，
  不在队列返回 404——这三种结果直接决定用户看到的提示；
- 结果对比（单条/批量）：404/400 的区分与成功透传；
- 历史删除/详情：不存在时 404；
- 应用分析标签：提示「已把 N 个标签应用到素材」；
- `_resolve_prompt_combinations`：模型去重、prompt_id=0 表示当前默认、历史版本标签
  带保存时间、非法版本 400。
"""

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.routers import ai_analysis as aa
from app.routers import ai_shared as shared
from app.services import ai_analysis_service as ai_svc


@pytest.fixture(autouse=True)
def _reset_queue_state():
    shared._pending_queue.clear()
    shared._active_analyses.clear()
    shared._task_by_id.clear()
    shared.set_queue_paused(False)
    yield
    shared._pending_queue.clear()
    shared._active_analyses.clear()
    shared._task_by_id.clear()
    shared.set_queue_paused(False)


class _FakeTask:
    def __init__(self, *, done=False):
        self._done = done
        self.cancelled = False

    def done(self):
        return self._done

    def cancel(self):
        self.cancelled = True


class TestPendingQueueEndpoints:
    async def test_empty_queue(self):
        out = await aa.get_pending_queue(db=None)
        assert out == {"items": [], "paused": False}

    async def test_paused_flag_is_propagated(self):
        shared.set_queue_paused(True)

        out = await aa.get_pending_queue(db=None)

        assert out["paused"] is True

    async def test_merges_pending_and_active_with_previews(self, monkeypatch):
        shared._pending_queue.extend(["a", "b"])
        shared._active_analyses.update({"b": "analyzing", "c": "running"})

        async def _preview_map(_db, ids):
            return {
                "a": {"thumbnail_path": "thumbs/a.jpg", "file_path": "images/a.jpg"},
                "c": {"thumbnail_path": None, "file_path": "images/c.jpg"},
            }

        monkeypatch.setattr(aa.ai_svc, "get_inspiration_preview_map", _preview_map)

        out = await aa.get_pending_queue(db=None)

        items = {item["inspiration_id"]: item for item in out["items"]}
        assert items["a"]["status"] == "排队中"
        assert items["a"]["thumbnail_path"] == "thumbs/a.jpg"
        # b 既在排队又已被执行：只出现一次（排队态）
        assert [i["inspiration_id"] for i in out["items"]].count("b") == 1
        # c 只在进行中：带状态与空缩略图（预览缺失占位）
        assert items["c"]["status"] == "running"
        assert items["c"]["thumbnail_path"] is None
        assert items["c"]["file_path"] == "images/c.jpg"

    async def test_active_without_pending_is_listed(self, monkeypatch):
        """只有进行中、没有排队：也要列出（前端靠它显示进度与缩略图）。"""
        shared._active_analyses.update({"a": "running"})
        requested: list[list[str]] = []

        async def _preview_map(_db, ids):
            requested.append(list(ids))
            return {"a": {}}  # 预览缺失 → 用 None 占位

        monkeypatch.setattr(aa.ai_svc, "get_inspiration_preview_map", _preview_map)

        out = await aa.get_pending_queue(db=None)

        assert out["items"] == [
            {
                "inspiration_id": "a",
                "thumbnail_path": None,
                "file_path": None,
                "status": "running",
            }
        ]
        assert requested == [["a"]]


class TestCancelQueueItem:
    async def test_cancel_pending_task(self):
        task = _FakeTask(done=False)
        shared._pending_queue.extend(["a"])
        shared._task_by_id["a"] = task

        result = await aa.cancel_queue_item("a")

        assert result == {"message": "已取消排队任务"}
        assert task.cancelled is True
        assert "a" not in shared._pending_queue
        assert "a" not in shared._task_by_id

    async def test_cancel_pending_when_task_already_done(self):
        shared._pending_queue.extend(["b"])
        shared._task_by_id["b"] = _FakeTask(done=True)

        result = await aa.cancel_queue_item("b")

        assert result["message"] == "已取消排队任务"
        assert "b" not in shared._pending_queue

    async def test_running_item_conflicts(self):
        shared._active_analyses["c"] = "analyzing"

        with pytest.raises(HTTPException) as exc_info:
            await aa.cancel_queue_item("c")

        assert exc_info.value.status_code == 409
        assert "正在执行中" in exc_info.value.detail

    async def test_unknown_item_404(self):
        with pytest.raises(HTTPException) as exc_info:
            await aa.cancel_queue_item("missing")
        assert exc_info.value.status_code == 404

    async def test_active_analyses_endpoint(self):
        shared._active_analyses["d"] = "running"

        out = await aa.get_active_analyses()

        assert out == {"active_analyses": {"d": "running"}, "count": 1}


class TestPromptCombinations:
    VERSIONS = [
        {"saved_at": "2026-01-02T03:04:05", "prompt": "v2 文本"},
        {"saved_at": "", "prompt": "v1 文本"},
    ]

    def _patch_versions(self, monkeypatch):
        monkeypatch.setattr(aa, "get_prompt_versions", lambda _model: list(self.VERSIONS))

    def test_defaults_when_no_models_or_prompt_ids(self, monkeypatch):
        self._patch_versions(monkeypatch)

        combos = aa._resolve_prompt_combinations(None, None)

        assert combos == [
            {
                "model": aa.settings.ollama_vision_model,
                "prompt": None,
                "prompt_label": "当前默认提示词",
                "prompt_id": 0,
            }
        ]

    def test_models_deduped_and_trimmed(self, monkeypatch):
        self._patch_versions(monkeypatch)

        combos = aa._resolve_prompt_combinations(["  m1 ", "m1", "", "m2"], None)

        assert [c["model"] for c in combos] == ["m1", "m2"]

    def test_prompt_ids_dedupe_and_labels(self, monkeypatch):
        self._patch_versions(monkeypatch)

        combos = aa._resolve_prompt_combinations(["m"], [0, 0, 1, 2])

        assert [(c["prompt_id"], c["prompt"]) for c in combos] == [
            (0, None),
            (1, "v2 文本"),
            (2, "v1 文本"),
        ]
        # 保存时间非空时带进标签；空 saved_at 只留「版本 #N」
        assert combos[1]["prompt_label"] == "版本 #1（2026-01-02 03:04:05）"
        assert combos[2]["prompt_label"] == "版本 #2"

    def test_invalid_prompt_id_400(self, monkeypatch):
        self._patch_versions(monkeypatch)

        with pytest.raises(HTTPException) as exc_info:
            aa._resolve_prompt_combinations(None, [99])

        assert exc_info.value.status_code == 400
        assert "0~2" in exc_info.value.detail


class TestHistoryEndpoints:
    async def test_delete_all_failed(self, client, monkeypatch):
        async def _zero(_db):
            return 0

        monkeypatch.setattr(aa.ai_svc, "delete_failed_logs", _zero)
        async with async_session() as db:
            out = await aa.delete_all_failed_logs(db)
        assert out == {"message": "没有失败的记录", "count": 0}

    async def test_delete_all_failed_counts(self, client, monkeypatch):
        async def _three(_db):
            return 3

        monkeypatch.setattr(aa.ai_svc, "delete_failed_logs", _three)
        async with async_session() as db:
            out = await aa.delete_all_failed_logs(db)
        assert out["count"] == 3
        assert "已删除 3 条失败记录" in out["message"]

    async def test_analysis_detail_and_delete(self, client, monkeypatch):
        async def _none(_db, _log_id):
            return None

        monkeypatch.setattr(aa.ai_svc, "get_analysis_detail", _none)
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await aa.get_analysis_detail(1, db)
        assert exc_info.value.status_code == 404

        async def _detail(_db, log_id):
            return {"id": log_id, "tags": []}

        monkeypatch.setattr(aa.ai_svc, "get_analysis_detail", _detail)
        async with async_session() as db:
            assert await aa.get_analysis_detail(2, db) == {"id": 2, "tags": []}

        async def _false(_db, _log_id):
            return False

        monkeypatch.setattr(aa.ai_svc, "delete_analysis_log", _false)
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await aa.delete_analysis_log(1, db)
        assert exc_info.value.status_code == 404

        async def _true(_db, _log_id):
            return True

        monkeypatch.setattr(aa.ai_svc, "delete_analysis_log", _true)
        async with async_session() as db:
            out = await aa.delete_analysis_log(7, db)
        assert out == {"message": "分析记录 #7 已删除"}


class TestCompareAndApply:
    async def test_compare_not_found(self, client, monkeypatch):
        async def _boom(_db, _id):
            raise ai_svc.AIAnalysisNotFoundError("素材没有分析记录")

        monkeypatch.setattr(aa.ai_svc, "get_analysis_comparison", _boom)
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await aa.compare_analyses("insp-1", db)
        assert exc_info.value.status_code == 404

    async def test_compare_success(self, client, monkeypatch):
        async def _ok(_db, _id):
            return {"analyses": [], "tag_diff": {}}

        monkeypatch.setattr(aa.ai_svc, "get_analysis_comparison", _ok)
        async with async_session() as db:
            assert await aa.compare_analyses("insp-1", db) == {
                "analyses": [],
                "tag_diff": {},
            }

    async def test_compare_batch_validation_and_errors(self, client, monkeypatch):
        async with async_session() as db:
            for payload in ({}, {"log_ids": "x"}, {"log_ids": []}, {"log_ids": [1, "2"]}):
                with pytest.raises(HTTPException) as exc_info:
                    await aa.compare_analyses_batch(payload, db)
                assert exc_info.value.status_code == 400

            async def _not_found(_db, _ids):
                raise ai_svc.AIAnalysisNotFoundError("记录不存在")

            monkeypatch.setattr(aa.ai_svc, "compare_logs_batch", _not_found)
            with pytest.raises(HTTPException) as exc_info:
                await aa.compare_analyses_batch({"log_ids": [1, 2]}, db)
            assert exc_info.value.status_code == 404

            async def _bad(_db, _ids):
                raise ValueError("需为同一素材")

            monkeypatch.setattr(aa.ai_svc, "compare_logs_batch", _bad)
            with pytest.raises(HTTPException) as exc_info:
                await aa.compare_analyses_batch({"log_ids": [1, 2]}, db)
            assert exc_info.value.status_code == 400
            assert "需为同一素材" in exc_info.value.detail

    async def test_apply_analysis_to_material(self, client, monkeypatch):
        async def _not_found(_db, _log_id):
            raise ai_svc.AIAnalysisNotFoundError("日志不存在")

        monkeypatch.setattr(aa.ai_svc, "apply_analysis_to_material", _not_found)
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await aa.apply_analysis_log_to_material(1, db)
        assert exc_info.value.status_code == 404

        async def _bad(_db, _log_id):
            raise ValueError("该记录没有标签")

        monkeypatch.setattr(aa.ai_svc, "apply_analysis_to_material", _bad)
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await aa.apply_analysis_log_to_material(1, db)
        assert exc_info.value.status_code == 400

        async def _ok(_db, log_id):
            return {"applied": 3, "inspiration_id": "insp-1", "log_id": log_id}

        monkeypatch.setattr(aa.ai_svc, "apply_analysis_to_material", _ok)
        async with async_session() as db:
            out = await aa.apply_analysis_log_to_material(5, db)
        assert out["message"] == "已把 3 个标签应用到素材"
        assert out["applied"] == 3
        assert out["log_id"] == 5
