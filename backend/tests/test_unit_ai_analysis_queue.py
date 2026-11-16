"""AI 分析历史/结果对比/应用标签接口测试（`routers/ai_analysis.py`）。

2026-11-16（task-9）起单图分析改为 worker 任务队列，API 进程内存分析队列及其
`queue/pending`、`queue/pause|resume`、`queue/{id}`、`active-analyses` 接口已删除；
本文件保留其余接口测试：

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
from app.services import ai_analysis_service as ai_svc


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
