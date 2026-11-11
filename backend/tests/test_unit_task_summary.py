"""任务列表 result 摘要（2026-11-03 评审 P1-2/P2-6）。

历史问题：列表取 ``substr(result,1,8192)``，超长 JSON 前缀解析失败 → 147 条任务
标题/摘要整条为空。现在写入时生成 ``result_summary``（事件监听器），列表只读摘要；
详情接口保留完整 result。
"""

from __future__ import annotations

import json

from sqlalchemy import update

from app.database import async_session
from app.main import _backfill_task_result_summaries
from app.models.task import TaskQueue
from app.utils.task_summary import SUMMARY_LIMIT, summarize_result


def _huge_result() -> dict:
    return {
        "done": 12,
        "total": 12,
        "text_done": 9,
        "fetch_mode": "collection",
        "plan": {"files": 20, "skipped": {"已存在": 3}},
        "import": {"imported": 7},
        "details": [
            {"index": idx, "text": "很长的明细" * 200, "scores": [0.1] * 100}
            for idx in range(50)
        ],
    }


def test_小结果原样保留():
    result = {"done": 3, "total": 3}
    assert summarize_result(result) == result


def test_大结果摘要可解析且优先字段保留():
    summary = summarize_result(_huge_result())
    assert summary is not None
    text = json.dumps(summary, ensure_ascii=False)
    assert len(text) <= SUMMARY_LIMIT
    # 标题/摘要要用的顶层字段必须还在；明细被截断
    assert summary["done"] == 12
    assert summary["fetch_mode"] == "collection"
    assert summary["import"]["imported"] == 7
    assert len(summary["details"]) <= 10


async def test_列表返回大结果摘要_详情保留完整结果(client):
    result = _huge_result()
    async with async_session() as db:
        task = TaskQueue(
            type="f2_import",
            status="success",
            progress=100,
            total=12,
            done=12,
            result=result,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id
        # ORM 事件应已写入摘要
        assert task.result_summary is not None

    listed = client.get("/api/tasks", params={"size": 200, "type": "f2_import"}).json()
    item = next(row for row in listed["items"] if row["id"] == task_id)
    assert item["result"] is not None
    assert item["result"]["done"] == 12
    assert item["result"]["fetch_mode"] == "collection"

    detail = client.get(f"/api/tasks/{task_id}").json()
    # 详情也默认摘要（大结果不随轮询返回），完整结果走显式 /result
    assert detail["result"]["done"] == 12
    assert len(json.dumps(detail["result"], ensure_ascii=False)) <= SUMMARY_LIMIT

    full = client.get(f"/api/tasks/{task_id}/result").json()
    assert len(json.dumps(full["result"], ensure_ascii=False)) > SUMMARY_LIMIT
    assert full["result"]["done"] == 12


async def test_超期任务完整result被清理_摘要保留():
    """P1-2：超过保留天数的终态任务清空 result，但列表摘要必须保留。"""
    from datetime import timedelta

    from app.services.task_retention import prune_task_results
    from app.utils.time import utcnow

    async with async_session() as db:
        old = TaskQueue(
            type="tag_health_scan", status="success", progress=100, result={"done": 1}
        )
        fresh = TaskQueue(
            type="tag_health_scan", status="success", progress=100, result={"done": 2}
        )
        db.add_all([old, fresh])
        await db.commit()
        await db.refresh(old)
        await db.refresh(fresh)
        await db.execute(
            update(TaskQueue)
            .where(TaskQueue.id == old.id)
            .values(updated_at=utcnow() - timedelta(days=30))
        )
        await db.commit()
        old_id, fresh_id = old.id, fresh.id

    async with async_session() as db:
        preview = await prune_task_results(db, days=7, dry_run=True)
        assert preview["scanned"] == 1
        # dry-run 不写库
        assert (await db.get(TaskQueue, old_id)).result is not None

        applied = await prune_task_results(db, days=7)
        assert applied["pruned"] == 1

    async with async_session() as db:
        old_row = await db.get(TaskQueue, old_id)
        fresh_row = await db.get(TaskQueue, fresh_id)
        assert old_row.result is None
        assert old_row.result_summary == {"done": 1}
        assert fresh_row.result == {"done": 2}


async def test_历史行摘要为空时启动回填(client):
    async with async_session() as db:
        task = TaskQueue(
            type="batch_analyze",
            status="success",
            progress=100,
            total=1,
            done=1,
            result={"done": 1, "total": 1},
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id
        # 模拟迁移前的历史行：result 有值、summary 为空
        await db.execute(
            update(TaskQueue).where(TaskQueue.id == task_id).values(result_summary=None)
        )
        await db.commit()
        await db.refresh(task)
        assert task.result_summary is None

    filled = await _backfill_task_result_summaries()
    assert filled >= 1

    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert task is not None
        assert task.result_summary == {"done": 1, "total": 1}
