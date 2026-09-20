"""感知哈希缓存补齐测试：扫描限时返回 + 后台任务补齐 + 「算不出哈希」不打转。

背景（2026-09 实测事故）：`scan_near_duplicates` 原先把缺失 phash 一次请求补到一张不剩。
库里 11,568 张 f2 抖音原图缺哈希、单张实测 142 ms（中位 0.25 MB 大图），全量约 27 分钟
——前端/反代先超时，用户看到「接口异常」，后端还在跑。现在：
- 扫描接口限时补算并回报 `missing` / `cache_complete`；
- 剩余缺口交给后台任务 `phash_backfill`（worker 执行，无请求超时约束）；
- 算不出哈希的行（文件缺失/损坏）必须排除，否则补算循环原地打转。
"""

import sqlite3
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.task import TaskQueue
from app.services import near_duplicate_service as nd
from app.services import task_runner
from app.services.task_runners import phash_backfill as pb


def _db_path():
    return settings.storage_root.parent / "fashion_inspo.db"


def _clear_phash(ids: list[str] | None = None) -> None:
    """把素材的 phash 置空（模拟历史素材：f2 导入路径不算 phash）。

    uploads 走的是「上传即算」还是「扫描时懒算」由实现决定，这里直接置空，
    保证用例测的是补算逻辑本身而不是上传路径的副作用。
    """
    conn = sqlite3.connect(str(_db_path()))
    try:
        if ids:
            marks = ",".join("?" * len(ids))
            conn.execute(f"UPDATE inspirations SET phash = NULL WHERE id IN ({marks})", ids)
        else:
            conn.execute("UPDATE inspirations SET phash = NULL")
        conn.commit()
    finally:
        conn.close()


def _phash_map() -> dict[str, str | None]:
    conn = sqlite3.connect(str(_db_path()))
    try:
        return {
            row[0]: row[1]
            for row in conn.execute("SELECT id, phash FROM inspirations WHERE deleted_at IS NULL")
        }
    finally:
        conn.close()


def _upload_images(upload, n: int = 3) -> list[str]:
    """上传 n 张内容不同的图片，返回素材 ID（内容不同 → 不会被内容去重拦掉）。"""
    ids: list[str] = []
    for _ in range(n):
        r = upload()
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


class TestScanBudget:
    async def test_budget_zero_returns_immediately_without_hashing(
        self, client, upload, monkeypatch
    ):
        """预算为 0（只看现状）：立刻返回、不算哈希、如实回报缺口。"""
        ids = _upload_images(upload, 3)
        _clear_phash(ids)
        monkeypatch.setattr(nd, "BACKFILL_TIME_BUDGET_SECONDS", 0.0)

        async with async_session() as db:
            result = await nd.scan_near_duplicates(db, limit=0, threshold=32)

        assert result["backfilled"] == 0
        assert result["missing"] == 3
        assert result["cache_complete"] is False
        assert _phash_map() == dict.fromkeys(ids)  # 一张都没写

    async def test_scan_fills_cache_and_reports_complete(self, client, upload):
        """预算充足：补完缓存并标记 complete；再扫一次零补算（幂等）。"""
        ids = _upload_images(upload, 3)
        _clear_phash(ids)

        async with async_session() as db:
            first = await nd.scan_near_duplicates(db, limit=0, threshold=32)

        assert first["backfilled"] == 3
        assert first["missing"] == 0
        assert first["cache_complete"] is True
        assert all(_phash_map()[i] for i in ids)

        async with async_session() as db:
            second = await nd.scan_near_duplicates(db, limit=0, threshold=32)
        assert second["backfilled"] == 0
        assert second["cache_complete"] is True

    async def test_unhashable_rows_do_not_loop_forever(self, client, upload, monkeypatch):
        """算不出哈希的行必须被排除：否则补算循环永远命中同一批（请求挂住）。

        回归：把 perceptual_hash 换成恒返回空（模拟文件缺失/损坏），即便给足预算
        也必须**返回**，并把这类行计入 unhashable。
        """
        ids = _upload_images(upload, 3)
        _clear_phash(ids)
        monkeypatch.setattr(nd, "perceptual_hash", lambda _path: "")

        async with async_session() as db:
            result = await nd.backfill_phash_cache(db, budget_seconds=30.0)

        assert result["computed"] == 0
        assert result["missing"] == 3
        assert result["complete"] is False
        assert result["unhashable"] == 3

    async def test_backfill_excludes_known_unhashable_and_finishes_rest(
        self, client, upload, monkeypatch
    ):
        """混合场景：1 张算不出、2 张正常 → 正常的补上，坏的不拖累整体。"""
        ids = _upload_images(upload, 3)
        _clear_phash(ids)
        # perceptual_hash 拿到的参数是**磁盘路径**，不是素材 id，故按路径区分好坏
        conn = sqlite3.connect(str(_db_path()))
        try:
            bad_id, bad_path = conn.execute(
                "SELECT id, file_path FROM inspirations WHERE id = ?", (ids[0],)
            ).fetchone()
        finally:
            conn.close()
        good = ids[1:]
        real = nd.perceptual_hash
        # 用文件名匹配：库里存的是 `images/2026-09/x.jpg`（正斜杠相对路径），
        # 传给 perceptual_hash 的是拼接后的绝对路径（Windows 反斜杠），直接比字符串不会命中
        bad_name = Path(bad_path).name
        monkeypatch.setattr(
            nd, "perceptual_hash", lambda p: "" if Path(str(p)).name == bad_name else real(p)
        )

        async with async_session() as db:
            result = await nd.backfill_phash_cache(db, budget_seconds=30.0)

        assert result["computed"] == 2
        assert result["missing"] == 1
        assert result["unhashable"] == 1
        cached = _phash_map()
        assert all(cached[i] for i in good)
        assert cached[bad_id] is None


    async def test_async_on_batch_callback_is_awaited(self, client, upload):
        """进度回调可以是协程函数，必须被 await——否则后台任务进度永远停在 0%。"""
        ids = _upload_images(upload, 3)
        _clear_phash(ids)
        seen: list[tuple[int, int]] = []

        async def _on_batch(computed: int, remaining: int) -> None:
            seen.append((computed, remaining))

        async with async_session() as db:
            result = await nd.backfill_phash_cache(db, budget_seconds=30.0, on_batch=_on_batch)

        assert result["computed"] == 3
        assert seen == [(3, 0)]  # 协程真的执行了，而不是被静默丢弃


class TestBackfillTask:
    async def test_task_fills_cache_and_is_idempotent(self, client, upload):
        """后台任务：一次性补齐 → 进度 100 / complete；再创建时缓存已完整 → 400。"""
        ids = _upload_images(upload, 3)
        _clear_phash(ids)

        r = client.post("/api/admin/phash-backfill")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 3 and body["reused"] is False
        task_id = body["task_id"]

        async with async_session() as db:
            task = await db.get(TaskQueue, task_id)
            assert task.type == "phash_backfill"
            await task_runner.execute_phash_backfill(db, task)
            await db.refresh(task)

        assert task.progress == 100
        assert task.result["complete"] is True
        assert task.result["computed"] == 3
        assert all(_phash_map()[i] for i in ids)

        # 缓存已完整：再次创建被拒（不需要重复跑）
        assert client.post("/api/admin/phash-backfill").status_code == 400

    async def test_running_task_is_reused_not_duplicated(self, client, upload):
        """已有进行中任务时不重复入队（避免两个 worker 同时算同一批）。"""
        ids = _upload_images(upload, 2)
        _clear_phash(ids)

        first = client.post("/api/admin/phash-backfill").json()
        second = client.post("/api/admin/phash-backfill")
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["reused"] is True
        assert body["task_id"] == first["task_id"]

        async with async_session() as db:
            rows = (
                await db.execute(
                    select(TaskQueue.id).where(TaskQueue.type == "phash_backfill")
                )
            ).all()
        assert len(rows) == 1  # 只入队一次

    async def test_task_records_unhashable_sample(self, client, upload, monkeypatch):
        """算不出哈希的素材：任务不假装成功，结果里给出缺口与样例。"""
        ids = _upload_images(upload, 2)
        _clear_phash(ids)
        monkeypatch.setattr(nd, "perceptual_hash", lambda _path: "")

        async with async_session() as db:
            created = await pb.create_phash_backfill_task(db)
            task = await db.get(TaskQueue, created["task_id"])
            await task_runner.execute_phash_backfill(db, task)
            await db.refresh(task)

        assert task.result["complete"] is False
        assert task.result["computed"] == 0
        assert task.result["unhashable"] == 2
        assert len(task.result["unhashable_sample"]) == 2
        assert task.progress < 100

    async def test_create_returns_zero_when_nothing_missing(self, client, upload):
        """缓存已完整：创建函数返回 task_id=None / total=0（路由据此返回 400）。"""
        _upload_images(upload, 1)  # 不置空 → 上传路径已算好哈希（或下面补一次）
        async with async_session() as db:
            await nd.backfill_phash_cache(db, budget_seconds=30.0)
            created = await pb.create_phash_backfill_task(db)
        assert created == {"task_id": None, "total": 0, "reused": False}
