"""批量质量审核的上限与中断语义测试（代码审查后放开上限的配套保障）。

背景：批量导入（如 f2 抖音素材）后常有上万条待审核，200 的上限需要点几十次，
本次把单次上限放宽到 5000（约 17 小时/次，单并发）。因此必须保证：
  1. 上限边界前后端一致（5000 通过、5001 拒绝）
  2. 长任务可以暂停/取消——执行器每批检查一次任务状态，保留已判定结果
"""

import httpx
import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services.task_runner import create_quality_check_task, execute_quality_check


class _OkOllama:
    """Ollama 全部成功：审核判定为穿搭、非 AI 生成。"""

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


def test_quality_check_limit_boundary(client):
    """上限 5000：达到上限放行，超出则 422（前后端一致，避免静默截断）。"""
    ok = client.post("/api/ai/quality-check", params={"limit": 5000})
    assert ok.status_code == 200

    too_big = client.post("/api/ai/quality-check", params={"limit": 5001})
    assert too_big.status_code == 422

    zero = client.post("/api/ai/quality-check", params={"limit": 0})
    assert zero.status_code == 422


def _flip_status_after_first(target_status: str):
    """构造一个「首张审核完成后把任务置为 target_status」的假 Ollama 客户端。

    模拟用户在任务管理页点「取消/暂停」：执行器应在下一批检查时停下。
    注意测试里任务状态初始为 pending（worker 认领后才 running），
    因此这里不能以 running 为前置条件。
    """

    class _FlipAfterFirst(_OkOllama):
        task_id: int | None = None
        flipped = False

        async def post(self, url, json=None, **kwargs):
            response = await super().post(url, json=json, **kwargs)
            if self.task_id is not None and not type(self).flipped:
                type(self).flipped = True
                async with async_session() as db:
                    task = await db.get(TaskQueue, self.task_id)
                    if task is not None:
                        task.status = target_status
                        await db.commit()
            return response

    return _FlipAfterFirst


async def test_quality_check_stops_when_cancelled(client, upload, monkeypatch):
    """取消后立即停止：保留已判定结果、不写 100% 进度、不触发「全部失败」判定。

    回归点：此前执行器从不检查任务状态，5000 张的任务一旦提交就无法中断。
    """
    ids = []
    for _ in range(3):
        iid = upload().json()["id"]
        client.patch(f"/api/inspirations/{iid}", json={"quality_status": "pending"})
        ids.append(iid)

    fake = _flip_status_after_first("cancelled")
    monkeypatch.setattr(httpx, "AsyncClient", fake)

    async with async_session() as db:
        task = await create_quality_check_task(db, ids)
        fake.task_id = task.id
        await execute_quality_check(db, task)  # 正常返回（不抛错）

        await db.refresh(task)
        assert task.result["cancelled"] is True
        assert task.status == "cancelled"  # 尊重外部状态，worker 不会覆盖为 success
        assert task.progress < 100
        assert task.done == 1  # 只处理了第一张
        assert task.result["approved"] == 1

        # 未处理的素材保持 pending（未被误判）
        statuses = (
            await db.execute(
                select(Inspiration.quality_status).where(Inspiration.id.in_(ids))
            )
        ).scalars().all()
        assert sorted(statuses) == ["approved", "pending", "pending"]


async def test_quality_check_paused_keeps_status(client, upload, monkeypatch):
    """暂停语义与取消一致：停止本轮、保留结果、状态保持 paused。"""
    ids = []
    for _ in range(2):
        iid = upload().json()["id"]
        client.patch(f"/api/inspirations/{iid}", json={"quality_status": "pending"})
        ids.append(iid)

    fake = _flip_status_after_first("paused")
    monkeypatch.setattr(httpx, "AsyncClient", fake)

    async with async_session() as db:
        task = await create_quality_check_task(db, ids)
        fake.task_id = task.id
        await execute_quality_check(db, task)

        await db.refresh(task)
        assert task.status == "paused"
        assert task.result["cancelled"] is True
        assert task.done == 1
