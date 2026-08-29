"""任务执行器（task_runners）回归测试：批量删除、质量审核任务的创建与执行。"""

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services.task_runners.batch_delete import (
    create_batch_delete_task,
    execute_batch_delete,
)
from app.services.task_runners.common import PermanentTaskError
from app.services.task_runners.quality_check import (
    create_quality_check_task,
    execute_quality_check,
)
from app.services.task_runners import vector_backfill as vb_module


async def test_execute_batch_delete_deletes_records_and_files(client, upload):
    """批量删除：删除数据库记录 + 物理删除文件 + 释放空间统计。"""
    a = upload().json()["id"]
    b = upload().json()["id"]

    async with async_session() as db:
        # 记录删除前的文件路径，用于删除后校验物理文件确实消失
        rows = (await db.execute(
            select(Inspiration.file_path, Inspiration.thumbnail_path)
            .where(Inspiration.id.in_([a, b]))
        )).all()
        paths = [settings.storage_root / p for row in rows for p in row if p]
        assert paths and all(p.exists() for p in paths)  # 上传确实落盘

        task = await create_batch_delete_task(db, [a, b], label="ids")
        assert task.total == 2

        await execute_batch_delete(db, task)

        assert task.result["deleted_count"] == 2
        assert task.result["freed_bytes"] > 0
        assert task.done == 2
        assert task.progress == 100

        remaining = await db.scalar(select(func.count(Inspiration.id)))
        assert remaining == 0

    # 删除后：这些文件已从磁盘物理删除
    assert all(not p.exists() for p in paths)


async def test_execute_batch_delete_empty_ids(client):
    """空 ID 列表：任务秒完成，不删任何记录。"""
    async with async_session() as db:
        task = await create_batch_delete_task(db, [], label="ids")
        await execute_batch_delete(db, task)

        assert task.done == 0
        assert task.progress == 100
        assert task.result["deleted_count"] == 0


# ============ 质量审核执行器 ============


class _FailAllOllama:
    """模拟 Ollama 全部请求返回 400（模型未就绪/请求被拒，永久错误场景）。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def post(self, url, json=None, **kwargs):
        # 注意：httpx.Response.raise_for_status 要求 request 已设置，必须传入
        return httpx.Response(400, request=httpx.Request("POST", url))


class _FailFirstOllama:
    """前 1 次请求返回 400（部分失败场景），之后按 prompt 正常返回。"""

    call_count = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def post(self, url, json=None, **kwargs):
        type(self).call_count += 1
        request = httpx.Request("POST", url)
        if type(self).call_count == 1:
            return httpx.Response(400, request=request)
        prompt = ((json or {}).get("messages") or [{}])[0].get("content", "")
        if "疑似由 AI 生成" in prompt:
            content = '{"is_ai_generated": false, "confidence": 0.1}'
        else:
            content = '{"is_outfit": true, "reason": "穿搭照片"}'
        return httpx.Response(200, json={"message": {"content": content}}, request=request)


@pytest.fixture
def ollama_all_fail(monkeypatch):
    """Ollama 全部请求失败（httpx.AsyncClient → _FailAllOllama）。"""
    monkeypatch.setattr(httpx, "AsyncClient", _FailAllOllama)


@pytest.fixture
def ollama_fail_first(monkeypatch):
    """Ollama 前 1 次请求失败，之后成功。"""
    monkeypatch.setattr(httpx, "AsyncClient", _FailFirstOllama)
    _FailFirstOllama.call_count = 0


async def test_quality_check_all_failed_raises_permanent(client, upload, ollama_all_fail):
    """质量审核整批失败（Ollama 400）：任务抛永久错误，不再冒充「完成 2/2」。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    for iid in (a, b):
        client.patch(f"/api/inspirations/{iid}", json={"quality_status": "pending"})

    async with async_session() as db:
        task = await create_quality_check_task(db, [a, b])
        with pytest.raises(PermanentTaskError) as exc_info:
            await execute_quality_check(db, task)
        assert "质量审核全部失败" in str(exc_info.value)

        await db.refresh(task)
        assert task.result["failed"] == 2
        assert task.result["pending"] == 2
        assert task.result["approved"] == 0
        assert task.result["rejected"] == 0
        # 素材保持 pending（未被误判为通过/拒绝）
        statuses = (
            await db.execute(
                select(Inspiration.quality_status).where(Inspiration.id.in_([a, b]))
            )
        ).scalars().all()
        assert statuses == ["pending", "pending"]


async def test_quality_check_partial_failed_still_success(client, upload, ollama_fail_first):
    """质量审核部分失败：任务正常完成，result 单列 failed 张数（不误导为全成功）。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    for iid in (a, b):
        client.patch(f"/api/inspirations/{iid}", json={"quality_status": "pending"})

    async with async_session() as db:
        task = await create_quality_check_task(db, [a, b])
        await execute_quality_check(db, task)  # 部分失败不抛异常

        assert task.result["failed"] == 1
        assert task.result["approved"] == 1
        assert task.result["pending"] == 1
        assert task.done == 2
        assert task.progress == 100


# ============ 向量回填执行器 ============


class _FakeRebuildVectors:
    """mock _build_material_vectors：fail_ids 中的素材返回全部失败，其余成功。

    （向量回填执行器已改为批量写入，接缝从 rebuild_inspiration_vectors
    换成 _build_material_vectors + vector_store.batch_upsert_vectors。）
    """

    def __init__(self) -> None:
        self.fail_ids: set[str] = set()

    async def __call__(self, insp) -> tuple[list[float] | None, list[float] | None]:
        if insp.id in self.fail_ids:
            return None, None
        return [0.1, 0.2], [0.3, 0.4]


def _patch_backfill_fakes(monkeypatch, fake: "_FakeRebuildVectors") -> None:
    """统一打桩：向量构造走 fake，批量写入/读回走内存假实现（维度与配置无关）。"""
    monkeypatch.setattr(vb_module, "_build_material_vectors", fake)

    async def fake_batch_upsert(kind: str, items):
        return len(items)

    monkeypatch.setattr(vb_module.vector_store, "batch_upsert_vectors", fake_batch_upsert)
    monkeypatch.setattr(vb_module.vector_store, "get_vector", _fake_get_vector)


async def _fake_get_vector(kind: str, inspiration_id: str):
    """mock vector_store.get_vector：声称写入的向量都能读回（落库验证通过）。"""
    return [0.1] * (384 if kind == "text" else 512)


async def _make_backfill_task(db, inspiration_ids: list[str]) -> TaskQueue:
    """直接构造向量回填任务（绕过 create 的数据库过滤，聚焦执行器逻辑验证）。"""
    task = TaskQueue(
        type="vector_backfill",
        status="pending",
        progress=0,
        total=len(inspiration_ids),
        done=0,
        result={"inspiration_ids": inspiration_ids},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def test_vector_backfill_all_image_failed_raises(client, upload, monkeypatch):
    """向量回填：全部图片素材向量生成失败时任务抛永久错误（不再冒充「完成」）。"""
    a = upload().json()["id"]
    b = upload().json()["id"]

    fake = _FakeRebuildVectors()
    fake.fail_ids = {a, b}
    _patch_backfill_fakes(monkeypatch, fake)

    async with async_session() as db:
        task = await _make_backfill_task(db, [a, b])
        with pytest.raises(PermanentTaskError):
            await vb_module.execute_vector_backfill(db, task)

        await db.refresh(task)
        assert task.result["image_failed"] == 2
        assert task.result["image_done"] == 0
        assert task.result["text_done"] == 0


async def test_vector_backfill_partial_success(client, upload, monkeypatch):
    """向量回填：部分素材成功时任务正常完成，result 单列失败数（不误导为全成功）。"""
    a = upload().json()["id"]
    b = upload().json()["id"]

    fake = _FakeRebuildVectors()
    fake.fail_ids = {b}
    _patch_backfill_fakes(monkeypatch, fake)

    async with async_session() as db:
        task = await _make_backfill_task(db, [a, b])
        await vb_module.execute_vector_backfill(db, task)  # 部分成功不抛异常

        assert task.result["image_done"] == 1
        assert task.result["image_failed"] == 1
        assert task.result["text_done"] == 1
        assert task.result["text_skipped"] == 1
        assert task.done == 2
        assert task.progress == 100


async def test_vector_backfill_verify_persisted_fails(client, upload, monkeypatch):
    """落库验证：声称写入成功但向量读不回（如向量库目录被外部删除/覆盖）
    时任务抛永久错误，不再冒充「完成」——防止「假成功」导致缺失向量静默累积。"""
    a = upload().json()["id"]

    fake = _FakeRebuildVectors()  # 全部成功
    _patch_backfill_fakes(monkeypatch, fake)
    # 读回全 None：模拟写入未持久化（目录被删/覆盖后重建为空）
    async def _missing(_kind: str, _inspiration_id: str):
        return None

    monkeypatch.setattr(vb_module.vector_store, "get_vector", _missing)

    async with async_session() as db:
        task = await _make_backfill_task(db, [a])
        with pytest.raises(PermanentTaskError) as exc:
            await vb_module.execute_vector_backfill(db, task)
        assert "落库验证失败" in str(exc.value)


# ============ 幂等断言：同一任务重跑，结果与统计不变、无副作用 ============


class _AlwaysOkOllama:
    """模拟 Ollama 全部请求成功（幂等重跑场景：两次执行结果一致）。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def post(self, url, json=None, **kwargs):
        request = httpx.Request("POST", url)
        prompt = ((json or {}).get("messages") or [{}])[0].get("content", "")
        if "疑似由 AI 生成" in prompt:
            content = '{"is_ai_generated": false, "confidence": 0.1}'
        else:
            content = '{"is_outfit": true, "reason": "穿搭照片"}'
        return httpx.Response(200, json={"message": {"content": content}}, request=request)


async def test_vector_backfill_rerun_idempotent(client, upload, monkeypatch):
    """幂等：向量回填任务成功重跑，统计与第一次一致（upsert 语义，无重复向量）。"""
    a = upload().json()["id"]
    b = upload().json()["id"]

    fake = _FakeRebuildVectors()  # fail_ids 为空 → 全部成功
    _patch_backfill_fakes(monkeypatch, fake)

    async with async_session() as db:
        task = await _make_backfill_task(db, [a, b])
        await vb_module.execute_vector_backfill(db, task)
        first = {
            "image_done": task.result["image_done"],
            "image_failed": task.result["image_failed"],
            "text_done": task.result["text_done"],
            "text_skipped": task.result["text_skipped"],
        }
        assert first["image_done"] == 2  # 首次执行全部成功

        # worker 重复认领/重试导致重跑：结果统计必须与第一次完全一致，且不抛错
        await vb_module.execute_vector_backfill(db, task)
        second = {
            "image_done": task.result["image_done"],
            "image_failed": task.result["image_failed"],
            "text_done": task.result["text_done"],
            "text_skipped": task.result["text_skipped"],
        }
        assert second == first
        assert task.done == 2


async def test_quality_check_rerun_no_side_effects(client, upload, monkeypatch):
    """幂等：质量审核任务重跑不产生新的审核日志/判定记录（无副作用）。"""
    from app.models.inspiration import AIAnalysisLog, AIQualityReview

    monkeypatch.setattr(httpx, "AsyncClient", _AlwaysOkOllama)
    a = upload().json()["id"]
    b = upload().json()["id"]
    for iid in (a, b):
        client.patch(f"/api/inspirations/{iid}", json={"quality_status": "pending"})

    async with async_session() as db:
        task = await create_quality_check_task(db, [a, b])
        await execute_quality_check(db, task)
        assert task.result["approved"] == 2  # 首次执行全部通过

        logs_after_first = await db.scalar(
            select(func.count(AIAnalysisLog.id)).where(
                AIAnalysisLog.log_type == "quality_check"
            )
        )
        reviews_after_first = await db.scalar(select(func.count(AIQualityReview.id)))
        assert logs_after_first == 2

        # 重跑：素材已 approved 被过滤（total=0 秒完成），不写任何新日志/判定
        await execute_quality_check(db, task)
        logs_after_rerun = await db.scalar(
            select(func.count(AIAnalysisLog.id)).where(
                AIAnalysisLog.log_type == "quality_check"
            )
        )
        reviews_after_rerun = await db.scalar(select(func.count(AIQualityReview.id)))
        assert logs_after_rerun == logs_after_first
        assert reviews_after_rerun == reviews_after_first


async def test_batch_delete_rerun_idempotent(client, upload):
    """幂等：批量删除任务重跑（记录已删）不抛错、不重复删、统计为 0。"""
    a = upload().json()["id"]
    b = upload().json()["id"]

    async with async_session() as db:
        task = await create_batch_delete_task(db, [a, b], label="ids")
        await execute_batch_delete(db, task)
        assert task.result["deleted_count"] == 2
        remaining = await db.scalar(select(func.count(Inspiration.id)))
        assert remaining == 0

        # 重跑：素材已不存在 → 查不到待删记录，deleted_count=0，不抛错
        await execute_batch_delete(db, task)
        assert task.result["deleted_count"] == 0
        assert task.result["freed_bytes"] == 0
        assert remaining == 0


# ============ 向量回填攒批机制（批量触发策略） ============


@pytest.fixture
def small_batch_threshold(monkeypatch):
    """把攒批触发阈值调小（3），便于用例低成本触发自动 flush。"""
    monkeypatch.setattr(vb_module, "VECTOR_BACKFILL_BATCH_SIZE", 3)
    return 3


async def _pending_ids() -> list[str]:
    """读取待回填表中的素材 ID 列表（独立会话）。"""
    from app.models.task import PendingVectorBackfill

    async with async_session() as db:
        result = await db.execute(select(PendingVectorBackfill.inspiration_id))
        return [row[0] for row in result.all()]


async def _insert_inspiration(db) -> str:
    """直插一条素材记录（绕过上传接口，避免触发攒批登记干扰用例断言）。"""
    insp = Inspiration(file_path="images/test.jpg")
    db.add(insp)
    await db.commit()
    await db.refresh(insp)
    return insp.id


async def _backfill_task_count(db) -> int:
    """统计任务队列中的向量回填任务数量。"""
    return (
        await db.execute(
            select(func.count()).select_from(TaskQueue).where(
                TaskQueue.type == "vector_backfill"
            )
        )
    ).scalar() or 0


async def test_enqueue_below_threshold_no_task(client, small_batch_threshold):
    """攒批未达阈值：不创建任务，素材登记进待回填表；同素材重复登记幂等去重。"""
    async with async_session() as db:
        a = await _insert_inspiration(db)
        b = await _insert_inspiration(db)

        task = await vb_module.enqueue_vector_backfills(db, [a])
        assert task is None  # 未达阈值：不再创建 1/1 小任务
        task = await vb_module.enqueue_vector_backfills(db, [a, b])
        assert task is None
        # enqueue 不再内部提交：登记行由调用方统一提交
        await db.commit()
        assert sorted(await _pending_ids()) == sorted([a, b])  # 同素材重复登记去重
        assert await _backfill_task_count(db) == 0  # 任务队列零 vector_backfill 任务


async def test_enqueue_reaches_threshold_auto_flush(client, small_batch_threshold):
    """累计达到阈值：自动创建包含全部待回填素材的批量任务，待回填表清空。"""
    async with async_session() as db:
        ids = [await _insert_inspiration(db) for _ in range(3)]  # 阈值=3

        task = await vb_module.enqueue_vector_backfills(db, ids)
        assert task is not None  # 达阈值自动 flush
        assert task.total == 3
        assert set(task.result["inspiration_ids"]) == set(ids)  # 顺序不保证，按集合比较
        assert task.done == 0  # 待 worker 执行

        # 待回填表已清空；任务队列只有这一个批量任务（没有 1/1 小任务）
        assert await _pending_ids() == []
        assert await _backfill_task_count(db) == 1


async def test_upload_batch_no_small_tasks(client, upload, small_batch_threshold):
    """集成：连续上传素材，仅当累计达到阈值时出现 1 个批量任务，全程无 1/1 小任务。"""
    async with async_session() as db:
        for i in range(3):
            upload()  # 每次上传都会登记待回填
            tasks = (
                await db.execute(
                    select(TaskQueue).where(TaskQueue.type == "vector_backfill")
                )
            ).scalars().all()
            # 前两次上传无任务；第 3 次上传（达阈值）出现 1 个批量任务
            assert len(tasks) == (1 if i == 2 else 0)
            if tasks:
                assert tasks[0].total == 3
        assert await _pending_ids() == []


async def test_flush_force_merges_extra_ids(client, small_batch_threshold):
    """手动触发（force）：忽略阈值，待回填素材与额外素材合并为一个批量任务。"""
    async with async_session() as db:
        a = await _insert_inspiration(db)
        b = await _insert_inspiration(db)

        await vb_module.enqueue_vector_backfills(db, [a])  # 1 个待回填（未达阈值）
        task = await vb_module.flush_pending_vector_backfills(
            db, force=True, extra_ids=[b]
        )
        assert task is not None
        assert task.total == 2
        assert set(task.result["inspiration_ids"]) == {a, b}
        assert await _pending_ids() == []  # 待回填表已清空


async def test_flush_no_pending_returns_none(client):
    """无待回填素材时 flush 返回 None（空任务不创建）。"""
    async with async_session() as db:
        task = await vb_module.flush_pending_vector_backfills(db, force=True)
        assert task is None


async def test_purge_small_backfill_tasks(client, upload):
    """历史清理：仅删已终态小任务；pending/running 小任务保留（不误删攒批新机制任务）。"""
    async with async_session() as db:
        # 小任务：成功（终态，应删）/ 排队（保留）/ 运行中（保留，心跳租约负责）
        for status, done in (("success", 1), ("pending", 0), ("running", 1)):
            db.add(
                TaskQueue(
                    type="vector_backfill", status=status, progress=100,
                    total=1, done=done, result={}, max_retries=2,
                )
            )
        # 大任务（手动批量回填产物）：应保留
        db.add(
            TaskQueue(
                type="vector_backfill", status="success", progress=100,
                total=10, done=10, result={}, max_retries=2,
            )
        )
        await db.commit()

        deleted = await vb_module.purge_small_backfill_tasks(db)
        assert deleted == 1  # 仅终态（success）小任务删除

        tasks = (
            await db.execute(
                select(TaskQueue).where(TaskQueue.type == "vector_backfill")
            )
        ).scalars().all()
        by_status = {t.status: t for t in tasks}
        assert set(by_status.keys()) == {"pending", "running", "success"}
        assert by_status["pending"].total == 1  # 排队小任务保留（可能是攒批新机制产物）
        assert by_status["running"].total == 1  # 运行中小任务保留（心跳租约负责）
        assert by_status["success"].total == 10  # 大任务保留

        # 幂等：重复执行不再删除任何任务
        assert await vb_module.purge_small_backfill_tasks(db) == 0
