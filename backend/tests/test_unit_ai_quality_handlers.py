"""质量审核路由（`routers/ai_quality.py`）的统计/导出/复核/纠正接口测试。

这些接口回答「审核质量怎么样、要不要复核、误判怎么反馈」，原覆盖约 53%。它们的
数字口径错了不会报错，只会让看板与导出**悄悄失真**：

- `quality-stats`：通过率只在已判定上算、疑似 AI 生成正交统计、均值只用**当前模型**
  的成功审核日志、被拒原因码分组；`include_trashed` 决定是否包含垃圾桶；
- `quality-stats-by-source`：按来源分组的通过率（pending 不进分母）；
- `quality-prompt-ab`：按 prompt 指纹分组比较通过率，历史无 hash 归 unknown 并标注；
- `quality-gray`：灰区 = 每条日志**最新一次**评审、置信度落在 [0.5, 阈值)；
- `quality-export.csv`：带 BOM、公式注入清洗、prompt 指纹只取前 8 位；
- 误判反馈：参数校验 400、素材不存在 404、纠正失败 400。
"""

import uuid

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.inspiration import AIAnalysisLog, AIQualityReview, Inspiration
from app.routers import ai_quality as q
from app.services import quality_correction
from app.utils.time import utcnow


async def _insp(**fields) -> str:
    inspiration_id = str(uuid.uuid4())
    payload = {
        "source_type": "manual_upload",
        "file_path": f"images/{inspiration_id}.jpg",
        "media_type": "image",
        "quality_status": "pending",
        "rating": 0,
    }
    payload.update(fields)
    async with async_session() as db:
        db.add(Inspiration(id=inspiration_id, **payload))
        await db.commit()
    return inspiration_id


async def _log(
    inspiration_id: str,
    *,
    error: str | None = None,
    log_type: str = "quality_check",
    processing_time_ms: int | None = None,
    model_version: str | None = None,
) -> int:
    async with async_session() as db:
        log = AIAnalysisLog(
            inspiration_id=inspiration_id,
            model_name="qwen3-vl:8b-instruct",
            log_type=log_type,
            raw_response="{}",
            error=error,
            processing_time_ms=processing_time_ms,
            model_version=model_version,
        )
        db.add(log)
        await db.commit()
        return log.id


async def _review(
    log_id: int,
    *,
    result: str = "approved",
    reason: str | None = None,
    prompt_hash: str | None = None,
    confidence: float | None = None,
) -> int:
    async with async_session() as db:
        review = AIQualityReview(
            log_id=log_id,
            result=result,
            reason=reason,
            prompt_hash=prompt_hash,
            model_name="qwen3-vl:8b-instruct",
            confidence=confidence,
        )
        db.add(review)
        await db.commit()
        return review.id


class TestManualUploadConfig:
    async def test_get_and_set(self, client, monkeypatch):
        monkeypatch.setattr(q.settings, "manual_upload_auto_approve", False)
        assert await q.get_manual_upload_auto_approve() == {"enabled": False}

        persisted: list[dict] = []

        async def _update(values):
            persisted.append(values)

        monkeypatch.setattr(q, "_update_env_file", _update)

        result = await q.set_manual_upload_auto_approve(enabled=True, persist=True)
        assert result["enabled"] is True
        assert "开启" in result["message"]
        assert persisted == [{"MANUAL_UPLOAD_AUTO_APPROVE": "true"}]

        await q.set_manual_upload_auto_approve(enabled=False, persist=False)
        assert q.settings.manual_upload_auto_approve is False
        assert len(persisted) == 1  # 不持久化时不写 .env


class TestQualityCheckAndRecheck:
    async def test_quality_check_submits_task_and_skips_deleted(self, client, monkeypatch):
        pending = await _insp(quality_status="pending")
        await _insp(quality_status="pending", deleted_at=utcnow())  # 垃圾桶不参与
        video = await _insp(media_type="video", file_path="videos/a.mp4")

        async def _running():
            return True

        created: dict = {}

        async def _create(_db, ids, random=False):
            created.update(ids=set(ids), random=random)
            return type("T", (), {"id": 9})()

        import app.services.task_runner as runner

        monkeypatch.setattr(q, "is_ollama_running", _running)
        monkeypatch.setattr(runner, "create_quality_check_task", _create)

        async with async_session() as db:
            out = await q.batch_quality_check(limit=10, random=True, db=db)

        assert out["task_id"] == 9
        assert out["count"] == 2
        assert created["ids"] == {pending, video}  # 视频也参与（取首帧）
        assert created["random"] is True

    async def test_quality_check_no_candidates(self, client, monkeypatch):
        async def _running():
            return True

        monkeypatch.setattr(q, "is_ollama_running", _running)
        async with async_session() as db:
            out = await q.batch_quality_check(limit=5, random=False, db=db)
        assert out == {"message": "没有可审核的素材", "count": 0}

    async def test_quality_check_starts_ollama_when_down(self, client, monkeypatch):
        started: list[bool] = []

        async def _down():
            return False

        async def _start():
            started.append(True)

        async def _create(_db, _ids, random=False):
            return type("T", (), {"id": 1})()

        import app.services.task_runner as runner

        monkeypatch.setattr(q, "is_ollama_running", _down)
        monkeypatch.setattr(q, "start_ollama", _start)
        monkeypatch.setattr(runner, "create_quality_check_task", _create)
        await _insp()

        async with async_session() as db:
            await q.batch_quality_check(limit=5, random=False, db=db)

        assert started == [True]

    async def test_recheck_resets_approved_and_resubmits(self, client, monkeypatch):
        approved = await _insp(quality_status="approved")
        pending = await _insp(quality_status="pending")

        async def _running():
            return True

        captured: dict = {}

        async def _create(_db, ids, random=False):
            captured.update(ids=set(ids))
            return type("T", (), {"id": 3})()

        import app.services.task_runner as runner

        monkeypatch.setattr(q, "is_ollama_running", _running)
        monkeypatch.setattr(runner, "create_quality_check_task", _create)

        async with async_session() as db:
            out = await q.recheck_quality(db=db)

        assert out["task_id"] == 3
        assert "已重置 1 个已通过素材" in out["message"]
        assert {approved, pending} == captured["ids"]  # 重置后两者都待审
        async with async_session() as db:
            row = await db.get(Inspiration, approved)
        assert row.quality_status == "pending"
        assert row.quality_reason is None

    async def test_recheck_without_approved(self, client, monkeypatch):
        async def _running():
            return True

        monkeypatch.setattr(q, "is_ollama_running", _running)
        async with async_session() as db:
            out = await q.recheck_quality(db=db)
        assert out == {"message": "没有已通过的素材可重新审核", "count": 0}


class TestQualityStats:
    async def test_stats_rates_reasons_and_ai_generated(self, client, monkeypatch):
        monkeypatch.setattr(q.settings, "ollama_vision_model", "qwen3-vl:8b-instruct")
        approved_id = await _insp(quality_status="approved")
        await _insp(quality_status="rejected", quality_reason_code="not_outfit")
        await _insp(quality_status="rejected", quality_reason_code=None)
        await _insp(quality_status="pending", is_ai_generated=True)
        await _insp(quality_status="rejected", deleted_at=utcnow())  # 默认不计

        good_log = await _log(
            approved_id, processing_time_ms=2000, model_version="qwen3-vl:8b-instruct"
        )
        await _review(good_log, result="approved")
        failed_log = await _log(approved_id, error="超时", processing_time_ms=9999)
        await _review(failed_log, result="pending")

        async with async_session() as db:
            out = await q.quality_stats(include_trashed=False, db=db)

        assert out["total"] == 4
        assert out["approved"] == 1
        assert out["rejected"] == 2
        assert out["pending"] == 1
        assert out["pass_rate"] == round(1 / 3 * 100, 1)
        assert out["ai_generated"] == 1
        # 只算成功的、当前模型的审核日志：2000ms → 2.0s
        assert out["avg_seconds"] == 2.0
        assert out["reason_counts"]["not_outfit"] == 1
        assert out["reason_counts"]["unknown"] == 1

        async with async_session() as db:
            with_trashed = await q.quality_stats(include_trashed=True, db=db)
        assert with_trashed["total"] == 5

    async def test_stats_by_source_pass_rate_excludes_pending(self, client):
        await _insp(source_type="xiaohongshu", quality_status="approved")
        await _insp(source_type="xiaohongshu", quality_status="rejected")
        await _insp(source_type="xiaohongshu", quality_status="pending")
        await _insp(source_type="manual_upload", quality_status=None)  # NULL 按 pending
        await _insp(source_type="xaiohongshu", quality_status="rejected", deleted_at=utcnow())

        async with async_session() as db:
            out = await q.quality_stats_by_source(db=db)

        rows = {row["source_type"]: row for row in out["items"]}
        xhs = rows["xiaohongshu"]
        assert xhs["total"] == 3
        assert xhs["pass_rate"] == 50.0  # 1/2，pending 不进分母
        manual = rows["manual_upload"]
        assert manual["pass_rate"] == 0  # 全 pending
        assert out["total"] == 4
        assert out["items"][0]["total"] >= out["items"][-1]["total"]  # 按总数倒序


class TestExportAndAB:
    async def test_export_csv_bom_sanitize_and_hash_prefix(self, client):
        insp = await _insp()
        log_id = await _log(insp)
        await _review(
            log_id,
            result="rejected",
            reason="=CMD|' /C calc'!A0",  # 公式注入尝试
            prompt_hash="abcdef1234567890",
        )

        async with async_session() as db:
            response = await q.export_quality_csv(db=db)

        assert response.media_type == "text/csv"
        assert "attachment" in response.headers["content-disposition"]
        text = bytes(response.body).decode("utf-8-sig")
        assert text.startswith("\ufeff") is False  # decode 已去 BOM
        header = text.splitlines()[0]
        assert "审核ID" in header and "prompt指纹" in header
        assert "'=CMD" in text or "'=CMD|" in text  # sanitize 前缀
        assert "abcdef12" in text  # 指纹只回前 8 位

    async def test_prompt_ab_groups_and_unknown(self, client):
        approved_log = await _log(await _insp())
        rejected_log = await _log(await _insp())
        unknown_log = await _log(await _insp())
        await _review(approved_log, result="approved", prompt_hash="a" * 64)
        await _review(rejected_log, result="rejected", prompt_hash="a" * 64)
        await _review(unknown_log, result="pending", prompt_hash=None)

        async with async_session() as db:
            out = await q.quality_prompt_ab(db=db)

        assert out["count"] == 2
        by_hash = {item["prompt_hash"]: item for item in out["items"]}
        known = by_hash["a" * 8]
        assert known["known"] is True
        assert known["total"] == 2
        assert known["pass_rate"] == 50.0
        unknown = by_hash["unknown"]
        assert unknown["known"] is False
        assert unknown["pending"] == 1

    async def test_gray_band_uses_latest_review_only(self, client, monkeypatch):
        monkeypatch.setattr(q.settings, "quality_classifier_threshold", 0.9)
        insp = await _insp()
        other = await _insp()
        deleted = await _insp(deleted_at=utcnow())

        old_log = await _log(insp)
        await _review(old_log, result="approved", confidence=0.99)  # 旧评审在灰区外
        latest_log = await _log(insp)
        await _review(latest_log, result="rejected", confidence=0.6)  # 最新一次在灰区

        other_log = await _log(other)
        await _review(other_log, result="approved", confidence=0.95)  # 高于阈值不算灰区

        deleted_log = await _log(deleted)
        await _review(deleted_log, result="approved", confidence=0.6)  # 垃圾桶排除

        async with async_session() as db:
            out = await q.quality_gray(limit=10, db=db)

        assert [item["id"] for item in out["items"]] == [insp]
        assert out["items"][0]["confidence"] == 0.6
        assert out["threshold"] == 0.9
        assert out["count"] == 1


class TestCorrections:
    async def test_create_validation_and_errors(self, client, monkeypatch):
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await q.create_quality_correction({"corrected_status": "approved"}, db)
        assert exc_info.value.status_code == 400

        async def _record(_db, *_args, **_kwargs):
            return {"ok": True}

        monkeypatch.setattr(quality_correction, "record_correction", _record)
        async with async_session() as db:
            out = await q.create_quality_correction(
                {"inspiration_id": "i1", "corrected_status": "approved"}, db
            )
        assert out == {"ok": True}

        async def _not_found(_db, *_args, **_kwargs):
            raise LookupError("素材不存在")

        monkeypatch.setattr(quality_correction, "record_correction", _not_found)
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await q.create_quality_correction(
                    {"inspiration_id": "i1", "corrected_status": "approved"}, db
                )
        assert exc_info.value.status_code == 404

        async def _bad(_db, *_args, **_kwargs):
            raise ValueError("状态不合法")

        monkeypatch.setattr(quality_correction, "record_correction", _bad)
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await q.create_quality_correction(
                    {"inspiration_id": "i1", "corrected_status": "x"}, db
                )
        assert exc_info.value.status_code == 400

    async def test_list_and_stats_delegate(self, client, monkeypatch):
        async def _list(_db, page, size):
            return [{"id": 1}], 1

        async def _stats(_db):
            return {"total": 1, "by_reason": {}}

        monkeypatch.setattr(quality_correction, "list_corrections", _list)
        monkeypatch.setattr(quality_correction, "correction_stats", _stats)

        async with async_session() as db:
            assert await q.list_quality_corrections(1, 50, db) == {
                "items": [{"id": 1}],
                "total": 1,
                "page": 1,
                "size": 50,
            }
            assert await q.quality_correction_stats(db) == {"total": 1, "by_reason": {}}
