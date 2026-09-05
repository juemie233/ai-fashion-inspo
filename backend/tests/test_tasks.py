"""任务队列路由测试：列表/筛选/详情/取消/删除。"""

from datetime import datetime, timedelta

from sqlalchemy import update

from app.database import async_session
from app.models.task import PendingVectorBackfill, TaskQueue
from app.utils.time import utcnow


async def _add_task(
    status: str = "pending",
    type_: str = "batch_analyze",
    heartbeat_at: datetime | None = None,
) -> int:
    """直接插入一条任务记录，返回任务 ID。"""
    async with async_session() as db:
        task = TaskQueue(
            type=type_, status=status, progress=0, total=1, done=0,
            result={}, max_retries=2, heartbeat_at=heartbeat_at,
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
    """删除 pending 任务 → 400，记录保留（待执行任务请走取消接口移除）。"""
    tid = await _add_task(status="pending")

    r = client.delete(f"/api/tasks/{tid}")
    assert r.status_code == 400, r.text
    assert "不能删除" in r.json()["detail"]
    assert client.get(f"/api/tasks/{tid}").status_code == 200
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "pending"


async def test_delete_live_running_rejected(client):
    """心跳新鲜（worker 正在执行）的 running 任务 → 400，记录保留。"""
    tid = await _add_task(status="running", heartbeat_at=utcnow())

    r = client.delete(f"/api/tasks/{tid}")
    assert r.status_code == 400, r.text
    assert "不能删除" in r.json()["detail"]
    assert client.get(f"/api/tasks/{tid}").status_code == 200


async def test_delete_zombie_running_task_allowed(client):
    """僵尸 running（心跳缺失 / 心跳超时，如停电、进程崩溃遗留）可删除。"""
    # 心跳缺失：视为僵尸（与 worker 僵尸重置判定一致）
    tid = await _add_task(status="running", heartbeat_at=None)
    r = client.delete(f"/api/tasks/{tid}")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert client.get(f"/api/tasks/{tid}").status_code == 404

    # 心跳过期：超过 90s 阈值（如停电后任务卡在 running 一直无人刷新）
    stale = utcnow() - timedelta(seconds=120)
    tid = await _add_task(status="running", heartbeat_at=stale)
    r = client.delete(f"/api/tasks/{tid}")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert client.get(f"/api/tasks/{tid}").status_code == 404


def test_delete_missing_task_404(client):
    """删除不存在的任务 → 404。"""
    assert client.delete("/api/tasks/99999").status_code == 404


# ============ 暂停 / 恢复（batch_analyze / multi_analyze / tag_network_analyze） ============


async def test_pause_batch_analyze_running(client):
    """运行中的批量分析任务可暂停（AI 标签分析核心批量路径）。"""
    tid = await _add_task(status="running", type_="batch_analyze")

    r = client.post(f"/api/tasks/{tid}/pause")
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "任务已暂停"
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "paused"
    async with async_session() as db:
        row = await db.get(TaskQueue, tid)
        assert row.status == "paused"
        assert row.paused_at is not None


async def test_pause_multi_analyze_running(client):
    """运行中的组合分析任务可暂停。"""
    tid = await _add_task(status="running", type_="multi_analyze")

    r = client.post(f"/api/tasks/{tid}/pause")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "paused"


async def test_pause_non_pausable_type_rejected(client):
    """暂停非可暂停类型（quality_check）→ 400，记录保持 running。"""
    tid = await _add_task(status="running", type_="quality_check")

    r = client.post(f"/api/tasks/{tid}/pause")
    assert r.status_code == 400, r.text
    assert "可暂停" in r.json()["detail"]
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "running"


async def test_pause_non_running_rejected(client):
    """暂停非 running 状态（pending）→ 400。"""
    tid = await _add_task(status="pending", type_="batch_analyze")

    r = client.post(f"/api/tasks/{tid}/pause")
    assert r.status_code == 400, r.text
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "pending"


async def test_resume_batch_analyze_requeues_pending(client):
    """恢复批量分析：放回 pending 并清空认领信息，供 worker 重新认领续算。"""
    tid = await _add_task(status="paused", type_="batch_analyze")

    r = client.post(f"/api/tasks/{tid}/resume")
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "任务已恢复"
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "pending"
    # 认领/心跳/暂停标记已在数据库侧清空（API 响应模型不暴露这些字段）
    async with async_session() as db:
        row = await db.get(TaskQueue, tid)
        assert row.status == "pending"
        assert row.claimed_by is None
        assert row.paused_at is None
        assert row.heartbeat_at is None


async def test_resume_multi_analyze_requeues_pending(client):
    """恢复组合分析：同样放回 pending。"""
    tid = await _add_task(status="paused", type_="multi_analyze")

    r = client.post(f"/api/tasks/{tid}/resume")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "pending"


async def test_resume_tag_network_keeps_running(client):
    """恢复标签网络分析：维持既有语义（恢复为 running 断点续算），不受批量改动影响。"""
    tid = await _add_task(status="paused", type_="tag_network_analyze")

    r = client.post(f"/api/tasks/{tid}/resume")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "running"


async def test_resume_non_paused_rejected(client):
    """恢复非 paused 状态（running）→ 400。"""
    tid = await _add_task(status="running", type_="batch_analyze")

    r = client.post(f"/api/tasks/{tid}/resume")
    assert r.status_code == 400, r.text
    assert "可恢复" in r.json()["detail"]


async def test_pause_resume_cycle(client):
    """批量分析任务暂停 → 恢复一轮完整流转。"""
    tid = await _add_task(status="running", type_="batch_analyze")

    assert client.post(f"/api/tasks/{tid}/pause").status_code == 200
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "paused"
    assert client.post(f"/api/tasks/{tid}/resume").status_code == 200
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "pending"


async def test_execute_batch_analyze_paused_early_return(client, monkeypatch):
    """执行器每批边界感知 paused 后保存进度提前返回（不覆盖为 success）。"""
    from app.services.task_runners import batch_analyze as runner

    # 建 running 的批量分析任务（2 个素材，并发 1 → 两个批次）
    async with async_session() as db:
        task = TaskQueue(
            type="batch_analyze", status="running", progress=0, total=2, done=0,
            result={"inspiration_ids": ["insp-1", "insp-2"]}, max_retries=2,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        tid = task.id

    # 固定并发为 1；跳过真实素材加载
    monkeypatch.setattr(runner, "_analyze_concurrency", lambda: 1)
    async def _fake_load(db, inspiration_ids, skip_analyzed=True):
        return [("insp-1", ["f1.jpg"]), ("insp-2", ["f2.jpg"])], 0, 0
    monkeypatch.setattr(runner, "_load_pending_items", _fake_load)

    # 首个素材分析期间把任务标记为 paused（模拟用户点了暂停）
    flipped = False

    async def _fake_analyze_one(sem, inspiration_id, frames):
        nonlocal flipped
        if not flipped:
            flipped = True
            async with async_session() as s:
                await s.execute(
                    update(TaskQueue).where(TaskQueue.id == tid).values(status="paused")
                )
                await s.commit()
        return inspiration_id, True, None

    monkeypatch.setattr(runner, "_analyze_one", _fake_analyze_one)

    async with async_session() as db:
        task = await db.get(TaskQueue, tid)
        await runner.execute_batch_analyze(db, task)

        # 提前返回：状态保持 paused、进度停留在第一批次边界（50）、
        # 未覆盖 success 完成态
        assert task.status == "paused"
        assert task.done == 1
        assert task.progress == 50
        assert task.result == {"inspiration_ids": ["insp-1", "insp-2"]}

    # 数据库侧同样保持 paused 与断点进度
    async with async_session() as db:
        row = await db.get(TaskQueue, tid)
        assert row.status == "paused"
        assert row.done == 1
        assert row.progress == 50
