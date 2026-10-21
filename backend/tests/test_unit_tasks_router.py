"""任务中心的状态机测试（取消 / 暂停 / 恢复 / 删除）。

**为什么直接调 handler 而不是用 HTTP 客户端**：本项目的 TestClient 请求跑在
anyio 的 portal 线程里，那条线程不被 coverage 追踪——用 HTTP 测这些端点，
覆盖率只停留在「模块级 def 行被执行」的程度（实测 33%），看起来像完全没测。
直接 `await cancel_task(task_id, db=db)` 才是真实覆盖，断言也更精确。

这些接口改的是任务状态，出错代价很高：
- 误删正在跑的任务 → worker 半途丢任务，产物残缺；
- 取消了不能取消的类型 → 用户以为停了、实际还在烧 GPU；
- 暂停后 worker 又认领 → 暂停形同虚设（worker 只认领 pending）。
"""

from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.task import TaskQueue
from app.routers import tasks as tasks_router
from app.services.task_runners.common import utcnow


async def _task(
    task_type: str,
    status: str,
    progress: int = 0,
    total: int = 4,
    heartbeat_at=None,
) -> int:
    async with async_session() as db:
        task = TaskQueue(
            type=task_type,
            status=status,
            progress=progress,
            total=total,
            heartbeat_at=heartbeat_at,
        )
        db.add(task)
        await db.commit()
        return task.id


async def _status(task_id: int) -> str | None:
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        return task.status if task else None


# ── 查询 ──


async def test_list_tasks_paginates_and_counts():
    for _ in range(3):
        await _task("batch_analyze", "success")

    async with async_session() as db:
        # 直接调用要把 Query 默认值也显式传进来（Query 对象不会被 FastAPI 解析）
        page = await tasks_router.list_tasks(page=1, size=2, status=None, type=None, db=db)

    assert page["size"] == 2
    assert len(page["items"]) == 2
    assert page["total"] >= 3


async def test_list_tasks_filters_by_status_and_type():
    await _task("quality_check", "failed")

    async with async_session() as db:
        # 参数名是 type（与模型字段一致），不是 task_type
        page = await tasks_router.list_tasks(
            page=1, size=20, status="failed", type="quality_check", db=db
        )

    assert page["total"] >= 1
    assert all(t.status == "failed" and t.type == "quality_check" for t in page["items"])


async def test_get_task_and_404():
    task_id = await _task("batch_analyze", "pending")

    async with async_session() as db:
        task = await tasks_router.get_task(task_id, db=db)
        assert task.id == task_id

        with pytest.raises(HTTPException) as exc:
            await tasks_router.get_task(999999, db=db)
        assert exc.value.status_code == 404


# ── 取消 ──


async def test_cancel_pending_deletes_record():
    """等待中的任务取消 = 物理删除（需求：取消后不留历史）。"""
    task_id = await _task("batch_analyze", "pending")

    async with async_session() as db:
        result = await tasks_router.cancel_task(task_id, db=db)

    assert result["deleted"] is True
    assert await _status(task_id) is None


async def test_cancel_running_whitelisted_marks_cancelled():
    """运行中且在白名单内：置 cancelled 并写原因（执行器据此自我停止）。"""
    task_id = await _task("face_scan", "running")

    async with async_session() as db:
        result = await tasks_router.cancel_task(task_id, db=db)

    assert result["message"] == "任务已取消"
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
    assert task.status == "cancelled"
    assert task.error == "用户手动取消"


async def test_cancel_running_not_whitelisted_returns_400():
    """不在白名单的运行中任务不能取消：400 且状态不变。"""
    task_id = await _task("batch_delete", "running")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await tasks_router.cancel_task(task_id, db=db)

    assert exc.value.status_code == 400
    assert "仅等待中的任务" in str(exc.value.detail)
    assert await _status(task_id) == "running"


async def test_cancel_paused_cancellable_goes_terminal():
    """暂停中的任务取消 → 直接终态（不必先恢复再取消）。"""
    task_id = await _task("quality_check", "paused")

    async with async_session() as db:
        await tasks_router.cancel_task(task_id, db=db)

    assert await _status(task_id) == "cancelled"


@pytest.mark.parametrize("status", ["success", "failed", "cancelled"])
async def test_cancel_terminal_states_return_400(status):
    task_id = await _task("batch_analyze", status)

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await tasks_router.cancel_task(task_id, db=db)

    assert exc.value.status_code == 400
    assert await _status(task_id) == status


async def test_cancel_missing_task_returns_404():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await tasks_router.cancel_task(999999, db=db)
    assert exc.value.status_code == 404


# ── 暂停 / 恢复 ──


async def test_pause_running_pausable_type():
    task_id = await _task("vector_backfill", "running", progress=100, total=400)

    async with async_session() as db:
        await tasks_router.pause_task(task_id, db=db)

    assert await _status(task_id) == "paused"


async def test_pause_rejects_non_pausable_type():
    task_id = await _task("face_scan", "running")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await tasks_router.pause_task(task_id, db=db)

    assert exc.value.status_code == 400
    assert await _status(task_id) == "running"


async def test_pause_rejects_pending_task():
    """pending 还没开始跑，没有可暂停的东西。"""
    task_id = await _task("batch_analyze", "pending")

    async with async_session() as db:
        with pytest.raises(HTTPException):
            await tasks_router.pause_task(task_id, db=db)


async def test_pause_missing_task_returns_404():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await tasks_router.pause_task(999999, db=db)
    assert exc.value.status_code == 404


async def test_resume_paused_task_returns_to_pending():
    """恢复必须回到 pending：worker 只认领 pending，置 running 会永远没人跑。"""
    task_id = await _task("batch_analyze", "paused", progress=1)

    async with async_session() as db:
        await tasks_router.resume_task(task_id, db=db)

    assert await _status(task_id) == "pending"


async def test_resume_rejects_running_task():
    task_id = await _task("batch_analyze", "running")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await tasks_router.resume_task(task_id, db=db)

    assert exc.value.status_code == 400


async def test_resume_missing_task_returns_404():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await tasks_router.resume_task(999999, db=db)
    assert exc.value.status_code == 404


# ── 删除 ──


async def test_delete_pending_rejected():
    """pending 只能用取消（取消对 pending 本身就是物理删除）。"""
    task_id = await _task("batch_analyze", "pending")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await tasks_router.delete_task(task_id, db=db)

    assert exc.value.status_code == 400
    assert "请使用取消操作" in str(exc.value.detail)
    assert await _status(task_id) == "pending"


async def test_delete_running_with_fresh_heartbeat_rejected():
    """worker 还在跑（心跳新鲜）时不能删，否则产物残缺。"""
    task_id = await _task("batch_analyze", "running", heartbeat_at=utcnow())

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await tasks_router.delete_task(task_id, db=db)

    assert exc.value.status_code == 400
    assert "正在执行中" in str(exc.value.detail)
    assert await _status(task_id) == "running"


async def test_delete_zombie_running_allowed():
    """心跳超时（worker 已死）的僵尸任务可删——这正是该分支存在的意义。"""
    stale = utcnow() - timedelta(hours=2)
    task_id = await _task("batch_analyze", "running", heartbeat_at=stale)

    async with async_session() as db:
        result = await tasks_router.delete_task(task_id, db=db)

    assert result["deleted"] is True
    assert await _status(task_id) is None


async def test_delete_paused_and_terminal_allowed():
    """暂停（无心跳、不会被认领）与终态任务都可清理。"""
    paused = await _task("batch_analyze", "paused")
    done = await _task("batch_analyze", "success")

    async with async_session() as db:
        await tasks_router.delete_task(paused, db=db)
        await tasks_router.delete_task(done, db=db)

    assert await _status(paused) is None
    assert await _status(done) is None


async def test_delete_missing_task_returns_404():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await tasks_router.delete_task(999999, db=db)
    assert exc.value.status_code == 404
