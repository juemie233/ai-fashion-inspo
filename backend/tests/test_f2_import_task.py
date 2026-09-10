"""f2 一键获取素材任务（task type: f2_import）测试：API 创建 / 可用性 / 执行接线。

不真跑 f2、不真下抖音：下载阶段用 monkeypatch 关掉（fetch=False）或打桩子进程，
执行阶段用临时 f2 目录 + 项目自带的临时素材库（conftest 已隔离）。
"""

import asyncio
import json
from pathlib import Path

import pytest
from PIL import Image

from app.database import async_session
from app.services import task_runner
from scripts import import_f2_downloads as f2


def _jpeg(path: Path, color: str = "red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 48), color).save(path, "JPEG")
    return path


@pytest.fixture
def f2_tree(tmp_path, monkeypatch):
    """把 f2 的工作目录/下载目录指向临时目录，并放两个作品的文件。"""
    f2_dir = tmp_path / "f2proj"
    root = f2_dir / "Download" / "douyin" / "post"
    _jpeg(root / "里香1√" / "2025-01-01 10-00-00_#jk_标题_image_1.jpg")
    _jpeg(root / "里香1√" / "2025-01-01 10-00-00_#jk_标题_image_2.jpg", "blue")
    _jpeg(root / "某博主" / "2025-02-02 11-00-00_#通勤_标题_video.mp4".replace(".mp4", ".jpg"), "green")
    monkeypatch.setattr(f2, "DEFAULT_F2_DIR", f2_dir)
    monkeypatch.setattr(f2, "DEFAULT_F2_ROOT", root)
    return f2_dir, root


# ── 可用性检查 ──


def test_f2_import_status_reports_reason_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(f2, "DEFAULT_F2_DIR", tmp_path / "不存在")
    status = task_runner.f2_import_status()
    assert status["available"] is False
    assert "未找到 f2 工作目录" in status["reason"]


def test_f2_import_status_available_with_authors(tmp_path, monkeypatch):
    f2_dir = tmp_path / "f2proj"
    f2_dir.mkdir()
    import sqlite3

    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.execute("INSERT INTO user_info_web VALUES ('sec1', '里香1√', 171)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(f2, "DEFAULT_F2_DIR", f2_dir)
    monkeypatch.setattr(f2, "DEFAULT_F2_ROOT", f2_dir / "Download")

    status = task_runner.f2_import_status()
    assert status["available"] is True
    assert status["authors"] == 1
    assert "1 个" in status["reason"]


# ── API ──


def test_f2_status_endpoint(client):
    resp = client.get("/api/scraper/f2-status")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"available", "reason", "authors", "f2_dir", "root"}


def test_create_f2_import_task_endpoint(client):
    """POST 创建任务：返回 task_id，参数落进任务 result（供执行阶段读取）。"""
    resp = client.post(
        "/api/scraper/f2-import",
        params={"fetch": False, "authors": "里香,娜娜瑜", "limit": 50, "skip_live": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"]

    detail = client.get(f"/api/tasks/{body['task_id']}").json()
    assert detail["type"] == "f2_import"
    opts = detail["result"]
    assert opts["authors"] == ["里香", "娜娜瑜"]
    assert opts["limit"] == 50 and opts["skip_live"] is True and opts["fetch"] is False


def test_create_f2_import_rejects_when_unavailable(client, tmp_path, monkeypatch):
    """fetch=True 但环境不可用时：不建任务，返回原因（供前端提示）。"""
    monkeypatch.setattr(f2, "DEFAULT_F2_DIR", tmp_path / "不存在")
    resp = client.post("/api/scraper/f2-import", params={"fetch": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] is None
    assert "f2" in body["message"]


# ── 执行链路 ──


async def test_execute_f2_import_ingests_files_and_records_result(
    client, f2_tree, upload
):
    """执行（fetch=False）：扫描 → 去重 → 入库，进度与结果写回任务行。"""
    from app.models.task import TaskQueue

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=False)
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["plan"]["files"] == 3
        assert stored.result["import"]["imported"] == 3
        assert stored.result["import"]["failed"] == 0
        assert stored.progress == 100 and stored.done == 3
        assert stored.error is None

    # 素材已入库且**未打标**（导入不做标签分析）
    detail = client.get("/api/inspirations?size=50").json()
    f2_items = [
        item for item in detail["items"] if str(item["source_platform_id"]).startswith("f2:")
    ]
    assert len(f2_items) == 3
    for item in f2_items:
        assert item["source_type"] == "douyin"
        assert item["quality_status"] == "pending"

    # 批次清单落盘（可回滚）——落在 storage 根目录下，与素材/缩略图同根
    from app.config import settings

    batch_dir = settings.storage_root / f2.IMPORT_BATCH_DIRNAME
    batches = sorted(batch_dir.glob("*.json"))
    assert batches, "批次清单未落盘"
    payload = json.loads(batches[-1].read_text(encoding="utf-8"))
    assert len(payload["imported"]) == 3


async def test_execute_f2_import_second_run_is_idempotent(client, f2_tree):
    """重复执行：全部判重跳过，不再新增素材。"""
    from app.models.task import TaskQueue

    for _ in range(2):
        async with async_session() as db:
            task = await task_runner.create_f2_import_task(db, fetch=False)
            task_id = task.id
            await task_runner.execute_f2_import(db, task)
        async with async_session() as db:
            stored = await db.get(TaskQueue, task_id)
            if _ == 0:
                assert stored.result["import"]["imported"] == 3
            else:
                assert stored.result["plan"]["files"] == 0
                assert stored.result["import"]["imported"] == 0


async def test_execute_f2_import_fetch_requires_f2(monkeypatch, client, f2_tree):
    """fetch=True 但 f2 不可用时：抛错让任务失败（而不是静默导入旧文件）。"""
    monkeypatch.setattr(f2, "f2_available", lambda: False)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True)
        with pytest.raises(RuntimeError, match="未检测到 f2"):
            await task_runner.execute_f2_import(db, task)


async def test_execute_f2_import_author_filter(client, f2_tree):
    """--authors 归一化过滤：只导入指定作者的文件。"""
    from app.models.task import TaskQueue

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, authors=["里香"], fetch=False)
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["plan"]["files"] == 2  # 只导 里香1√ 的两张图


# ── 代码审查修复项的回归测试 ──


async def test_execute_f2_import_fails_when_all_downloads_fail(
    client, f2_tree, monkeypatch
):
    """修复（审查 M4）：所有作者下载都失败时必须让任务显式失败。

    回归点：cookie 失效时原先会以 success + 0 下载收尾，用户从任务中心
    看到「成功」而毫不知情。
    """
    from app.services.task_runners import f2_import as runner

    f2_dir, _root = f2_tree
    import sqlite3 as _sq

    conn = _sq.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.execute("INSERT INTO user_info_web VALUES ('sec1', '里香1√', 171)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(f2, "f2_available", lambda: True)
    monkeypatch.setattr(runner, "_run_subprocess", lambda cmd, cwd: 1)  # 每个作者都失败

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True)
        with pytest.raises(RuntimeError, match="下载全部失败"):
            await task_runner.execute_f2_import(db, task)


async def test_execute_f2_import_does_not_block_event_loop(
    client, f2_tree, monkeypatch
):
    """修复（审查 H1）：扫描 + 全量哈希必须在线程里跑。

    回归点：build_import_plan 同步执行时（实测 7.3 GB 约 81 秒）会阻塞 worker
    事件循环，心跳（10s/90s 阈值）停跳可能让运行中的任务被判 stale 并重复认领。
    这里用「慢扫描」模拟重活，断言事件循环在此期间仍能转。
    """
    import time

    f2_dir, _root = f2_tree
    marks: dict[str, float] = {}

    def slow_scan(root):
        marks["start"] = time.monotonic()
        time.sleep(0.5)
        marks["end"] = time.monotonic()
        return []

    monkeypatch.setattr(f2, "scan_directory", slow_scan)

    ticks: list[float] = []

    async def _ticker():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.02)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=False)
        ticker = asyncio.create_task(_ticker())
        try:
            await task_runner.execute_f2_import(db, task)
        finally:
            ticker.cancel()

    during = [t for t in ticks if marks["start"] <= t <= marks["end"]]
    assert len(during) >= 5, f"扫描期间事件循环仅转了 {len(during)} 次，疑似被阻塞"


def test_create_f2_import_reuses_running_task(client):
    """修复（审查 L4）：已有进行中的任务时直接复用，避免连点起多个任务。"""
    first = client.post("/api/scraper/f2-import", params={"fetch": False}).json()
    second = client.post("/api/scraper/f2-import", params={"fetch": False}).json()

    assert first["task_id"] and second["task_id"] == first["task_id"]
    assert second.get("reused") is True
    assert "进行中" in second["message"]
