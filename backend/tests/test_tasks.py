"""任务队列路由测试：列表/筛选/详情/取消。"""

from app.database import async_session
from app.models.task import TaskQueue


async def _add_task(status: str = "pending", type_: str = "batch_analyze") -> int:
    """直接插入一条任务记录，返回任务 ID。"""
    async with async_session() as db:
        task = TaskQueue(
            type=type_, status=status, progress=0, total=1, done=0,
            result={}, max_retries=2,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task.id


async def test_list_tasks(client):
    """任务列表：返回全部任务。"""
    await _add_task(status="pending", type_="batch_analyze")
    await _add_task(status="success", type_="deduplicate")

    data = client.get("/api/tasks").json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


async def test_list_tasks_filter_by_status(client):
    """按状态筛选任务。"""
    await _add_task(status="pending", type_="batch_analyze")
    await _add_task(status="success", type_="deduplicate")

    data = client.get("/api/tasks", params={"status": "pending"}).json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "pending"


def test_get_task_not_found(client):
    """查询不存在的任务 → 404。"""
    assert client.get("/api/tasks/99999").status_code == 404


async def test_cancel_pending_task(client):
    """取消排队中的任务：状态置为 cancelled。"""
    tid = await _add_task(status="pending")

    r = client.post(f"/api/tasks/{tid}/cancel")
    assert r.status_code == 200
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "cancelled"


async def test_cancel_running_task_conflict(client):
    """取消执行中的任务 → 409（不硬打断）。"""
    tid = await _add_task(status="running")

    r = client.post(f"/api/tasks/{tid}/cancel")
    assert r.status_code == 409
