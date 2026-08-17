"""任务执行器（task_runners）回归测试：批量删除、质量审核任务的创建与执行。"""

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.services.task_runners.batch_delete import (
    create_batch_delete_task,
    execute_batch_delete,
)
from app.services.task_runners.common import PermanentTaskError
from app.services.task_runners.quality_check import (
    create_quality_check_task,
    execute_quality_check,
)


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
