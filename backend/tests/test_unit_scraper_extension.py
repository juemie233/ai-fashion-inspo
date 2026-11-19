"""浏览器插件采集会话补充测试：创建会话、汇总完成、异常与分支口径。

对应 ``app/services/scraper/extension.py`` 的分支：
create_extension_task 的默认 platform/source_url 配置；complete 的 404/400、
发现-入库差额换算、全部失败时的 error 提示、负向计数兜底。
"""

import json

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.scraper import ScraperTask
from app.services.scraper import extension
from app.utils.time import utcnow


async def _add_task(db, platform="douyin", status="pending") -> ScraperTask:
    task = ScraperTask(platform=platform, status=status)
    db.add(task)
    await db.commit()
    return task


async def test_create_extension_task_records_config():
    """创建会话：platform 固定 browser_extension，config 记录来源 URL 与原始平台。"""
    async with async_session() as db:
        task = await extension.create_extension_task(
            db,
            {"platform": "xiaohongshu", "source_url": "https://www.xiaohongshu.com/explore/a"},
        )

    assert task.id is not None
    assert task.platform == "browser_extension"
    assert task.status == "running"
    assert task.started_at is not None
    config = json.loads(task.config)
    assert config == {
        "mode": "extension",
        "source_url": "https://www.xiaohongshu.com/explore/a",
        "origin_platform": "xiaohongshu",
    }


async def test_create_extension_task_default_origin_platform():
    """未传 platform：origin_platform 回退 browser_extension，source_url 可为 None。"""
    async with async_session() as db:
        task = await extension.create_extension_task(db, {})

    config = json.loads(task.config)
    assert config["origin_platform"] == "browser_extension"
    assert config["source_url"] is None


async def test_complete_unknown_task_404():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await extension.complete_extension_task(db, 99999, {"items_found": 1})
    assert exc.value.status_code == 404
    assert exc.value.detail == "任务不存在"


async def test_complete_non_extension_task_400():
    async with async_session() as db:
        task = await _add_task(db, platform="douyin")
        with pytest.raises(HTTPException) as exc:
            await extension.complete_extension_task(db, task.id, {"items_found": 1})
    assert exc.value.status_code == 400
    assert "浏览器插件任务" in exc.value.detail


async def test_complete_summarizes_counts_and_funnel():
    """正常完成：状态 completed、计数落库、漏斗里把发现-入库差额记为跳过。"""
    async with async_session() as db:
        task = await _add_task(db, platform="browser_extension", status="running")
        task_id = task.id

    async with async_session() as db:
        result = await extension.complete_extension_task(
            db,
            task_id,
            {"items_found": 5, "items_added": 2, "source_url": "https://x.com/p/1"},
        )
        row = await db.get(ScraperTask, task_id)

    assert result == {"message": "已记录插件采集", "task_id": task_id, "items_added": 2}
    assert row.status == "completed"
    assert row.finished_at is not None
    assert row.items_found == 5 and row.items_added == 2
    assert row.error is None
    diagnostics = json.loads(row.diagnostics)
    assert diagnostics["per_search"] == [
        {
            "keyword": "https://x.com/p/1",
            "sort_type": "extension",
            "batch_added": 2,
            "batch_skipped_existing": 3,
        }
    ]
    summary = diagnostics["summary"]
    assert summary["total_found"] == 5 and summary["total_added"] == 2
    assert summary["skipped_network_error"] == 3


async def test_complete_without_source_url_uses_placeholder_keyword():
    """未传 source_url：漏斗关键词用「插件采集」占位。"""
    async with async_session() as db:
        task = await _add_task(db, platform="browser_extension", status="running")
        task_id = task.id

    async with async_session() as db:
        await extension.complete_extension_task(db, task_id, {"items_found": 1, "items_added": 1})
        row = await db.get(ScraperTask, task_id)

    diagnostics = json.loads(row.diagnostics)
    assert diagnostics["per_search"][0]["keyword"] == "插件采集"


async def test_complete_all_failed_sets_error_hint():
    """发现>0 但入库=0：写 error 提示（可能内容重复或后端异常）。"""
    async with async_session() as db:
        task = await _add_task(db, platform="browser_extension", status="running")
        task_id = task.id

    async with async_session() as db:
        await extension.complete_extension_task(db, task_id, {"items_found": 3, "items_added": 0})
        row = await db.get(ScraperTask, task_id)

    assert row.error is not None and "全部图片上传失败" in row.error
    diagnostics = json.loads(row.diagnostics)
    assert diagnostics["summary"]["skipped_network_error"] == 3


async def test_complete_missing_counts_and_added_exceeding_found():
    """缺省计数按 0；入库>发现的异常输入：跳过数按 0 兜底，不出现负数。"""
    now = utcnow()
    async with async_session() as db:
        task = await _add_task(db, platform="browser_extension", status="running")
        task_id = task.id

    async with async_session() as db:
        await extension.complete_extension_task(db, task_id, {"items_added": 4})
        row = await db.get(ScraperTask, task_id)

    assert row.items_found == 0 and row.items_added == 4
    assert json.loads(row.diagnostics)["per_search"][0]["batch_skipped_existing"] == 0
    assert row.error is None  # found=0 不触发「全部失败」提示
    assert row.finished_at is not None and row.finished_at >= now
