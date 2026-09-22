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


async def test_list_tasks_paused_first(client):
    """已暂停的任务排最前（哪怕它比别的任务更早创建），同组内仍按最新在前。"""
    paused_old = await _add_task(status="paused", type_="batch_analyze")
    running_new = await _add_task(status="running", type_="batch_analyze")
    success_newest = await _add_task(status="success", type_="deduplicate")

    data = client.get("/api/tasks").json()
    assert data["total"] == 3
    assert [t["id"] for t in data["items"]] == [paused_old, success_newest, running_new]
    assert data["items"][0]["status"] == "paused"

    # 分页第二页不该重复出现暂停任务（排序发生在 SQL 里，不是前端重排）
    second = client.get("/api/tasks", params={"page": 2, "size": 1}).json()
    assert second["items"][0]["id"] == success_newest


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
    """取消执行中的普通任务 → 400（不硬打断、不删除）。

    用 deduplicate（不在运行中可取消白名单内）验证拒绝分支——批量/组合分析
    已纳入白名单，见下方两个用例。
    """
    tid = await _add_task(status="running", type_="deduplicate")

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


async def test_cancel_running_batch_analyze_cancelled(client):
    """运行中的批量分析可取消 → 标记 cancelled（记录保留，执行器下个批次边界停止）。

    全库级批量分析动辄跑数十小时，没有取消入口时用户只能干等；已产出的
    分析日志与标签保留，重新「分析未分析」即可幂等续算。
    """
    for task_type in ("batch_analyze", "multi_analyze"):
        tid = await _add_task(status="running", type_=task_type)

        r = client.post(f"/api/tasks/{tid}/cancel")
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] is False
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


async def test_delete_paused_task_physically_deletes(client):
    """删除已暂停的任务：物理删除（暂停任务不会被 worker 认领、无心跳、不再执行）。"""
    tid = await _add_task(status="paused")

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


async def test_pause_quality_check_running(client):
    """运行中的质量审核任务可暂停（与标签分析同一套「批次边界停」语义）。"""
    tid = await _add_task(status="running", type_="quality_check")

    r = client.post(f"/api/tasks/{tid}/pause")
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "任务已暂停"
    async with async_session() as db:
        row = await db.get(TaskQueue, tid)
        assert row.status == "paused"
        assert row.paused_at is not None


async def test_resume_quality_check_back_to_pending(client):
    """恢复质量审核：放回 pending 由 worker 重新认领（执行器只查剩下的 pending 素材）。"""
    tid = await _add_task(status="paused", type_="quality_check")

    r = client.post(f"/api/tasks/{tid}/resume")
    assert r.status_code == 200, r.text
    async with async_session() as db:
        row = await db.get(TaskQueue, tid)
        assert row.status == "pending"
        assert row.claimed_by is None
        assert row.paused_at is None


async def test_pause_non_pausable_type_rejected(client):
    """暂停非可暂停类型（face_scan）→ 400，记录保持 running。"""
    tid = await _add_task(status="running", type_="face_scan")

    r = client.post(f"/api/tasks/{tid}/pause")
    assert r.status_code == 400, r.text
    assert "可暂停" in r.json()["detail"]
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "running"


def test_whitelists_match_frontend_contract():
    """锁定暂停/取消白名单内容：改这里必须同步前端（前端用例同样锁定这份清单）。

    跨语言无法共用常量，靠两侧用例把「人工对齐」变成会红的事实——否则会出现
    「界面有按钮、后端 400」或反过来的静默错位。
    """
    from app.routers.tasks import (
        _CANCELABLE_RUNNING_TYPES,
        _PAUSABLE_RUNNING_TYPES,
    )

    assert set(_PAUSABLE_RUNNING_TYPES) == {
        "tag_network_analyze",
        "batch_analyze",
        "multi_analyze",
        "quality_check",
        "f2_import",
    }
    assert set(_CANCELABLE_RUNNING_TYPES) == {
        "face_scan",
        "face_match",
        "tag_network_analyze",
        "f2_import",
        "batch_analyze",
        "multi_analyze",
        "quality_check",
    }


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
    async def _fake_load(db, inspiration_ids, skip_analyzed=True, exclude_inflight=None):
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


async def test_execute_batch_analyze_returns_when_status_changed_to_pending(client, monkeypatch):
    """暂停后快速恢复的竞态窗口（pause→resume 后状态为 pending）：执行器批边界
    发现状态不再是 running 也必须提前返回，不能继续跑完全部——否则出现「任务在跑
    但状态是 pending、UI 无暂停按钮、pause 接口又拒绝」的卡死表现。"""
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
    async def _fake_load(db, inspiration_ids, skip_analyzed=True, exclude_inflight=None):
        return [("insp-1", ["f1.jpg"]), ("insp-2", ["f2.jpg"])], 0, 0
    monkeypatch.setattr(runner, "_load_pending_items", _fake_load)

    # 首个素材分析期间把任务状态改为 pending（模拟 pause 后 worker 尚未感知
    # paused 时用户就点了恢复 → resume 接口把状态放回 pending）
    flipped = False

    async def _fake_analyze_one(sem, inspiration_id, frames):
        nonlocal flipped
        if not flipped:
            flipped = True
            async with async_session() as s:
                await s.execute(
                    update(TaskQueue).where(TaskQueue.id == tid).values(
                        status="pending", claimed_by=None
                    )
                )
                await s.commit()
        return inspiration_id, True, None

    monkeypatch.setattr(runner, "_analyze_one", _fake_analyze_one)

    async with async_session() as db:
        task = await db.get(TaskQueue, tid)
        await runner.execute_batch_analyze(db, task)

        # 提前返回：状态保持 pending（由 worker 重新认领续算）、进度停留在
        # 第一批次边界（50）、未覆盖 success 完成态、result 未被改写
        assert task.status == "pending"
        assert task.done == 1
        assert task.progress == 50
        assert task.result == {"inspiration_ids": ["insp-1", "insp-2"]}

    # 数据库侧同样保持 pending 与断点进度
    async with async_session() as db:
        row = await db.get(TaskQueue, tid)
        assert row.status == "pending"
        assert row.done == 1
        assert row.progress == 50


async def test_load_pending_items_defers_video_keyframes(client, upload, monkeypatch):
    """加载阶段不为视频抽帧（懒抽帧）：帧列表返回 None，ffmpeg 留到分析批次再跑。

    回归背景：原先加载阶段对全部视频逐个抽关键帧，实测 1753 个视频要一小时上下，
    这段时间进度恒为 0%、一条分析日志都不写、取消与暂停都无从生效——用户看到的
    就是「跑了十几分钟没有任何产出且停不掉」。
    """
    from app.models.inspiration import Inspiration
    from app.services.task_runners import batch_analyze as runner

    img_id = upload().json()["id"]
    vid_id = upload().json()["id"]
    async with async_session() as db:
        await db.execute(
            update(Inspiration).where(Inspiration.id == vid_id).values(media_type="video")
        )
        await db.commit()

    extracted: list[str] = []

    async def _spy(db_, iid, fp, mt):
        extracted.append(iid)
        return ["keyframes/x/frame_001.jpg"]

    monkeypatch.setattr(runner, "_resolve_analysis_frames", _spy)

    async with async_session() as db:
        items, already, unavailable = await runner._load_pending_items(db, [img_id, vid_id])

    assert extracted == []  # 加载阶段一次抽帧都不做
    assert already == 0
    assert unavailable == 0
    frames_by_id = dict(items)
    assert len(frames_by_id[img_id] or []) == 1  # 图片：分析源就是原图
    assert frames_by_id[vid_id] is None  # 视频：登记为待抽帧


async def test_execute_batch_analyze_resolves_video_frames_lazily(client, monkeypatch):
    """执行器在视频进入分析批次时才抽帧，并把抽到的帧交给分析函数。"""
    from app.services.task_runners import batch_analyze as runner

    async with async_session() as db:
        task = TaskQueue(
            type="batch_analyze", status="running", progress=0, total=1, done=0,
            result={"inspiration_ids": ["vid-1"]}, max_retries=2,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        tid = task.id

    monkeypatch.setattr(runner, "_analyze_concurrency", lambda: 1)

    async def _fake_load(db, inspiration_ids, skip_analyzed=True, exclude_inflight=None):
        return [("vid-1", None)], 0, 0  # 视频：加载阶段帧列表为 None

    monkeypatch.setattr(runner, "_load_pending_items", _fake_load)

    async def _fake_resolve(db, iid):
        assert iid == "vid-1"
        return ["keyframes/vid-1/frame_001.jpg"]

    monkeypatch.setattr(runner, "_resolve_frames_for", _fake_resolve)

    seen: list[list[str]] = []

    async def _fake_analyze_one(sem, iid, frames):
        seen.append(frames)
        return iid, True, None

    monkeypatch.setattr(runner, "_analyze_one", _fake_analyze_one)

    async with async_session() as db:
        task = await db.get(TaskQueue, tid)
        await runner.execute_batch_analyze(db, task)
        assert task.progress == 100
        assert task.result["success_count"] == 1
        assert task.result["unavailable"] == 0

    assert seen == [["keyframes/vid-1/frame_001.jpg"]]


async def test_execute_batch_analyze_counts_unavailable_video(client, monkeypatch):
    """抽帧失败的视频计入 unavailable 并跳过：不算分析失败，也不误报「全部失败」。"""
    from app.services.task_runners import batch_analyze as runner

    async with async_session() as db:
        task = TaskQueue(
            type="batch_analyze", status="running", progress=0, total=1, done=0,
            result={"inspiration_ids": ["vid-bad"]}, max_retries=2,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        tid = task.id

    monkeypatch.setattr(runner, "_analyze_concurrency", lambda: 1)

    async def _fake_load(db, inspiration_ids, skip_analyzed=True, exclude_inflight=None):
        return [("vid-bad", None)], 0, 0

    monkeypatch.setattr(runner, "_load_pending_items", _fake_load)

    async def _fake_resolve(db, iid):
        return []  # ffmpeg 无帧产出

    monkeypatch.setattr(runner, "_resolve_frames_for", _fake_resolve)

    async def _fake_analyze_one(sem, iid, frames):  # pragma: no cover - 不应被调用
        raise AssertionError("分析源不可用的素材不应进入分析")

    monkeypatch.setattr(runner, "_analyze_one", _fake_analyze_one)

    async with async_session() as db:
        task = await db.get(TaskQueue, tid)
        await runner.execute_batch_analyze(db, task)
        # 全部素材都不可用时不再抛「全部失败」（failed_items 为空，此前会越界）
        assert task.progress == 100
        assert task.result["unavailable"] == 1
        assert task.result["success_count"] == 0
        assert task.result["failed_count"] == 0
