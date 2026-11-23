"""出图批次取消与历史导出的直接调用测试。

这两块行为由异步路由函数承载：走 TestClient 时函数体在 portal 线程执行，不进入
pytest-cov 统计，历史用例里的路径长期显示「未覆盖」。这里直接 await 路由函数，
既验证行为（取消只影响活跃任务、导出 JSON 不带 BOM），也让默认门禁的覆盖率真实
反映这两条链路。
"""

import json

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.generation import GenerationRun
from app.models.task import TaskQueue
from app.routers.generate import cancel_run, export_runs


async def _add(row):
    async with async_session() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def _run(status: str = "running", task_id: int | None = None) -> tuple[int, int | None]:
    run = GenerationRun(prompt="测试 prompt", status=status, task_id=task_id)
    run_id = await _add(run)
    return run_id, task_id


async def _task(status: str) -> int:
    return await _add(TaskQueue(type="generate", status=status))


async def _reload_task(task_id: int) -> str:
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert task is not None
        return task.status


async def _cancel(run_id: int) -> dict:
    async with async_session() as db:
        return await cancel_run(run_id=run_id, db=db)


async def test_cancel_unknown_run_returns_404(client):
    with pytest.raises(HTTPException) as exc:
        await _cancel(999999)
    assert exc.value.status_code == 404


async def test_cancel_run_without_task_still_marks_run_cancelled(client):
    run_id, _ = await _run(status="queued")
    body = await _cancel(run_id)

    assert body == {"run_id": run_id, "status": "cancelled"}
    async with async_session() as db:
        run = await db.get(GenerationRun, run_id)
        assert run is not None
        assert run.status == "cancelled"


@pytest.mark.parametrize("task_status", ["pending", "running", "paused"])
async def test_cancel_run_cancels_active_task(client, task_status):
    task_id = await _task(task_status)
    run_id, _ = await _run(status="running", task_id=task_id)

    await _cancel(run_id)

    assert await _reload_task(task_id) == "cancelled"


@pytest.mark.parametrize("task_status", ["success", "failed", "cancelled"])
async def test_cancel_run_does_not_touch_finished_task(client, task_status):
    task_id = await _task(task_status)
    run_id, _ = await _run(status="done", task_id=task_id)

    await _cancel(run_id)

    # 已结束的任务保持原状态（取消只对活跃任务生效）
    assert await _reload_task(task_id) == task_status


async def test_cancel_run_tolerates_missing_task_row(client):
    """任务记录被清理后 task_id 悬空：不能 500，只取消批次本身。"""
    run_id, _ = await _run(status="running", task_id=999999)

    body = await _cancel(run_id)

    assert body["status"] == "cancelled"


async def test_export_runs_json_has_no_bom_and_valid_payload(client):
    await _run(status="done")

    async with async_session() as db:
        response = await export_runs(
            fmt="json",
            kind=None,
            model_key=None,
            status=None,
            has_imported=None,
            favorite=None,
            q=None,
            reference_id=None,
            since=None,
            until=None,
            db=db,
        )

    assert response.media_type == "application/json; charset=utf-8"
    assert "attachment" in response.headers["content-disposition"]
    text = response.body.decode("utf-8")
    assert not text.startswith("\ufeff")  # JSON 不带 BOM
    payload = json.loads(text)
    assert payload and payload[0]["prompt"] == "测试 prompt"


async def test_export_runs_csv_has_bom_for_excel(client):
    await _run(status="done")

    async with async_session() as db:
        response = await export_runs(
            fmt="csv",
            kind=None,
            model_key=None,
            status=None,
            has_imported=None,
            favorite=None,
            q=None,
            reference_id=None,
            since=None,
            until=None,
            db=db,
        )

    assert response.media_type == "text/csv; charset=utf-8"
    body = response.body
    assert body.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    assert "prompt" in body.decode("utf-8-sig").lower()
