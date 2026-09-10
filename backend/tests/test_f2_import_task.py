"""f2 一键获取素材任务（task type: f2_import）测试：API 创建 / 可用性 / 执行接线。

不真跑 f2、不真下抖音：下载阶段用 monkeypatch 关掉（fetch=False）或打桩子进程，
执行阶段用临时 f2 目录 + 项目自带的临时素材库（conftest 已隔离）。
"""

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image

from app.database import async_session
from app.services import task_runner
from app.services.task_runners.common import utcnow
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
    assert set(body) >= {"available", "reason", "authors", "f2_dir", "root", "auto"}
    assert set(body["auto"]) >= {
        "enabled",
        "interval_hours",
        "available",
        "last_task_at",
        "next_due_at",
        "running_task_id",
    }


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


def test_create_f2_import_task_endpoint_accepts_since_days(client):
    """日期窗口天数透传到任务参数；越界由 FastAPI 校验拦住。"""
    body = client.post(
        "/api/scraper/f2-import", params={"fetch": False, "since_days": 3}
    ).json()
    opts = client.get(f"/api/tasks/{body['task_id']}").json()["result"]
    assert opts["since_days"] == 3

    assert (
        client.post("/api/scraper/f2-import", params={"fetch": False, "since_days": -1}).status_code
        == 422
    )


async def test_execute_f2_import_fetch_uses_date_window(client, f2_tree, monkeypatch):
    """P0 提速回归：执行阶段必须给 f2 传日期窗口，而不是 `-i all`。

    回归点：`-i all` 时 f2 不设 min_cursor，「翻到范围起点就 break」永不触发，
    会把作者全部历史翻一遍，而每页固定 sleep 一次 timeout（实测单作者 263 秒里
    220 秒花在翻页等待上，真正下载只有 36 个文件）。
    """
    import sqlite3 as _sq

    from app.services.task_runners import f2_import as runner

    f2_dir, _root = f2_tree
    conn = _sq.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.execute("INSERT INTO user_info_web VALUES ('sec1', '里香1√', 171)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(f2, "f2_available", lambda: True)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        runner, "_run_subprocess", lambda cmd, cwd: (commands.append(cmd) or 0)
    )

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True, since_days=7)
        await task_runner.execute_f2_import(db, task)

    assert commands, "没有调用 f2"
    interval = commands[0][commands[0].index("-i") + 1]
    assert interval != "all"
    assert interval.endswith(f"|{utcnow().date():%Y-%m-%d}")
    assert interval.startswith(f"{(utcnow() - timedelta(days=7)).date():%Y-%m-%d}")


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


async def test_execute_f2_import_second_run_uses_hash_cache(client, f2_tree):
    """P0 回归：第二次运行的哈希全部命中落盘缓存（不再整棵下载树重算 SHA-256）。

    回归点：规划阶段原先每次都对 15,350 文件 / 7.28 GB 重算（实测约 81 秒），
    开了每日自动获取后变成每天固定成本，且随下载量线性增长。
    """
    from app.models.task import TaskQueue

    first_stats = second_stats = None
    first_seconds = second_seconds = 0.0
    for _ in range(2):
        async with async_session() as db:
            task = await task_runner.create_f2_import_task(db, fetch=False)
            task_id = task.id
            await task_runner.execute_f2_import(db, task)
        async with async_session() as db:
            stored = await db.get(TaskQueue, task_id)
            plan = stored.result["plan"]
            if _ == 0:
                first_stats, first_seconds = plan["hash_cache"], plan["seconds"]
            else:
                second_stats, second_seconds = plan["hash_cache"], plan["seconds"]

    assert first_stats["computed"] == 3 and first_stats["hit"] == 0
    assert second_stats["computed"] == 0 and second_stats["hit"] == 3
    # 缓存文件跨任务共享（同一 storage 根），条数只增不减
    assert second_stats["cached_rows"] >= 3
    assert first_seconds >= 0 and second_seconds >= 0  # 规划耗时落进任务结果
    assert second_stats["error"] == ""


async def test_execute_f2_import_does_not_resurrect_trashed_material(client, f2_tree):
    """P0 回归：丢进垃圾桶的素材不会被下次导入搬回来。

    回归点：判重原先只看未删除素材（deleted_at IS NULL），垃圾桶内容算「可重新入库」。
    开启每日自动获取后这会变成「每天自动复活一次」，与垃圾桶=负样本的设计冲突。
    """
    from app.models.task import TaskQueue

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=False)
        await task_runner.execute_f2_import(db, task)

    items = client.get("/api/inspirations?size=50").json()["items"]
    f2_items = [i for i in items if str(i["source_platform_id"]).startswith("f2:")]
    assert len(f2_items) == 3
    victim = f2_items[0]

    resp = client.post(f"/api/inspirations/{victim['id']}/trash", json={"reason": "重复"})
    assert resp.status_code == 200
    assert resp.json()["deleted_at"] is not None
    # 移入垃圾桶后默认列表不再返回它
    left = client.get("/api/inspirations?size=50").json()["items"]
    assert victim["id"] not in {i["id"] for i in left}

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=False)
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["plan"]["files"] == 0  # 三条全部跳过
        assert stored.result["import"]["imported"] == 0
        assert stored.result["plan"]["skipped"]["已在垃圾桶（不重新导入）"] == 1

    # 垃圾桶里那条仍在垃圾桶（没有被复活）
    trash_ids = {i["id"] for i in client.get("/api/inspirations/trash").json()["items"]}
    assert victim["id"] in trash_ids


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


# ── 每日自动获取（方案 A：每日新增作品自动入库）──


def _make_author_db(f2_dir: Path) -> None:
    """在临时 f2 工作目录建最小 user_info_web 表（load_f2_authors 的唯一数据源）。"""
    import sqlite3

    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.execute("INSERT INTO user_info_web VALUES ('sec1', '里香1√', 171)")
    conn.commit()
    conn.close()


@pytest.fixture
def auto_env(f2_tree, monkeypatch):
    """可用的自动获取环境：f2 视为已安装 + 作者库 1 个作者。"""
    f2_dir, _root = f2_tree
    _make_author_db(f2_dir)
    monkeypatch.setattr(f2, "f2_available", lambda: True)
    return f2_dir


@pytest.fixture
def auto_settings(monkeypatch):
    """隔离三个自动获取配置项：用例结束后还原（含 API 直接改 settings 的情况）。"""
    from app.config import settings

    original = (
        settings.f2_import_auto_enabled,
        settings.f2_import_interval_hours,
        settings.f2_import_auto_skip_live,
    )
    yield settings
    (
        settings.f2_import_auto_enabled,
        settings.f2_import_interval_hours,
        settings.f2_import_auto_skip_live,
    ) = original


async def _count_f2_tasks(db) -> int:
    from sqlalchemy import func, select

    from app.models.task import TaskQueue

    return (
        await db.execute(
            select(func.count()).select_from(TaskQueue).where(TaskQueue.type == "f2_import")
        )
    ).scalar() or 0


async def test_get_f2_auto_status_defaults(client, auto_settings):
    """默认关闭、无历史任务：下次到期时间未知（调度循环会立即触发）。"""
    auto_settings.f2_import_auto_enabled = False
    auto_settings.f2_import_interval_hours = 24

    async with async_session() as db:
        status = await task_runner.get_f2_auto_status(db)

    assert status["enabled"] is False
    assert status["interval_hours"] == 24
    assert status["last_task_at"] is None
    assert status["next_due_at"] is None
    assert status["running_task_id"] is None


async def test_get_f2_auto_status_reports_last_and_next(client, auto_settings):
    """有历史任务后：给出上次运行时间与「上次 + 间隔」的下次到期时间。"""
    from datetime import datetime, timedelta

    auto_settings.f2_import_interval_hours = 6
    client.post("/api/scraper/f2-import", params={"fetch": False})

    async with async_session() as db:
        status = await task_runner.get_f2_auto_status(db)

    last = datetime.fromisoformat(status["last_task_at"])
    nxt = datetime.fromisoformat(status["next_due_at"])
    assert nxt - last == timedelta(hours=6)
    # 手动创建的任务同样算作「上次运行」，并占用进行中标记（不会重复触发）
    assert status["running_task_id"] is not None


async def test_maybe_schedule_skips_when_disabled(client, auto_settings):
    """开关关闭：不创建任何任务（用户偏好手动确认）。"""
    auto_settings.f2_import_auto_enabled = False

    async with async_session() as db:
        assert await task_runner.maybe_schedule_auto_import(db) is None
        assert await _count_f2_tasks(db) == 0


async def test_maybe_schedule_creates_task_when_due(client, auto_env, auto_settings):
    """开启 + 环境可用 + 无历史：创建任务，且自动任务总是先增量下载。"""
    from app.models.task import TaskQueue

    auto_settings.f2_import_auto_enabled = True
    auto_settings.f2_import_interval_hours = 24

    async with async_session() as db:
        task_id = await task_runner.maybe_schedule_auto_import(db)
        assert task_id is not None
        task = await db.get(TaskQueue, task_id)
        assert task.type == "f2_import"
        assert task.result["fetch"] is True

        # 刚创建的任务处于 pending（进行中）：本轮不重复触发
        assert await task_runner.maybe_schedule_auto_import(db) is None
        assert await _count_f2_tasks(db) == 1


async def test_maybe_schedule_respects_interval(client, auto_env, auto_settings):
    """间隔判定：未到间隔跳过，已过间隔才创建（锚点是最近任务的创建时间）。"""
    from datetime import timedelta

    from app.services.task_runners.common import utcnow

    auto_settings.f2_import_auto_enabled = True
    auto_settings.f2_import_interval_hours = 1

    async with async_session() as db:
        first = await task_runner.create_f2_import_task(db, fetch=True)
        first.status = "success"  # 已收尾，不再阻塞下一轮
        first.created_at = utcnow() - timedelta(minutes=30)
        await db.commit()

        assert await task_runner.maybe_schedule_auto_import(db) is None

        first.created_at = utcnow() - timedelta(hours=2)
        await db.commit()

        new_id = await task_runner.maybe_schedule_auto_import(db)
        assert new_id is not None and new_id != first.id


async def test_maybe_schedule_skips_when_f2_unavailable(
    client, f2_tree, auto_settings, monkeypatch
):
    """环境不可用（f2 未装）：跳过并留痕，不制造失败任务。"""
    monkeypatch.setattr(f2, "f2_available", lambda: False)
    auto_settings.f2_import_auto_enabled = True

    async with async_session() as db:
        assert await task_runner.maybe_schedule_auto_import(db) is None
        assert await _count_f2_tasks(db) == 0


def test_f2_auto_endpoint_switches_and_persists(client, auto_settings, monkeypatch):
    """PUT /f2-auto：改配置 + 写 .env（测试里打桩落盘，不碰真实 .env）。"""
    saved: dict[str, str] = {}

    async def fake_update(updates):
        saved.update(updates)

    monkeypatch.setattr("app.routers.ai_shared._update_env_file", fake_update)

    resp = client.put(
        "/api/scraper/f2-auto",
        params={"enabled": True, "interval_hours": 12, "skip_live": True},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["auto"]["enabled"] is True
    assert body["auto"]["interval_hours"] == 12
    assert body["auto"]["skip_live"] is True
    assert "12" in body["message"]
    assert saved == {
        "F2_IMPORT_AUTO_ENABLED": "true",
        "F2_IMPORT_INTERVAL_HOURS": "12",
        "F2_IMPORT_AUTO_SKIP_LIVE": "true",
    }
    assert auto_settings.f2_import_auto_enabled is True
    assert auto_settings.f2_import_interval_hours == 12

    # 关闭：只改开关，间隔保持不变
    off = client.put("/api/scraper/f2-auto", params={"enabled": False}).json()
    assert off["auto"]["enabled"] is False
    assert off["auto"]["interval_hours"] == 12
    assert saved["F2_IMPORT_AUTO_ENABLED"] == "false"


def test_f2_auto_endpoint_rejects_out_of_range_interval(client, auto_settings):
    """间隔越界由 FastAPI 参数校验拦住（1~720 小时）。"""
    resp = client.put("/api/scraper/f2-auto", params={"enabled": True, "interval_hours": 0})
    assert resp.status_code == 422
