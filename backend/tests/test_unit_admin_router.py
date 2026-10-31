"""管理后台（`routers/admin.py`）破坏性/批量接口的单测。

**直接调用 handler**（TestClient 的请求在 anyio portal 线程里跑，pytest-cov 追踪不到）。

这组接口是全项目**唯一能物理删数据的地方**，而项目标准要求「破坏性接口必须显式测」。
覆盖的重点不是「能删」，而是三条安全语义：

1. **孤立文件清理的保护期**：上传是「先落盘 → 再入库」，清理孤立文件如果不等保护期，
   会把正在入库、DB 里还没有记录的图删掉（真实丢图事故）。用例锁住「10 分钟内的
   新文件必须跳过」。
2. **批量删除的条件口径**：`untagged` 只取未删除的无标签素材；`analysis_failed` 必须按
   **最新一条**分析日志判断（历史失败过但最新已成功的不能被误删）。
3. **AI 标记翻案的保护**：`source_type='generated'`（本地出图）是确定的 AI 生成，
   不允许被批量翻案成非 AI——否则「这张是生成的」这个事实被抹掉。

另外覆盖：导出 CSV 的 BOM 与文件名、向量回填的三种分支（lancedb 不可用 / 无素材 /
有素材重建）。
"""

import time

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.audit import AuditLog
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.models.tag import InspirationTag, Tag
from app.routers import admin as admin_router
from app.schemas.admin import VectorBackfillRequest


async def _insp(
    insp_id: str,
    *,
    deleted: bool = False,
    source_type: str = "manual_upload",
    media_type: str = "image",
    file_path: str | None = None,
    thumb: str | None = None,
    is_ai: bool = False,
) -> str:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type=source_type,
                file_path=file_path or f"images/2026-10/{insp_id}.jpg",
                thumbnail_path=thumb,
                media_type=media_type,
                is_ai_generated=is_ai,
                deleted_at=admin_utcnow() if deleted else None,
                trash_reason="质量差" if deleted else None,
                trash_source="manual" if deleted else None,
            )
        )
        await db.commit()
    return insp_id


def admin_utcnow():
    from app.utils.time import utcnow

    return utcnow()


async def _audit_count() -> int:
    from sqlalchemy import func, select

    async with async_session() as db:
        return (
            await db.execute(select(func.count()).select_from(AuditLog))
        ).scalar() or 0


# ── 孤立文件清理 ──


async def test_cleanup_orphans_only_deletes_old_unreferenced_files(client, monkeypatch):
    """只删「磁盘有、DB 无」且**超过保护期**的文件；新文件与已登记文件都保留。"""
    from app.config import settings

    root = settings.storage_root
    images = settings.images_dir
    images.mkdir(parents=True, exist_ok=True)

    old_orphan = images / "old_orphan.jpg"
    new_orphan = images / "new_orphan.jpg"
    tracked = images / "tracked.jpg"
    for p in (old_orphan, new_orphan, tracked):
        p.write_bytes(b"x" * 100)

    # 保护期外的旧孤儿：把 mtime 改到 20 分钟前
    past = time.time() - 20 * 60
    import os

    os.utime(old_orphan, (past, past))

    await _insp("cleanup-tracked", file_path="images/old_orphan_tracked.jpg")
    tracked2 = images / "old_orphan_tracked.jpg"
    tracked2.write_bytes(b"y" * 50)  # 已登记的文件不能被删

    out = await admin_router.cleanup_orphan_files()

    assert out["deleted_count"] == 1  # 只有保护期外的那一个
    assert out["freed_bytes"] == 100
    assert not old_orphan.exists()
    assert new_orphan.exists()  # 保护期内的新文件保留（可能仍在入库流程中）
    assert tracked2.exists()  # DB 有记录的不能删
    assert await _audit_count() >= 1  # 破坏性操作留痕
    assert root.exists()


async def test_cleanup_orphans_reports_failed_files(client, monkeypatch):
    """删除失败不能静默吞掉：要记进 failed_files，且不计入 freed_bytes。"""
    from app.config import settings

    images = settings.images_dir
    images.mkdir(parents=True, exist_ok=True)
    doomed = images / "undeletable.jpg"
    doomed.write_bytes(b"z" * 10)
    import os

    past = time.time() - 20 * 60
    os.utime(doomed, (past, past))

    real_unlink = type(doomed).unlink

    def _boom(self, *args, **kwargs):
        raise PermissionError("文件被占用")

    monkeypatch.setattr(type(doomed), "unlink", _boom, raising=True)
    try:
        out = await admin_router.cleanup_orphan_files()
    finally:
        monkeypatch.setattr(type(doomed), "unlink", real_unlink, raising=True)

    assert out["deleted_count"] == 0
    assert out["freed_bytes"] == 0
    assert out["failed_files"] == ["images/undeletable.jpg"]
    doomed.unlink(missing_ok=True)


# ── 批量删除 ──


async def test_batch_delete_requires_ids_or_condition(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await admin_router.batch_delete({}, db)
    assert exc.value.status_code == 400
    assert "ids" in str(exc.value.detail)


async def test_batch_delete_by_condition_untagged(client):
    """无标签素材按条件删除：有标签的、已进垃圾桶的都不能被选中。"""
    await _insp("tagged-1")
    await _insp("untagged-1")
    await _insp("trashed-1", deleted=True)

    async with async_session() as db:
        tag = Tag(name="针织开衫", category="单品", source="manual")
        db.add(tag)
        await db.flush()
        db.add(
            InspirationTag(
                inspiration_id="tagged-1", tag_id=tag.id, confidence=1.0, source="manual"
            )
        )
        await db.commit()

    async with async_session() as db:
        out = await admin_router.batch_delete({"condition": "untagged"}, db)

    assert out["task_id"]
    assert "1 个素材" in out["message"]  # 只有 untagged-1
    assert await _audit_count() >= 1


async def test_batch_delete_by_condition_returns_zero_when_nothing_matches(client):
    await _insp("tagged-no-delete")

    async with async_session() as db:
        tag = Tag(name="极简", category="风格", source="manual")
        db.add(tag)
        await db.flush()
        db.add(
            InspirationTag(
                inspiration_id="tagged-no-delete",
                tag_id=tag.id,
                confidence=1.0,
                source="manual",
            )
        )
        await db.commit()

    async with async_session() as db:
        out = await admin_router.batch_delete({"condition": "untagged"}, db)

    assert out == {"deleted_count": 0, "freed_bytes": 0}


async def test_batch_delete_analysis_failed_uses_latest_log(client):
    """按「分析失败」删除：只看**最新一条**日志——历史失败但最新成功的不能误删。"""
    await _insp("fail-latest")
    await _insp("recovered")
    await _insp("trashed-fail", deleted=True)

    async with async_session() as db:
        db.add(
            AIAnalysisLog(
                inspiration_id="fail-latest",
                model_name="m1",
                log_type="analysis",
                error="超时",
            )
        )
        # 先失败、后成功（最新一条无 error）→ 不应被删
        db.add(
            AIAnalysisLog(
                inspiration_id="recovered",
                model_name="m1",
                log_type="analysis",
                error="超时",
            )
        )
        await db.commit()
        db.add(
            AIAnalysisLog(
                inspiration_id="recovered",
                model_name="m1",
                log_type="analysis",
                error=None,
            )
        )
        db.add(
            AIAnalysisLog(
                inspiration_id="trashed-fail",
                model_name="m1",
                log_type="analysis",
                error="超时",
            )
        )
        await db.commit()

    async with async_session() as db:
        out = await admin_router.batch_delete({"condition": "analysis_failed"}, db)

    assert "1 个素材" in out["message"]  # 只有 fail-latest


async def test_batch_delete_by_ids(client):
    await _insp("by-id-1")
    await _insp("by-id-2")

    async with async_session() as db:
        out = await admin_router.batch_delete({"ids": ["by-id-1", "by-id-2"]}, db)

    assert out["task_id"]
    assert "2 个素材" in out["message"]


# ── 批量翻案 AI 标记 ──


async def test_batch_unmark_ai_requires_ids(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await admin_router.batch_unmark_ai({"ids": []}, db)
    assert exc.value.status_code == 400


async def test_batch_unmark_ai_skips_generated_material(client):
    """本地出图（generated）是确定的 AI 生成，不允许被翻案成非 AI。"""
    await _insp("suspect-1", is_ai=True, source_type="scraper")
    await _insp("generated-1", is_ai=True, source_type="generated")

    async with async_session() as db:
        out = await admin_router.batch_unmark_ai(
            {"ids": ["suspect-1", "generated-1"]}, db
        )

    assert out == {"updated": 1, "skipped_generated": 1}
    async with async_session() as db:
        assert (await db.get(Inspiration, "suspect-1")).is_ai_generated is False
        assert (await db.get(Inspiration, "generated-1")).is_ai_generated is True


# ── 去重（提交任务） ──


async def test_deduplicate_creates_task(client):
    async with async_session() as db:
        out = await admin_router.deduplicate_files(db)
    assert out["task_id"]
    assert "去重任务" in out["message"]


# ── 导出 CSV ──


async def test_export_csv_has_bom_and_filename(client):
    await _insp("export-1", source_type="douyin")
    await _insp("export-trashed", deleted=True)

    async with async_session() as db:
        resp = await admin_router.export_inspirations(db)

    body = resp.body.decode("utf-8")
    assert body.startswith("\ufeff")  # BOM：Excel 打开中文不乱码
    assert "export-1" in body
    assert "export-trashed" not in body  # 垃圾桶不参与导出
    assert resp.headers["content-disposition"].endswith('.csv"')


# ── 向量回填 ──


async def test_vector_backfill_400_when_lancedb_missing(client, monkeypatch):
    from app.services.vector import store as vector_store

    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: False)

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await admin_router.vector_backfill(None, db)

    assert exc.value.status_code == 400
    assert "lancedb" in str(exc.value.detail)


async def test_vector_backfill_rebuild_text_without_material(client, monkeypatch):
    from app.services.vector import store as vector_store

    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)

    async with async_session() as db:
        out = await admin_router.vector_backfill(VectorBackfillRequest(rebuild_text=True), db)

    assert out["task_id"] is None
    assert out["count"] == 0
    assert "没有可重建" in out["message"]


async def test_vector_backfill_rebuild_text_creates_task(client, monkeypatch):
    from app.services.task_runners import vector_backfill as vb
    from app.services.vector import store as vector_store

    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)
    await _insp("vec-1")

    created: list[dict] = []

    class _Task:
        id = 42
        total = 1

    async def _create(db, ids, mode="image"):
        created.append({"ids": list(ids), "mode": mode})
        return _Task()

    monkeypatch.setattr(vb, "create_vector_backfill_task", _create)

    async with async_session() as db:
        out = await admin_router.vector_backfill(VectorBackfillRequest(rebuild_text=True), db)

    assert created[0]["mode"] == "text"
    assert out == {
        "message": "已创建文本向量重建任务 #42，共 1 个素材",
        "task_id": 42,
        "count": 1,
    }


async def test_vector_backfill_default_flushes_with_missing_ids(client, monkeypatch):
    """默认分支：把「缺图像向量」的素材一起塞进 flush（不丢）。"""
    from app.services.task_runners import vector_backfill as vb
    from app.services.vector import store as vector_store

    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)

    async def _no_vectors(kind):
        return []

    monkeypatch.setattr(vector_store, "list_vector_ids", _no_vectors)
    await _insp("no-vector-1")

    seen: dict = {}

    class _Task:
        id = 7
        total = 1

    async def _flush(db, force=False, extra_ids=None):
        seen["force"] = force
        seen["extra"] = list(extra_ids or [])
        return _Task()

    monkeypatch.setattr(vb, "flush_pending_vector_backfills", _flush)

    async with async_session() as db:
        out = await admin_router.vector_backfill(None, db)

    assert seen["force"] is True
    assert seen["extra"] == ["no-vector-1"]  # 缺向量的素材被带上
    assert out["count"] == 1


async def test_vector_backfill_default_returns_zero_when_all_vectorized(client, monkeypatch):
    from app.services.task_runners import vector_backfill as vb
    from app.services.vector import store as vector_store

    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)

    async def _all_vectorized(kind):
        return ["has-vector-1"]

    monkeypatch.setattr(vector_store, "list_vector_ids", _all_vectorized)
    await _insp("has-vector-1")

    async def _flush(db, force=False, extra_ids=None):
        return None

    monkeypatch.setattr(vb, "flush_pending_vector_backfills", _flush)

    async with async_session() as db:
        out = await admin_router.vector_backfill(None, db)

    assert out["count"] == 0
    assert "全部已入库" in out["message"]


async def test_get_missing_image_vector_ids(client, monkeypatch):
    """缺失图像向量 = 有效图片素材 − 已有向量；视频不算（视频不做图像向量）。"""
    from app.services.vector import store as vector_store

    await _insp("img-a")
    await _insp("img-b")
    await _insp("vid-a", media_type="video")
    await _insp("trashed-a", deleted=True)
    async def _existing(kind):
        return ["img-a"]

    monkeypatch.setattr(vector_store, "list_vector_ids", _existing)

    async with async_session() as db:
        missing = await admin_router._get_missing_image_vector_ids(db)

    assert missing == ["img-b"]


# ── 轮询/校验类接口的转发与 400 语义 ──


async def test_phash_backfill_400_when_cache_complete(client, monkeypatch):
    """缓存已完整 → 400（前端据此提示「无需补齐」，而不是起一个空任务）。"""
    import app.services.task_runner as task_runner

    async def _created(db, force=False):
        return {"task_id": None, "total": 0, "reused": False}

    monkeypatch.setattr(task_runner, "create_phash_backfill_task", _created)

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await admin_router.phash_backfill(False, db)

    assert exc.value.status_code == 400
    assert "已完整" in str(exc.value.detail)


async def test_phash_backfill_message_differs_for_reused_task(client, monkeypatch):
    """复用既有任务与新建任务的提示文案要能区分（用户需要知道有没有真的排队）。"""
    import app.services.task_runner as task_runner

    async def _reused(db, force=False):
        return {"task_id": 9, "total": 120, "reused": True}

    monkeypatch.setattr(task_runner, "create_phash_backfill_task", _reused)
    async with async_session() as db:
        out = await admin_router.phash_backfill(True, db)
    assert out["task_id"] == 9
    assert "在跑" in out["message"]  # 复用既有任务：文案要说明没重复排队

    async def _fresh(db, force=False):
        return {"task_id": 10, "total": 5, "reused": False}

    monkeypatch.setattr(task_runner, "create_phash_backfill_task", _fresh)
    async with async_session() as db:
        out2 = await admin_router.phash_backfill(False, db)
    assert "已创建" in out2["message"]


async def test_crop_scan_maps_value_error_to_400(client, monkeypatch):
    """剪裁扫描的参数错误（ValueError）→ 400 而不是 500。"""
    import app.services.crop_service as crop_service

    async def _boom(db, **kwargs):
        raise ValueError("crop_top 必须小于 crop_bottom")

    monkeypatch.setattr(crop_service, "scan_candidates", _boom)

    from app.schemas.admin import PhoneCropRequest

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await admin_router.crop_phone_screenshots_scan(PhoneCropRequest(), db)

    assert exc.value.status_code == 400
    assert "crop_top" in str(exc.value.detail)


async def test_crop_apply_requires_selection(client):
    from app.schemas.admin import PhoneCropApplyRequest

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await admin_router.crop_phone_screenshots_apply(PhoneCropApplyRequest(ids=[]), db)
    assert exc.value.status_code == 400
    assert "至少勾选" in str(exc.value.detail)


async def test_crop_apply_delegates_and_maps_value_error(client, monkeypatch):
    import app.services.crop_service as crop_service

    captured: dict = {}

    async def _apply(db, ids, mode, crop_top, crop_bottom):
        captured.update({"ids": ids, "mode": mode})
        return {"cropped": len(ids)}

    monkeypatch.setattr(crop_service, "apply_crops", _apply)

    from app.schemas.admin import PhoneCropApplyRequest

    async with async_session() as db:
        out = await admin_router.crop_phone_screenshots_apply(
            PhoneCropApplyRequest(ids=["a", "b"]), db
        )
    assert out == {"cropped": 2}
    assert captured["ids"] == ["a", "b"]

    async def _boom(db, **kwargs):
        raise ValueError("素材不是竖屏截图")

    monkeypatch.setattr(crop_service, "apply_crops", _boom)
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await admin_router.crop_phone_screenshots_apply(
                PhoneCropApplyRequest(ids=["a"]), db
            )
    assert exc.value.status_code == 400


async def test_near_duplicates_delegates(client, monkeypatch):
    import app.services.near_duplicate_service as nds

    async def _scan(db, limit=None, threshold=None):
        return {"groups": [], "scanned": 5, "cache_complete": True}

    monkeypatch.setattr(nds, "scan_near_duplicates", _scan)

    from app.schemas.admin import NearDuplicateScanRequest

    async with async_session() as db:
        out = await admin_router.near_duplicates(NearDuplicateScanRequest(), db)
    assert out["cache_complete"] is True


# ── 只读统计类接口：转发契约（字段名变了前端会静默显示 0） ──


@pytest.mark.parametrize(
    "handler,service_attr,payload",
    [
        ("admin_stats", "collect_stats", {"total_inspirations": 3}),
        ("largest_files", "largest_files", [{"path": "a.jpg", "size": 1}]),
        ("integrity_check", "integrity_check", {"missing_files": []}),
        ("find_duplicates", "find_duplicates", {"duplicate_groups": []}),
        ("vector_stats", "vector_stats", {"image_total": 0}),
        ("inspiration_trend", "query_inspiration_trend", {"points": []}),
        ("person_frequency", "person_frequency", [{"name": "某人", "count": 1}]),
    ],
)
async def test_readonly_stats_delegate(client, monkeypatch, handler, service_attr, payload):
    """只读统计接口的返回值必须原样透出（前端字段名依赖它）。"""
    import app.services.admin_stats_service as stats

    async def _fake(db, *args, **kwargs):
        return payload

    monkeypatch.setattr(stats, service_attr, _fake)

    fn = getattr(admin_router, handler)
    async with async_session() as db:
        if handler == "largest_files":
            out = await fn(20, db)
        elif handler in ("inspiration_trend",):
            out = await fn(30, db)
        elif handler == "person_frequency":
            out = await fn(20, db)
        else:
            out = await fn(db)
    assert out == payload


async def test_audit_logs_delegates(client, monkeypatch):
    import app.services.audit_service as audit

    async def _query(db, limit):
        return [{"action": "batch_delete", "count": 2}]

    monkeypatch.setattr(audit, "query_audit_logs", _query)

    async with async_session() as db:
        out = await admin_router.audit_logs(50, db)
    assert out[0]["action"] == "batch_delete"


async def test_check_duplicate_delegates(client, monkeypatch):
    import app.services.inspiration_service as svc

    async def _check(db, digest):
        return {"exists": True, "inspiration_id": "hit-1"}

    monkeypatch.setattr(svc, "check_duplicate_hash", _check)

    async with async_session() as db:
        out = await admin_router.check_duplicate("a" * 64, db)
    assert out["inspiration_id"] == "hit-1"


async def test_backup_status_delegates(client, monkeypatch):
    """备份状态卡片：只读透出 backup_service 的结果（不触发备份）。"""
    import app.services.backup_service as backup_service

    payload = {"enabled": True, "last_success": "2026-10-31 09:00"}

    monkeypatch.setattr(backup_service, "build_backup_status", lambda: payload)

    assert await admin_router.backup_status() == payload


async def test_get_missing_image_vector_ids_empty_library(client, monkeypatch):
    """空库直接返回空列表（不查向量库，省一次 IO）。"""
    from app.services.vector import store as vector_store

    called = {"n": 0}

    async def _list(kind):
        called["n"] += 1
        return set()

    monkeypatch.setattr(vector_store, "list_vector_ids", _list)

    async with async_session() as db:
        assert await admin_router._get_missing_image_vector_ids(db) == []
    assert called["n"] == 0
