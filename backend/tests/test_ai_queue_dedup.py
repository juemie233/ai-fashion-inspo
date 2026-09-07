"""AI 分析队列跨队列去重测试。

覆盖缺陷：内存分析队列（_active_analyses/_pending_queue）与 worker 数据库
任务队列（batch_analyze/multi_analyze）分别去重时互不可见，导致同一素材
可能被两个队列各分析一次。本组测试验证修复后的去重行为：

- get_inflight_analysis_ids：收集未完成 batch/multi 任务认领的素材 ID
- _inflight_excluding：排除「其它」未完成任务认领的素材（并发批处理防重复）
- get_unanalyzed_ids / batch_analyze 端点：进行中素材不再被纳入分析任务
"""

from app.database import async_session
from app.models.task import TaskQueue
from app.services.ai_analysis_service import (
    get_inflight_analysis_ids,
    get_unanalyzed_ids,
)
from app.services.task_runners.batch_analyze import _inflight_excluding


async def _add_task(ids: list[str], status: str = "running", type_: str = "batch_analyze") -> int:
    """创建一条 batch/multi 任务记录（result 携带 inspiration_ids），返回任务 ID。"""
    async with async_session() as db:
        task = TaskQueue(
            type=type_,
            status=status,
            progress=0,
            total=len(ids),
            done=0,
            result={"inspiration_ids": ids, "retry": status == "running"},
            max_retries=2,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task.id


async def test_inflight_ids_collects_pending_running_only(client):
    """get_inflight_analysis_ids：只收集 pending/running 的 batch/multi 任务认领素材，
    排除终态任务与其它任务类型。"""
    await _add_task(["insp-a", "insp-b"], status="running", type_="batch_analyze")
    await _add_task(["insp-c"], status="pending", type_="multi_analyze")
    # 终态/其它类型不收集
    await _add_task(["insp-d"], status="success", type_="batch_analyze")
    await _add_task(["insp-e"], status="failed", type_="batch_analyze")
    await _add_task(["insp-f"], status="running", type_="vector_backfill")

    async with async_session() as db:
        ids = await get_inflight_analysis_ids(db)
    assert {"insp-a", "insp-b", "insp-c"} <= ids
    assert "insp-d" not in ids
    assert "insp-e" not in ids
    assert "insp-f" not in ids


async def test_inflight_excluding_omits_other_tasks(client):
    """_inflight_excluding：排除其它未完成任务的素材，但保留当前任务自己的素材。"""
    cur = await _add_task(["insp-self"], status="running", type_="batch_analyze")
    await _add_task(["insp-other", "insp-self"], status="running", type_="batch_analyze")

    async with async_session() as db:
        inflight = await _inflight_excluding(db, cur)
    # 其他任务认领 insp-self 也被视为进行中（跨任务去重），并含 insp-other
    assert "insp-other" in inflight
    assert "insp-self" in inflight


async def test_unanalyzed_ids_excludes_inflight(client, upload):
    """get_unanalyzed_ids：排除已进入未完成任务的内存/数据库进行中素材。"""
    # 一个已分析成功的素材不算未分析；两个未分析素材，其中一个在跑批任务
    insp_ok = upload().json()["id"]
    insp_busy = upload().json()["id"]
    insp_free = upload().json()["id"]
    # 给 insp_busy 建一条 running 批量任务（视为进行中）
    await _add_task([insp_busy], status="running", type_="batch_analyze")

    async with async_session() as db:
        ids = await get_unanalyzed_ids(db, skip_ids={insp_ok})
    # 进行中的 insp_busy、已分析成功的 insp_ok 都不应返回；仅 insp_free 返回
    assert insp_busy not in ids
    assert insp_ok not in ids
    assert insp_free in ids


async def test_batch_analyze_route_excludes_inflight(client, upload):
    """POST /ai/batch-analyze：进行中素材被剔除，不会重复入队。"""
    import app.routers.ai_analysis as router_mod

    # 待分析素材：一个进行中（insp_busy），一个空闲（insp_free）
    insp_busy = upload().json()["id"]
    insp_free = upload().json()["id"]
    await _add_task([insp_busy], status="running", type_="batch_analyze")

    r = client.post("/api/ai/batch-analyze", json=[insp_busy, insp_free, "no-such-id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["skipped"] == 2  # 进行中 insp_busy + 不存在 no-such-id = 2

    # 校验任务实际只包含 inspir_free
    task_id = body["task_id"]
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert insp_busy not in (task.result or {}).get("inspiration_ids", [])
        assert insp_free in (task.result or {}).get("inspiration_ids", [])


async def test_batch_analyze_all_inflight_400(client, upload):
    """POST /ai/batch-analyze：所选素材全部进行中 → 400（不重复入队）。"""
    insp_busy = upload().json()["id"]
    await _add_task([insp_busy], status="running", type_="batch_analyze")

    r = client.post("/api/ai/batch-analyze", json=[insp_busy])
    assert r.status_code == 400, r.text
    assert "已在分析队列中" in r.json()["detail"]
