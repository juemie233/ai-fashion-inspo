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
    """mock rebuild_inspiration_vectors：fail_ids 中的素材返回全部失败，其余成功。"""

    def __init__(self) -> None:
        self.fail_ids: set[str] = set()

    async def __call__(self, db, inspiration_id: str) -> dict:
        if inspiration_id in self.fail_ids:
            return {"text": False, "image": False}
        return {"text": True, "image": True}


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
    monkeypatch.setattr(vb_module, "rebuild_inspiration_vectors", fake)

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
    monkeypatch.setattr(vb_module, "rebuild_inspiration_vectors", fake)

    async with async_session() as db:
        task = await _make_backfill_task(db, [a, b])
        await vb_module.execute_vector_backfill(db, task)  # 部分成功不抛异常

        assert task.result["image_done"] == 1
        assert task.result["image_failed"] == 1
        assert task.result["text_done"] == 1
        assert task.result["text_skipped"] == 1
        assert task.done == 2
        assert task.progress == 100


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
    monkeypatch.setattr(vb_module, "rebuild_inspiration_vectors", fake)

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
