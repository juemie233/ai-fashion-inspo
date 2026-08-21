"""任务队列路由测试：列表/筛选/详情/取消。"""

from app.database import async_session
from app.models.task import PendingVectorBackfill, TaskQueue


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


async def test_cancel_pending_task_physically_deletes(client):
    """取消排队中的任务：pending 直接物理删除，记录从表与列表中消失。"""
    tid = await _add_task(status="pending")
    # 关联数据检查：pending_vector_backfills 与 task_queue 无外键依赖，不应被误删
    async with async_session() as db:
        backfill = PendingVectorBackfill(inspiration_id="insp-1")
        db.add(backfill)
        await db.commit()
        backfill_id = backfill.id

    r = client.post(f"/api/tasks/{tid}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is True
    assert body["message"] == "任务已删除"
    # 详情 404、列表不再包含
    assert client.get(f"/api/tasks/{tid}").status_code == 404
    task_ids = [t["id"] for t in client.get("/api/tasks").json()["items"]]
    assert tid not in task_ids
    # 关联待回填记录保留
    async with async_session() as db:
        assert await db.get(PendingVectorBackfill, backfill_id) is not None


async def test_cancel_non_pending_task_rejected(client):
    """取消非 pending 任务（success）→ 400，记录保留。"""
    tid = await _add_task(status="success")

    r = client.post(f"/api/tasks/{tid}/cancel")
    assert r.status_code == 400, r.text
    assert "仅等待中的任务" in r.json()["detail"]
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "success"


async def test_cancel_running_task_rejected(client):
    """取消执行中的普通任务 → 400（不硬打断、不删除）。"""
    tid = await _add_task(status="running")

    r = client.post(f"/api/tasks/{tid}/cancel")
    assert r.status_code == 400, r.text
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "running"


async def test_cancel_running_face_scan_still_cancelled(client):
    """运行中的人脸扫描任务仍走「标记 cancelled」（记录保留，既有能力不受影响）。"""
    tid = await _add_task(status="running", type_="face_scan")

    r = client.post(f"/api/tasks/{tid}/cancel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is False
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "cancelled"


async def test_cancel_again_after_delete_returns_404(client):
    """已删除的任务再次取消 → 404。"""
    tid = await _add_task(status="pending")
    assert client.post(f"/api/tasks/{tid}/cancel").status_code == 200
    assert client.post(f"/api/tasks/{tid}/cancel").status_code == 404


def test_cancel_missing_task_404(client):
    """取消不存在的任务 → 404。"""
    assert client.post("/api/tasks/99999/cancel").status_code == 404


async def test_delete_terminal_task_physically_deletes(client):
    """删除终态任务（cancelled/success/failed）：物理删除，记录从表与列表中消失。"""
    for status in ("cancelled", "success", "failed"):
        tid = await _add_task(status=status)

        r = client.delete(f"/api/tasks/{tid}")
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] is True
        assert client.get(f"/api/tasks/{tid}").status_code == 404


async def test_delete_pending_running_rejected(client):
    """删除 pending/running 任务 → 400，记录保留（避免与 worker 竞态）。"""
    for status in ("pending", "running"):
        tid = await _add_task(status=status)

        r = client.delete(f"/api/tasks/{tid}")
        assert r.status_code == 400, r.text
        assert "不能删除" in r.json()["detail"]
        assert client.get(f"/api/tasks/{tid}").status_code == 200
        assert client.get(f"/api/tasks/{tid}").json()["status"] == status


def test_delete_missing_task_404(client):
    """删除不存在的任务 → 404。"""
    assert client.delete("/api/tasks/99999").status_code == 404
