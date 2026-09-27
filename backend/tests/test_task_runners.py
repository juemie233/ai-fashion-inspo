"""任务执行器（task_runners）回归测试：批量删除、质量审核任务的创建与执行。"""

import asyncio

import httpx
import pytest
from sqlalchemy import delete, func, select, update

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.task import PendingVectorBackfill, TaskQueue
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
    """mock _build_material_vectors（**窗口批量**接缝）：fail_ids 中的素材返回全部失败，
    其余成功；text_fail_ids 中的素材只让文本失败（图像照常成功）。

    执行器已改为整窗批量编码，所以接缝入参是「一个窗口的素材列表」，返回等长的
    _ItemVectors 列表（不再是单条素材的二元组）。
    fail_ids / text_fail_ids 同时置 text_failed=True：文本「有内容却拿不到向量」必须
    计入 text_failed 而不是 text_skipped，否则失败会被当成「这条没文本」静默吞掉。
    """

    def __init__(self) -> None:
        self.fail_ids: set[str] = set()
        self.text_fail_ids: set[str] = set()

    async def __call__(self, inss, with_images: bool = True) -> list:
        return [
            vb_module._ItemVectors(
                text=None
                if (insp.id in self.fail_ids or insp.id in self.text_fail_ids)
                else [0.1, 0.2],
                image=None if insp.id in self.fail_ids else [0.3, 0.4],
                text_failed=insp.id in self.fail_ids or insp.id in self.text_fail_ids,
            )
            for insp in inss
        ]


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
    """直接构造向量回填任务（绕过 create 的数据库过滤，聚焦执行器逻辑验证）。

    状态取 ``running`` 而非 ``pending``：真实调用链里 worker 先原子认领
    （pending → running，见 app/worker.py ``_claim_next_task``）才把任务交给
    执行器，执行器循环内的检查点是**严格**的——只要不再是 running 就停下。
    若这里造 pending，任务会在第一个检查点被判定为「被外部中断」而提前返回，
    收尾的落库验证 / 防假成功判定根本走不到，用例就测不到真实行为。
    """
    task = TaskQueue(
        type="vector_backfill",
        status="running",
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
        # 失败计入 text_failed，不再混进 text_skipped（否则「缺向量」会被当成「没文本」）
        assert task.result["text_failed"] == 1
        assert task.result["text_skipped"] == 0
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


# ============ 向量回填：暂停 / 取消 / 断点续算 ============


async def _seed_image_inspirations(n: int) -> list[str]:
    """直接插 n 条图片素材（不走上传接口）：本组用例只验执行器的停止/续算语义。"""
    ids = [f"vb-stop-{i:03d}" for i in range(n)]
    async with async_session() as db:
        for iid in ids:
            db.add(
                Inspiration(
                    id=iid,
                    source_type="manual_upload",
                    source_url=f"https://example.com/{iid}",
                    file_path=f"images/2026-09/{iid}.webp",
                    media_type="image",
                )
            )
        await db.commit()
    return ids


class _FlippingVectors:
    """逐条返回可用向量；累计处理到第 flip_at 条后把任务状态改成 new_status。

    模拟真实竞态：状态由**接口进程**（另一个会话）改写，执行器只能在下一次
    ``db.refresh(task)`` 时看到——这正是每个窗口边界要做的事。
    接缝是窗口（整窗一次编码），所以 calls 按**素材条数**累计，断言口径与逐条时一致。
    """

    def __init__(self, task_id: int, flip_at: int, new_status: str) -> None:
        self.calls = 0
        self.task_id = task_id
        self.flip_at = flip_at
        self.new_status = new_status

    async def __call__(self, inss, with_images: bool = True) -> list:
        self.calls += len(inss)
        if self.calls >= self.flip_at:
            async with async_session() as other:
                await other.execute(
                    update(TaskQueue)
                    .where(TaskQueue.id == self.task_id)
                    .values(status=self.new_status)
                )
                await other.commit()
        return [vb_module._ItemVectors([0.1, 0.2], [0.3, 0.4]) for _ in inss]


async def test_vector_backfill_pause_then_resume_from_checkpoint(client, monkeypatch):
    """暂停：在进度检查点感知后把已算向量落盘并停下；恢复从 task.done 断点续算。

    用户要求：向量回填要能暂停。若恢复时从头重来，图像向量要重跑 CLIP——
    那等于「暂停付双倍代价」，用户会认为暂停没生效。所以这里同时锁两件事：
    ① 停下时进度落在检查点上、已算向量已落盘；② 恢复只处理剩余素材。
    """
    ids = await _seed_image_inspirations(60)
    interval = vb_module._PROGRESS_EVERY
    stop_at = interval * 2  # 第一个检查点之后再暂停 → 第二个检查点停下

    async with async_session() as db:
        task = await _make_backfill_task(db, ids)
        task_id = task.id
        fake = _FlippingVectors(task_id, flip_at=interval + 1, new_status="paused")
        _patch_backfill_fakes(monkeypatch, fake)

        await vb_module.execute_vector_backfill(db, task)
        await db.refresh(task)

        assert task.status == "paused"  # 执行器不改外部写入的状态
        assert task.done == stop_at
        assert task.progress < 100
        assert task.result["image_done"] == stop_at
        assert task.result["text_done"] == stop_at
        assert task.result["remaining"] == 60 - stop_at
        assert fake.calls == stop_at  # 只处理到检查点

    # 恢复：接口把任务放回 pending → worker 重新认领为 running（这里直接再执行一次）
    async with async_session() as db:
        await db.execute(
            update(TaskQueue).where(TaskQueue.id == task_id).values(status="running")
        )
        await db.commit()
        resumed = await db.get(TaskQueue, task_id)
        fake2 = _FlippingVectors(task_id, flip_at=10**9, new_status="running")
        _patch_backfill_fakes(monkeypatch, fake2)

        await vb_module.execute_vector_backfill(db, resumed)
        await db.refresh(resumed)

        # 断点续算：只编码剩余 10 条，前 50 条不重跑
        assert fake2.calls == 60 - stop_at
        assert resumed.done == 60
        assert resumed.progress == 100
        # 结果计数是**累计值**（50 + 10），不是本轮的 10
        assert resumed.result["image_done"] == 60
        assert resumed.result["text_done"] == 60
        assert resumed.result["remaining"] == 0


async def test_vector_backfill_cancel_stops_and_keeps_partial_result(client, monkeypatch):
    """取消：执行器停下、状态保持 cancelled、不留 100% 假完成、已算向量落盘。"""
    ids = await _seed_image_inspirations(30)
    interval = vb_module._PROGRESS_EVERY

    async with async_session() as db:
        task = await _make_backfill_task(db, ids)
        fake = _FlippingVectors(task.id, flip_at=1, new_status="cancelled")
        _patch_backfill_fakes(monkeypatch, fake)

        await vb_module.execute_vector_backfill(db, task)
        await db.refresh(task)

        assert task.status == "cancelled"  # 不被覆盖成 success
        assert task.progress < 100  # 不能冒充完成
        assert task.done == interval  # 停在第一个检查点
        assert task.result["image_done"] == interval
        assert task.result["text_done"] == interval
        assert task.result["remaining"] == 30 - interval


async def test_vector_backfill_pending_mid_flight_is_not_a_stop_signal(client, monkeypatch):
    """执行中被置回 pending（暂停后立刻「继续」）：继续算完，不当中断。

    锁定判据：执行器只把 paused / cancelled 当停止信号，pending 的语义是
    「排队等待被认领」，不是「停下」。若误把 pending 也当停止，收尾的落库验证 /
    防假成功 / 版本标记会被静默跳过（内部直接调用执行器时任务本就是 pending）。
    """
    ids = await _seed_image_inspirations(30)

    async with async_session() as db:
        task = await _make_backfill_task(db, ids)
        fake = _FlippingVectors(task.id, flip_at=1, new_status="pending")
        _patch_backfill_fakes(monkeypatch, fake)

        await vb_module.execute_vector_backfill(db, task)
        await db.refresh(task)

        assert fake.calls == len(ids), "pending 不该中断执行"
        assert task.done == len(ids)
        assert task.progress == 100
        assert task.result["image_done"] == len(ids)
        assert task.result["remaining"] == 0


async def test_vector_backfill_skips_when_already_stopped(client, monkeypatch):
    """进入执行前已被暂停/取消：一条都不处理（不白跑一批素材）。"""
    ids = await _seed_image_inspirations(3)

    for status in ("paused", "cancelled"):
        async with async_session() as db:
            task = await _make_backfill_task(db, ids)
            await db.execute(
                update(TaskQueue).where(TaskQueue.id == task.id).values(status=status)
            )
            await db.commit()
            fake = _FlippingVectors(task.id, flip_at=10**9, new_status=status)
            _patch_backfill_fakes(monkeypatch, fake)

            await vb_module.execute_vector_backfill(db, task)
            await db.refresh(task)

            assert fake.calls == 0, f"状态={status} 时不该编码任何素材"
            assert task.status == status
            assert task.progress == 0


async def test_vector_backfill_real_load_path_with_tags(client, upload, monkeypatch):
    """回归：真实加载路径（不 mock _build_material_vectors）带标签的素材不再抛
    MissingGreenlet。

    批量落盘重构后执行器改用 db.get 加载素材，而 InspirationTag.tag 是默认
    lazy="select"——build_inspiration_text 同步访问 t.tag.name 触发异步环境
    下的隐式懒加载 → "greenlet_spawn has not been called; can't call
    await_only()"，任务运行几秒即失败。修复为显式两级 selectinload。
    """
    insp_id = upload().json()["id"]
    r = client.post(
        f"/api/inspirations/{insp_id}/tags", json={"names": ["白色"], "category": "color"}
    )
    assert r.status_code == 200, r.text

    # 只 mock 向量生成与 LanceDB 写入/读回；素材加载、文本构造走真实路径
    # （图像走批量接缝 generate_image_embeddings：整窗一次编码）
    async def fake_text_emb(_text):
        return [0.1, 0.2]

    async def fake_image_embs(file_paths):
        return [[0.3, 0.4] for _ in file_paths]

    from app.services.vector import embedding as emb_module

    monkeypatch.setattr(emb_module, "generate_text_embedding", fake_text_emb)
    monkeypatch.setattr(emb_module, "generate_image_embeddings", fake_image_embs)

    async def fake_batch_upsert(_kind, items):
        return len(items)

    monkeypatch.setattr(vb_module.vector_store, "batch_upsert_vectors", fake_batch_upsert)
    monkeypatch.setattr(vb_module.vector_store, "get_vector", _fake_get_vector)

    async with async_session() as db:
        task = await _make_backfill_task(db, [insp_id])
        # 修复前此处抛 MissingGreenlet → 任务失败
        await vb_module.execute_vector_backfill(db, task)
        await db.refresh(task)
        assert task.status != "failed"
        assert task.result["text_done"] == 1
        assert task.result["image_done"] == 1


async def test_vector_backfill_skips_deleted_inspiration(client, upload, monkeypatch):
    """已删除（垃圾桶）素材在回填中被跳过，不生成向量也不计入失败。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/trash")

    fake = _FakeRebuildVectors()
    _patch_backfill_fakes(monkeypatch, fake)
    called: list[str] = []

    async def spy_build(inss, with_images: bool = True):
        called.extend(insp.id for insp in inss)
        return [vb_module._ItemVectors([0.1], [0.2]) for _ in inss]

    monkeypatch.setattr(vb_module, "_build_material_vectors", spy_build)

    async with async_session() as db:
        task = await _make_backfill_task(db, [insp_id])
        await vb_module.execute_vector_backfill(db, task)
        await db.refresh(task)
        # 状态 success 由 worker _run_task 置；执行器只负责进度与统计
        assert task.done == 1 and task.progress == 100
        assert called == []  # 已删除素材未被构建向量
        assert task.result["text_skipped"] == 1
        assert task.result["image_skipped"] == 1


async def test_vector_backfill_batches_image_encoding_per_window(client, monkeypatch):
    """图像编码按窗口批量：整窗图片一次交给 generate_image_embeddings。

    这是「回填提速」的落点——单卡上逐张 encode（batch=1）时 GPU 大量时间在等数据
    搬运，整窗一次前向才是关键。这里锁住批量粒度：30 条素材 = 两个窗口 = 两次调用，
    分别是 _PROGRESS_EVERY 张与 5 张（不是 30 次单张）。
    """
    ids = await _seed_image_inspirations(30)
    batches: list[list[str]] = []

    async def fake_image_embs(file_paths):
        batches.append(list(file_paths))
        return [[0.3, 0.4] for _ in file_paths]

    async def fake_batch_upsert(_kind, items):
        return len(items)

    from app.services.vector import embedding as emb_module

    monkeypatch.setattr(emb_module, "generate_image_embeddings", fake_image_embs)
    monkeypatch.setattr(vb_module.vector_store, "batch_upsert_vectors", fake_batch_upsert)
    monkeypatch.setattr(vb_module.vector_store, "get_vector", _fake_get_vector)

    async with async_session() as db:
        task = await _make_backfill_task(db, ids)
        await vb_module.execute_vector_backfill(db, task)
        await db.refresh(task)

        assert [len(batch) for batch in batches] == [vb_module._PROGRESS_EVERY, 5]
        assert task.result["image_done"] == 30


async def test_vector_backfill_text_failure_requeued_not_silent(client, upload, monkeypatch):
    """文本嵌入失败：计入 text_failed（不混进 text_skipped）并重新登记待回填队列。

    旧行为：请求超时后 return None，被当成「这条本来就没文本」静默跳过——任务显示
    成功、文本向量却永久缺失，且不会再有任何重试（2026-09-27 实测连续 27 分钟每条
    都如此）。
    """
    a = upload().json()["id"]
    b = upload().json()["id"]

    fake = _FakeRebuildVectors()
    fake.text_fail_ids = {a}  # 只让文本失败，图像照常成功
    _patch_backfill_fakes(monkeypatch, fake)

    async with async_session() as db:
        # 清掉上传时登记的待回填行，确保下面的登记确实是执行器写的
        await db.execute(delete(PendingVectorBackfill))
        await db.commit()

        task = await _make_backfill_task(db, [a, b])
        await vb_module.execute_vector_backfill(db, task)
        await db.refresh(task)

        assert task.result["text_failed"] == 1
        assert task.result["text_done"] == 1
        assert task.result["text_skipped"] == 0  # 失败不再被算成「无文本」
        assert task.result["image_done"] == 2  # 文本失败不影响图像链路
        requeued = (
            await db.execute(select(PendingVectorBackfill.inspiration_id))
        ).scalars().all()
        assert list(requeued) == [a]  # 已登记：能力恢复后自动重试，不会永久缺失
        assert task.done == 2 and task.progress == 100


async def test_vector_backfill_all_text_failed_raises(client, upload, monkeypatch):
    """文本嵌入全失败（零成功）→ 抛永久错误，不冒充成功（与图像链路同口径）。

    典型场景：Ollama 被 VLM / 人脸任务抢占，嵌入请求整体超时。若此时任务报成功，
    用户看到的就是「成功但文本向量缺一片」，而且不会再有重试。
    """
    a = upload().json()["id"]
    b = upload().json()["id"]

    fake = _FakeRebuildVectors()
    fake.text_fail_ids = {a, b}  # 文本全失败、图像正常
    _patch_backfill_fakes(monkeypatch, fake)

    async with async_session() as db:
        task = await _make_backfill_task(db, [a, b])
        with pytest.raises(PermanentTaskError) as exc:
            await vb_module.execute_vector_backfill(db, task)
        assert "文本向量全部生成失败" in str(exc.value)

        await db.refresh(task)
        assert task.result["text_failed"] == 2
        assert task.result["text_done"] == 0


async def test_vector_backfill_text_only_skips_image_encoding(client, monkeypatch):
    """mode="text"（全量重建文本向量）：完全跳过 CLIP，并写入公式版本标记。

    管理页「重建文本向量」按钮走这条路（routers/admin.py 创建 mode="text" 任务），
    跳过图像向量的意义就是别为纯文本重建白跑一遍全库 CLIP；窗口批量改造后必须仍然
    一张图都不编码，否则这个按钮会悄悄变成「全库 CLIP + 文本」双倍开销。
    """
    ids = await _seed_image_inspirations(30)
    image_batches: list[list[str]] = []
    stored_versions: list[int] = []

    async def fake_image_embs(file_paths):
        image_batches.append(list(file_paths))
        return [[0.3, 0.4] for _ in file_paths]

    async def fake_text_emb(_text):
        return [0.1, 0.2]

    async def fake_batch_upsert(_kind, items):
        return len(items)

    from app.services.vector import embedding as emb_module
    from app.services.vector.embedding import TEXT_EMBEDDING_FORMULA_VERSION

    monkeypatch.setattr(emb_module, "generate_image_embeddings", fake_image_embs)
    monkeypatch.setattr(emb_module, "generate_text_embedding", fake_text_emb)
    # 种子素材本身没有标签/正文，这里让它们都有语义内容（否则全是 text_skipped）
    monkeypatch.setattr(emb_module, "build_inspiration_text", lambda _insp: "法式穿搭")
    monkeypatch.setattr(vb_module.vector_store, "batch_upsert_vectors", fake_batch_upsert)
    monkeypatch.setattr(vb_module.vector_store, "get_vector", _fake_get_vector)
    monkeypatch.setattr(
        vb_module.vector_store,
        "set_stored_text_formula_version",
        lambda version: stored_versions.append(version),
    )

    async with async_session() as db:
        task = await _make_backfill_task(db, ids)
        task.result = {**(task.result or {}), "mode": "text"}
        await db.commit()

        await vb_module.execute_vector_backfill(db, task)
        await db.refresh(task)

        assert image_batches == []  # text 模式一张图都不编码
        assert task.result["text_done"] == 30
        assert task.result["image_done"] == 0
        assert task.result["image_failed"] == 0
        assert task.result["mode"] == "text"
        assert stored_versions == [TEXT_EMBEDDING_FORMULA_VERSION]
        assert task.done == 30 and task.progress == 100


async def test_vector_backfill_text_embed_concurrency_follows_setting(client, monkeypatch):
    """文本嵌入并发度受 VECTOR_EMBED_CONCURRENCY 控制：默认 1（顺序），调大才并发。

    为什么默认 1：实测 Ollama 服务端默认串行处理嵌入（OLLAMA_NUM_PARALLEL 未设置）
    时，8 路并发比顺序慢 1.7 倍——客户端并发只是把请求堆在服务端排队。所以这个开关
    要等 OLLAMA_NUM_PARALLEL 调大后再打开。本用例同时锁住「默认不并发」与
    「调大后确实并发」，避免默认值被误改、或开关变成摆设。
    """
    ids = await _seed_image_inspirations(6)
    state = {"now": 0, "peak": 0}

    async def fake_text_emb(_text):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.01)  # 让并发有机会真正重叠
        state["now"] -= 1
        return [0.1, 0.2]

    async def fake_batch_upsert(_kind, items):
        return len(items)

    async def fake_image_embs(file_paths):
        return [[0.3, 0.4] for _ in file_paths]

    from app.services.vector import embedding as emb_module

    monkeypatch.setattr(emb_module, "generate_text_embedding", fake_text_emb)
    # 图像链路也一并打桩：本用例只关心文本嵌入的并发，别让真 CLIP 去读不存在的
    # 种子图片路径（否则全部图像失败 → 触发「图像全失败」的防假成功抛错）
    monkeypatch.setattr(emb_module, "generate_image_embeddings", fake_image_embs)
    # 种子素材本身没有标签/正文，这里让它们都有语义内容（否则全是 text_skipped）
    monkeypatch.setattr(emb_module, "build_inspiration_text", lambda _insp: "法式穿搭")
    monkeypatch.setattr(vb_module.vector_store, "batch_upsert_vectors", fake_batch_upsert)
    monkeypatch.setattr(vb_module.vector_store, "get_vector", _fake_get_vector)

    async def run_once() -> None:
        state["now"] = state["peak"] = 0
        async with async_session() as db:
            task = await _make_backfill_task(db, ids)
            await vb_module.execute_vector_backfill(db, task)

    # 默认 1：顺序执行，峰值并发 1
    await run_once()
    assert state["peak"] == 1

    # 调到 4：出现真正的并发
    monkeypatch.setattr(settings, "vector_embed_concurrency", 4)
    await run_once()
    assert state["peak"] > 1


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
