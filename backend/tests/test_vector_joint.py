"""向量回填联合测试：上传 → 攒批登记 → flush → 执行 → 真实 LanceDB 落库 → 面板统计。

覆盖用户反馈场景「全量回填后，新上传素材向量几乎全部缺失」的完整链路。
测试环境已安装 lancedb（requirements.txt），目录由 conftest 隔离为临时目录
（settings.lancedb_dir 跟随 STORAGE_ROOT）；向量**写入真实 LanceDB**，
仅 mock 向量构造（_build_material_vectors，固定维度）——CLIP/Ollama 在
测试环境不可用，但真实落库/读回/LanceDB 统计全部走真实现。

用例：
- test_upload_backfill_joint_real_lancedb：上传→登记→flush→执行→真库读回→
  管理页「缺失向量」统计归零（面板口径与 /api/admin/vector-stats 一致）；
- test_failed_backfill_re_enqueues_pending：任务永久失败后素材重新登记回
  待回填队列（修复点：防「任务失败 + 队列已清空」导致素材静默丢失）；
- test_enqueue_auto_flush_at_threshold：攒批达到阈值自动创建批量任务；
- test_clip_load_retries_after_transient_failure：CLIP 加载失败带时效缓存，
  重试窗口过后自动恢复（修复点：防一次临时失败永久降级、整批向量失败）。
"""

from sqlalchemy import delete, func, select

import pytest
from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.task import PendingVectorBackfill, TaskQueue
from app.services.task_runners import vector_backfill as vb_module
from app.services.task_runners.common import PermanentTaskError
from app.services.vector import store as vector_store


class _FakeRebuildVectorsReal:
    """mock _build_material_vectors：返回与 LanceDB schema 同维度的真实向量。

    文本 384 维（lancedb_text_dim）、图像 512 维（lancedb_image_dim）——
    真实 batch_upsert_vectors 会按维度校验，维度不符直接跳过（记 warning），
    因此 mock 必须返回正确维度，否则「真实落库」断言必然失败且难以定位。
    """

    def __init__(self) -> None:
        self.fail_ids: set[str] = set()

    async def __call__(self, insp) -> tuple[list[float] | None, list[float] | None]:
        if insp.id in self.fail_ids:
            return None, None
        return [0.1] * settings.lancedb_text_dim, [0.2] * settings.lancedb_image_dim


# ═══════════════════════════════════════════════════════════════
#  联合链路：上传 → 登记 → flush → 真实执行 → 面板统计归零
# ═══════════════════════════════════════════════════════════════


async def test_upload_backfill_joint_real_lancedb(client, upload, monkeypatch):
    """上传新素材后向量最终入库、管理页「缺失」归零（用户反馈现象的完整链路）。"""
    a = upload().json()["id"]
    b = upload().json()["id"]

    async with async_session() as db:
        # 1) 上传即登记待回填队列（未达阈值不自动 flush）
        pending = (
            await db.execute(select(PendingVectorBackfill.inspiration_id))
        ).scalars().all()
        assert sorted(pending) == sorted([a, b])

        # 2) 模拟手动「一键向量化」：flush 攒批队列 → 创建批量任务、清空队列
        task = await vb_module.flush_pending_vector_backfills(db, force=True)
        assert task is not None
        assert task.total == 2
        assert (
            await db.execute(select(func.count()).select_from(PendingVectorBackfill))
        ).scalar() == 0

        # 3) 执行（真实 LanceDB 写入，仅向量构造走 mock）
        fake = _FakeRebuildVectorsReal()
        monkeypatch.setattr(vb_module, "_build_material_vectors", fake)
        await vb_module.execute_vector_backfill(db, task)
        assert task.done == 2
        assert task.progress == 100
        assert task.result["text_done"] == 2
        assert task.result["image_done"] == 2

    # 4) 真实 LanceDB 落库读回（不在 db 会话内，独立验证持久化结果）
    for iid in (a, b):
        assert await vector_store.get_vector("image", iid) is not None
        assert await vector_store.get_vector("text", iid) is not None
    assert await vector_store.count_vectors("image") == 2
    assert await vector_store.count_vectors("text") == 2

    # 5) 管理页统计口径（与 /api/admin/vector-stats 同实现）：「缺失」归零
    existing = await vector_store.list_vector_ids("image")
    assert {a, b} <= existing
    panel = client.get("/api/admin/vector-stats")
    assert panel.status_code == 200
    data = panel.json()
    assert data["total_inspirations"] == 2
    assert data["image_vectors"] == 2
    assert data["missing"] == 0


# ═══════════════════════════════════════════════════════════════
#  失败兜底：任务永久失败 → 素材重新登记（不再静默丢失）
# ═══════════════════════════════════════════════════════════════


async def test_failed_backfill_re_enqueues_pending(client, upload, monkeypatch):
    """修复点：全量失败（如 CLIP 不可用）时素材重新登记回待回填队列。

    修复前：flush 已清空队列，任务抛永久错误后这些素材既不自动重试、
    也不在队列中——只有手动「一键向量化」才能找回，用户视角即
    「上传新素材后向量几乎全部缺失」。修复后下次 flush 自动重试。
    """
    a = upload().json()["id"]

    async with async_session() as db:
        task = await vb_module.flush_pending_vector_backfills(db, force=True)
        assert task is not None
        # flush 后队列已清空（登记被取出）
        assert (
            await db.execute(select(func.count()).select_from(PendingVectorBackfill))
        ).scalar() == 0

        fake = _FakeRebuildVectorsReal()
        fake.fail_ids = {a}
        monkeypatch.setattr(vb_module, "_build_material_vectors", fake)
        with pytest.raises(PermanentTaskError):
            await vb_module.execute_vector_backfill(db, task)

        # 修复点生效：失败素材重新登记，能力恢复后自动重试
        rows = (
            await db.execute(select(PendingVectorBackfill.inspiration_id))
        ).scalars().all()
        assert list(rows) == [a]


# ═══════════════════════════════════════════════════════════════
#  攒批阈值：登记累计达到阈值自动创建批量任务
# ═══════════════════════════════════════════════════════════════


async def test_enqueue_auto_flush_at_threshold(client, upload, monkeypatch):
    """攒批机制：待回填素材累计达到阈值时自动创建批量任务并清空队列。"""
    monkeypatch.setattr(vb_module, "VECTOR_BACKFILL_BATCH_SIZE", 3)
    ids = [upload().json()["id"] for _ in range(2)]
    async with async_session() as db:
        # 未达阈值：仅登记，不创建任务
        assert (
            await db.execute(select(func.count()).select_from(PendingVectorBackfill))
        ).scalar() == 2
        tasks = (
            await db.execute(
                select(TaskQueue).where(TaskQueue.type == "vector_backfill")
            )
        ).scalars().all()
        assert tasks == []

    ids.append(upload().json()["id"])  # 第 3 个：达到阈值，自动 flush
    async with async_session() as db:
        assert (
            await db.execute(select(func.count()).select_from(PendingVectorBackfill))
        ).scalar() == 0
        task = (
            await db.execute(
                select(TaskQueue)
                .where(TaskQueue.type == "vector_backfill")
                .order_by(TaskQueue.id.desc())
                .limit(1)
            )
        ).scalars().first()
        assert task is not None
        assert task.total == 3
        assert sorted(task.result["inspiration_ids"]) == sorted(ids)


# ═══════════════════════════════════════════════════════════════
#  CLIP 加载失败时效缓存：临时故障自动恢复（不永久降级）
# ═══════════════════════════════════════════════════════════════


def test_clip_load_retries_after_transient_failure(monkeypatch):
    """修复点：CLIP 加载失败不永久降级——重试窗口过后自动重新加载。

    修复前：_image_model_error 无限期缓存，进程内一次失败（如 CUDA 显存
    暂被占满）后所有图像向量永久失败，批量回填任务整体失败。
    """
    import sys
    import time
    import types

    from app.services.vector import embedding as emb_module

    # 重置模块级状态（monkeypatch 在用例结束后自动恢复原值）
    monkeypatch.setattr(emb_module, "_image_model", None)
    monkeypatch.setattr(emb_module, "_image_model_error", None)
    monkeypatch.setattr(emb_module, "_image_model_error_at", None)

    # 首次依赖检测失败（一次性临时故障），之后恢复。注意：窗口内的重复调用
    # 命中错误缓存、不会触发检测，因此重试成功那次是第 2 次检测——桩只能让
    # 第 1 次失败，否则「窗口后重试」拿到的仍是失败原因，与真实恢复语义不符。
    calls = {"n": 0}

    def _flaky_check() -> str | None:
        calls["n"] += 1
        return "临时失败：CLIP 模型加载抖动" if calls["n"] == 1 else None

    monkeypatch.setattr(emb_module, "_check_clip_dependency", _flaky_check)

    # sentence_transformers 未安装：注入假模块让「加载成功」路径可测
    fake_mod = types.ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = lambda *a, **k: object()
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    # 第 1 次：依赖检测失败 → 错误带时效缓存
    assert emb_module._load_clip_model() is None
    assert emb_module._image_model_error is not None
    assert calls["n"] == 1

    # 未过重试窗口：直接命中缓存错误，不再反复触发加载（开销保护）
    assert emb_module._load_clip_model() is None
    assert calls["n"] == 1  # 未新增依赖检测调用

    # 重试窗口已过：错误清除并重新尝试 → 依赖已恢复 → 加载成功
    monkeypatch.setattr(emb_module, "_image_model_error_at", time.monotonic() - 10_000)
    model = emb_module._load_clip_model()
    assert model is not None
    assert emb_module._image_model is model
    assert emb_module._image_model_error is None
    assert calls["n"] == 2


# ═══════════════════════════════════════════════════════════════
#  表损坏（空骨架）自愈：先备份后重建，绝不静默丢数据
# ═══════════════════════════════════════════════════════════════


async def test_corrupt_table_backed_up_and_new_backfill_keeps_data(
    client, upload, monkeypatch
):
    """表损坏（空骨架）自愈：备份而非静默删除，新素材回填不丢未损坏表数据。

    回归（用户反馈「全量回填后新素材上传，几乎全部向量缺失」的最终机制）：
    修复前 ``_table`` 检测到表损坏（_versions 目录丢失）直接 drop_table 重建
    空表，目录内可能可抢救的数据被静默销毁，之后只有新批次向量 → 面板缺失。
    修复后损坏表先改名备份（.corrupt-*），再建新表；未损坏表数据不受影响。
    """
    import shutil

    a = upload().json()["id"]
    b = upload().json()["id"]
    fake = _FakeRebuildVectorsReal()
    monkeypatch.setattr(vb_module, "_build_material_vectors", fake)

    # 1) 全量回填 a/b：真实 LanceDB 落库
    async with async_session() as db:
        task = await vb_module.flush_pending_vector_backfills(db, force=True)
        await vb_module.execute_vector_backfill(db, task)
    assert await vector_store.count_vectors("text") == 2
    assert await vector_store.count_vectors("image") == 2

    # 2) 模拟表损坏：删除 text 表目录的 _versions（制造「空骨架」）
    vector_store.reset_connection()
    text_dir = settings.lancedb_dir / f"{settings.lancedb_text_table}.lance"
    versions = text_dir / "_versions"
    assert versions.exists(), f"预期存在版本目录: {versions}"
    shutil.rmtree(versions)

    # 2.5) 读路径遇损坏表不崩溃、不建表：降级为空结果（面板/搜索不 500），
    #      损坏证据目录保持原样等待写路径自愈备份
    assert await vector_store.count_vectors("text") == 0
    assert await vector_store.get_vector("text", a) is None
    assert await vector_store.list_vector_ids("text") == set()
    assert (text_dir / "_versions").exists() is False, "读路径不得重建/修复损坏目录"

    # 3) 新素材上传 → 回填执行 → 触发写路径自愈（先备份后重建）
    c = upload().json()["id"]
    async with async_session() as db:
        task = await vb_module.flush_pending_vector_backfills(db, force=True)
        await vb_module.execute_vector_backfill(db, task)

    # 4) 断言：损坏表被备份（未静默删除），新表可用，未损坏表数据保留
    backups = [
        p for p in settings.lancedb_dir.iterdir() if p.name.startswith(".corrupt-")
    ]
    assert backups, "损坏表应被改名备份而非直接删除"
    assert await vector_store.get_vector("text", c) is not None  # 新表写入可用
    assert await vector_store.get_vector("image", a) is not None  # 未损坏表旧数据保留
    assert await vector_store.get_vector("image", b) is not None


# ═══════════════════════════════════════════════════════════════
#  数据重置：reset_lancedb_storage 后新素材回填正常（空骨架事故根因）
# ═══════════════════════════════════════════════════════════════


async def test_reset_storage_then_new_backfill_works(client, upload, monkeypatch):
    """数据重置清空向量库后，新素材回填链路完整可用、面板统计正确。

    回归（历史事故根因）：数据重置删除 lancedb 目录若不在跨进程写锁内，
    会与 worker 并发写入竞争，把数据集删成「空骨架」（表注册存在但
    _versions/data 全空），之后所有向量操作失败、面板显示「几乎全部向量
    缺失」。修复后 reset_lancedb_storage 在写锁内删目录并丢连接缓存，
    后续操作懒加载重建空表，新素材向量正常入库。
    """
    fake = _FakeRebuildVectorsReal()
    monkeypatch.setattr(vb_module, "_build_material_vectors", fake)

    # 1) 全量回填 a：真实 LanceDB 落库
    a = upload().json()["id"]
    async with async_session() as db:
        task = await vb_module.flush_pending_vector_backfills(db, force=True)
        await vb_module.execute_vector_backfill(db, task)
    assert await vector_store.count_vectors("image") == 1

    # 2) 模拟「数据重置」：ai_reset 先清空业务库再删向量库（同一入口
    #    reset_lancedb_storage）。这里同步清空 inspirations，复刻重置后空库。
    async with async_session() as db:
        await db.execute(delete(Inspiration))
        await db.commit()
    await vector_store.reset_lancedb_storage()
    assert not settings.lancedb_dir.exists() or not any(
        settings.lancedb_dir.iterdir()
    ), "重置后向量库目录应为空或不存在"
    # 缓存连接已丢弃：计数读到的是重建后的空库，而非陈旧视图
    assert await vector_store.count_vectors("image") == 0
    assert await vector_store.count_vectors("text") == 0

    # 3) 重置后新素材上传 → 回填：懒加载重建空表，向量正常入库
    b = upload().json()["id"]
    async with async_session() as db:
        task = await vb_module.flush_pending_vector_backfills(db, force=True)
        assert task.total == 1
        await vb_module.execute_vector_backfill(db, task)

    # 4) 新向量真实落库读回，面板统计口径归零（不再报「几乎全部缺失」）
    assert await vector_store.get_vector("image", b) is not None
    assert await vector_store.get_vector("text", b) is not None
    panel = client.get("/api/admin/vector-stats")
    assert panel.status_code == 200
    data = panel.json()
    assert data["image_vectors"] == 1
    assert data["missing"] == 0


def test_reset_lancedb_storage_serialized_with_write_lock():
    """重置与向量写入共用同一把跨进程写锁：重置函数在锁内执行删库。

    直接验证串行化机制本身（不依赖真实并发时序）：重置执行期间写锁被
    同线程持有（可重入放行），且重置完成后锁释放、目录被清空。
    """
    import asyncio

    async def _scenario() -> None:
        # 先写一条向量，确保目录与表真实存在
        ok = await vector_store.upsert_vector(
            "image", "reset-lock-probe", [0.3] * settings.lancedb_image_dim
        )
        assert ok
        assert settings.lancedb_dir.exists()

        # 在写锁内调用重置：同线程可重入，不死锁；重置完成后目录被删
        def _reset_under_lock() -> None:
            with vector_store._vector_write_lock():
                vector_store._reset_storage_sync()
                # 锁仍持有期间目录已被清空
                assert not settings.lancedb_dir.exists()

        await asyncio.to_thread(_reset_under_lock)
        # 重置后写操作懒加载重建，功能恢复
        ok = await vector_store.upsert_vector(
            "image", "reset-lock-probe-2", [0.4] * settings.lancedb_image_dim
        )
        assert ok
        assert await vector_store.get_vector("image", "reset-lock-probe-2") is not None

    asyncio.run(_scenario())