"""采集结果服务补充测试：统计聚合、结果分页、批量软删除的文件/墓碑/审计分支。

对应 ``app/services/scraper/results.py`` 中既有 test_scraper_results_api.py /
test_scraper.py 未覆盖的分支：get_scraper_stats 的空窗口与按平台/按日聚合、
get_task_results 的 404/分页/垃圾桶排除/时间格式、batch_delete 的空入参、
已删除跳过、文件移动失败降级、墓碑写入、任务计数同步与审计记录。
"""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.models.audit import AuditLog
from app.models.inspiration import Inspiration
from app.models.scraper import ScraperSeenURL, ScraperTask
from app.database import async_session
from app.services.scraper import results
from app.utils.time import format_utc, utcnow


async def _add_task(db, **overrides) -> ScraperTask:
    data = {"platform": "douyin", "status": "completed"}
    data.update(overrides)
    task = ScraperTask(**data)
    db.add(task)
    await db.commit()
    return task


async def _add_insp(db, **overrides) -> Inspiration:
    data = {"file_path": "images/x.jpg", "media_type": "image"}
    data.update(overrides)
    insp = Inspiration(**data)
    db.add(insp)
    await db.commit()
    return insp


# ═══════════════════════════════════════════════════════════════
#  统计看板
# ═══════════════════════════════════════════════════════════════


async def test_stats_empty_window_returns_zero_rates():
    """窗口内没有任务：成功率为 0、各分布为空（不是 None / 500）。"""
    stats = await results.get_scraper_stats(days=30)

    assert stats["total_tasks"] == 0
    assert stats["success_rate"] == 0
    assert stats["total_found"] == 0 and stats["total_added"] == 0
    assert stats["by_platform"] == [] and stats["by_day"] == []


async def test_stats_aggregates_platform_and_days():
    """按平台/按日聚合：完成任务、失败任务、窗口外任务的口径。"""
    async with async_session() as db:
        now = utcnow()
        await _add_task(
            db,
            platform="douyin",
            status="completed",
            items_found=10,
            items_added=4,
            created_at=now,
        )
        await _add_task(
            db,
            platform="xiaohongshu",
            status="failed",
            items_found=5,
            items_added=0,
            created_at=now,
        )
        # 窗口外任务不应计入任何聚合
        await _add_task(
            db,
            platform="douyin",
            status="completed",
            items_found=99,
            items_added=99,
            created_at=now - timedelta(days=40),
        )

    stats = await results.get_scraper_stats(days=30)

    assert stats["days"] == 30
    assert stats["total_tasks"] == 2
    assert stats["completed"] == 1 and stats["failed"] == 1
    assert stats["success_rate"] == 50.0
    assert stats["total_found"] == 15 and stats["total_added"] == 4
    by_platform = {row["platform"]: row for row in stats["by_platform"]}
    assert by_platform["douyin"] == {
        "platform": "douyin",
        "tasks": 1,
        "found": 10,
        "added": 4,
        "completed": 1,
    }
    assert by_platform["xiaohongshu"]["completed"] == 0
    assert sum(row["tasks"] for row in stats["by_day"]) == 2
    day = stats["by_day"][0]
    assert day["date"] and day["failed"] == 1


# ═══════════════════════════════════════════════════════════════
#  任务结果列表
# ═══════════════════════════════════════════════════════════════


async def test_task_results_unknown_task_404():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await results.get_task_results(db, 99999)
    assert exc.value.status_code == 404
    assert exc.value.detail == "采集任务未找到"


async def test_task_results_pagination_and_trash_exclusion():
    """分页切片、按创建时间倒序、垃圾桶素材不出现、时间带 Z 后缀。"""
    now = utcnow()
    async with async_session() as db:
        task = await _add_task(db, status="running", started_at=now)
        other = await _add_task(db, platform="xiaohongshu")
        first = await _add_insp(
            db, scraper_task_id=task.id, created_at=now - timedelta(minutes=3)
        )
        second = await _add_insp(
            db, scraper_task_id=task.id, created_at=now - timedelta(minutes=2)
        )
        trashed = await _add_insp(
            db,
            scraper_task_id=task.id,
            created_at=now - timedelta(minutes=1),
            deleted_at=now,
        )
        await _add_insp(db, scraper_task_id=other.id, created_at=now)

        page1 = await results.get_task_results(db, task.id, page=1, size=1)
        page2 = await results.get_task_results(db, task.id, page=2, size=1)

    assert page1["total"] == 2
    assert page1["task"]["id"] == task.id
    assert page1["task"]["started_at"] == format_utc(now)
    assert page1["task"]["finished_at"] is None
    assert page1["items"][0]["id"] == second.id  # 新的在前
    assert page1["items"][0]["created_at"].endswith("Z")
    assert page2["items"][0]["id"] == first.id
    assert trashed.id not in {page1["items"][0]["id"], page2["items"][0]["id"]}


# ═══════════════════════════════════════════════════════════════
#  批量移入垃圾桶
# ═══════════════════════════════════════════════════════════════


async def test_batch_delete_empty_ids_400():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await results.batch_delete_task_results(db, 1, [])
    assert exc.value.status_code == 400
    assert "素材 ID 列表" in exc.value.detail


async def test_batch_delete_moves_files_and_syncs_counters(tmp_path):
    """真实文件路径：文件移入 trash、墓碑写入、任务 items_added 同步、审计留痕。"""
    img_rel = "images/_.jpg"
    thumb_rel = "thumbnails/_.jpg"
    async with async_session() as db:
        task = await _add_task(db, items_added=2)
        insp = await _add_insp(
            db,
            scraper_task_id=task.id,
            file_path=img_rel,
            thumbnail_path=thumb_rel,
            source_url="https://example.com/img/1.jpg",
            quality_status="rejected",
        )
        # 造出真实文件（临时 storage 根）
        (settings.storage_root / img_rel).parent.mkdir(parents=True, exist_ok=True)
        (settings.storage_root / img_rel).write_bytes(b"img")
        (settings.storage_root / thumb_rel).parent.mkdir(parents=True, exist_ok=True)
        (settings.storage_root / thumb_rel).write_bytes(b"thumb")
        insp_id = insp.id
        task_id = task.id

    async with async_session() as db:
        result = await results.batch_delete_task_results(db, task_id, [insp_id])

        moved = await db.get(Inspiration, insp_id)
        assert result == {"trashed_count": 1, "skipped": 0, "remaining": 0}
        assert moved.deleted_at is not None
        assert moved.trash_source == "auto"
        assert moved.trash_reason == "质量差"  # 未传原因 + 审核拒绝 → 推断
        assert moved.file_path.startswith("trash/")
        assert moved.thumbnail_path.startswith("trash/")
        assert (settings.storage_root / moved.file_path).exists()
        # 墓碑：删除后采集器不再重复采集该 URL
        tomb = await db.get(ScraperSeenURL, "https://example.com/img/1.jpg")
        assert tomb is not None
        # 任务计数同步为剩余未删除数
        refreshed = await db.get(ScraperTask, task_id)
        assert refreshed.items_added == 0

    async with async_session() as audit_db:
        logs = (
            await audit_db.execute(select(AuditLog).where(AuditLog.action == "batch_trash"))
        ).scalars().all()
    assert len(logs) == 1 and logs[0].count == 1


async def test_batch_delete_skips_already_trashed_and_infers_reason():
    """已在垃圾桶的素材计入 skipped；未传原因且未审核拒绝 → 不喜欢。"""
    now = utcnow()
    async with async_session() as db:
        task = await _add_task(db, items_added=2)
        kept = await _add_insp(db, scraper_task_id=task.id, quality_status="pending")
        already = await _add_insp(db, scraper_task_id=task.id, deleted_at=now)
        kept_id, already_id, task_id = kept.id, already.id, task.id

    async with async_session() as db:
        result = await results.batch_delete_task_results(
            db, task_id, [kept_id, already_id], reason=None
        )
        kept_row = await db.get(Inspiration, kept_id)

    assert result == {"trashed_count": 1, "skipped": 1, "remaining": 0}
    assert kept_row.trash_reason == "不喜欢"
    assert kept_row.trash_source == "auto"


async def test_batch_delete_file_move_failure_degrades(monkeypatch):
    """文件移动抛 OSError：软删除照样提交（恢复时按 DB 路径自愈），仅记日志。"""
    async with async_session() as db:
        task = await _add_task(db)
        insp = await _add_insp(db, scraper_task_id=task.id)
        insp_id, task_id = insp.id, task.id

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(results, "move_to_trash", _boom)

    async with async_session() as db:
        result = await results.batch_delete_task_results(db, task_id, [insp_id])
        row = await db.get(Inspiration, insp_id)

    assert result["trashed_count"] == 1
    assert row.deleted_at is not None
    assert row.file_path == "images/x.jpg"  # 路径未改（移动失败不改库）


async def test_batch_delete_missing_file_returns_none(monkeypatch):
    """move_to_trash 返回 None（文件不存在）：不写回路径、不触发路径提交。"""
    calls: list[tuple] = []
    monkeypatch.setattr(
        results, "move_to_trash", lambda src, iid, suffix="": calls.append((src, suffix)) or None
    )
    async with async_session() as db:
        task = await _add_task(db)
        insp = await _add_insp(
            db, scraper_task_id=task.id, thumbnail_path="thumbnails/y.jpg"
        )
        insp_id, task_id = insp.id, task.id

    async with async_session() as db:
        await results.batch_delete_task_results(db, task_id, [insp_id])
        row = await db.get(Inspiration, insp_id)

    assert row.file_path == "images/x.jpg" and row.thumbnail_path == "thumbnails/y.jpg"
    assert [c[1] for c in calls] == ["", "_thumb"]
