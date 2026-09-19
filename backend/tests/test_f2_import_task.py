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
# 注意：f2_import_status 的分支顺序是「f2 是否安装 → 工作目录是否存在 → 作者库」，
# 而 f2_available() 取决于**运行环境是否装了 f2**（本机装了、CI 没装）。因此本组
# 用例一律显式打桩 f2_available，否则在 CI（无 f2）会先命中「未检测到 f2」分支。


def test_f2_import_status_reports_missing_f2(monkeypatch):
    """f2 未安装：给出安装指引（CI 等未装 f2 的环境走的就是这条分支）。"""
    monkeypatch.setattr(f2, "f2_available", lambda: False)

    status = task_runner.f2_import_status()

    assert status["available"] is False
    assert "未检测到 f2" in status["reason"]


def test_f2_import_status_reports_reason_when_unavailable(tmp_path, monkeypatch):
    """f2 已装但工作目录不存在：报「未找到 f2 工作目录」而不是安装指引。"""
    monkeypatch.setattr(f2, "f2_available", lambda: True)
    monkeypatch.setattr(f2, "DEFAULT_F2_DIR", tmp_path / "不存在")

    status = task_runner.f2_import_status()

    assert status["available"] is False
    assert "未找到 f2 工作目录" in status["reason"]


def test_f2_import_status_available_with_authors(tmp_path, monkeypatch):
    """f2 已装 + 目录存在 + 作者库有账号：可用。"""
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
    monkeypatch.setattr(f2, "f2_available", lambda: True)
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
        # 结构化计数：前端不必再去匹配中文跳过原因文案
        assert stored.result["plan"]["trash_skipped"] == 1

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


async def test_execute_f2_import_records_stage(client, f2_tree):
    """阶段标记（方案 D）：done/total 在下载阶段是作者数、入库阶段是文件数，
    前端靠 result.stage 才能把进度讲清楚（1% 挂在几分钟时用户要知道在干什么）。
    """
    from app.models.task import TaskQueue

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=False)
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        # 执行完 → done；且期间一定写过 import（计划算完就写）
        assert stored.result["stage"] == "done"
        assert stored.result["plan"]["files"] == 3


async def test_execute_f2_import_marks_download_stage_before_import(
    client, f2_tree, monkeypatch
):
    """下载阶段先把 stage 写成 download，避免前端把作者数当成文件数解释。"""
    import sqlite3 as _sq

    from app.models.task import TaskQueue
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
    seen: list[str] = []

    async def fake_status(_db, task_id):
        """在下载阶段（第一个作者）偷看一眼落库的 stage。"""
        if not seen:
            async with async_session() as probe:
                row = await probe.get(TaskQueue, task_id)
                seen.append(str((row.result or {}).get("stage")))
        return "running"

    monkeypatch.setattr(runner, "_current_status", fake_status)
    monkeypatch.setattr(runner, "_run_subprocess", lambda cmd, cwd: 0)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True)
        await task_runner.execute_f2_import(db, task)

    assert seen == ["download"]


async def test_get_f2_auto_status_exposes_running_stage(client, auto_settings):
    """卡片要能显示「正在执行 #N（下载中：第 3/21 个作者）」，故 auto.running 必须有阶段与计数。"""
    auto_settings.f2_import_auto_enabled = False
    body = client.post("/api/scraper/f2-import", params={"fetch": False}).json()
    task_id = body["task_id"]

    async with async_session() as db:
        status = await task_runner.get_f2_auto_status(db)

    running = status["running"]
    assert running is not None
    assert running["id"] == task_id
    assert running["status"] == "pending"
    assert set(running) >= {"id", "status", "progress", "done", "total", "stage"}


async def test_get_f2_auto_status_running_is_none_without_task(client, auto_settings):
    """没有任务在跑时不下发 running（卡片据此停掉轮询）。"""
    auto_settings.f2_import_auto_enabled = False

    async with async_session() as db:
        status = await task_runner.get_f2_auto_status(db)

    assert status["running"] is None and status["running_task_id"] is None


def test_f2_status_endpoint_includes_running_brief(client):
    """GET /f2-status 的 auto.running 字段存在（前端类型依赖它）。"""
    auto = client.get("/api/scraper/f2-status").json()["auto"]
    assert "running" in auto


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
    """隔离自动获取与「我的喜欢」相关配置项：用例结束后还原（含 API 直接改 settings 的情况）。"""
    from app.config import settings

    original = (
        settings.f2_import_auto_enabled,
        settings.f2_import_interval_hours,
        settings.f2_import_auto_skip_live,
        settings.f2_fetch_since_days,
        settings.f2_like_user,
    )
    yield settings
    (
        settings.f2_import_auto_enabled,
        settings.f2_import_interval_hours,
        settings.f2_import_auto_skip_live,
        settings.f2_fetch_since_days,
        settings.f2_like_user,
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


# ── 审查修复：可中断（取消/暂停）与并发保护 ──


def test_f2_status_endpoint_reports_default_window(client, auto_settings):
    """默认日期窗口随状态下发：前端据此填初值，不再硬编码 14 把 .env 配置顶掉。"""
    auto_settings.f2_fetch_since_days = 21

    body = client.get("/api/scraper/f2-status").json()
    assert body["fetch_since_days"] == 21


async def test_cancel_running_f2_import_via_api(client):
    """运行中的 f2_import 可以从任务中心取消（执行器早已实现停止逻辑，缺的是入口）。

    回归点：f2_import 不在 _CANCELABLE_RUNNING_TYPES 里，取消接口对运行中的它
    返回 400，删除接口又因心跳正常拒绝——下载可能跑几十分钟，用户完全无法中断。
    """
    from app.models.task import TaskQueue

    task_id = client.post("/api/scraper/f2-import", params={"fetch": False}).json()["task_id"]
    async with async_session() as db:
        (await db.get(TaskQueue, task_id)).status = "running"
        await db.commit()

    resp = client.post(f"/api/tasks/{task_id}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "任务已取消"
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "cancelled"


async def test_pause_and_resume_running_f2_import(client):
    """暂停 → paused（已下载/已入库产物保留），恢复 → pending 由 worker 幂等续算。"""
    from app.models.task import TaskQueue

    task_id = client.post("/api/scraper/f2-import", params={"fetch": False}).json()["task_id"]
    async with async_session() as db:
        (await db.get(TaskQueue, task_id)).status = "running"
        await db.commit()

    assert client.post(f"/api/tasks/{task_id}/pause").status_code == 200
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "paused"

    assert client.post(f"/api/tasks/{task_id}/resume").status_code == 200
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "pending"


async def test_execute_f2_import_cancel_during_download_skips_scan(
    client, auto_env, monkeypatch
):
    """取消发生在下载阶段：立即收尾，不再做重活扫描。

    回归点：原先 break 之后仍会跑完整扫描（全量实测约 81 秒）才停下，用户点了
    取消却迟迟没反应；顺带确认重跑不会把上一次的产物字段带进新结果。
    """
    from app.models.task import TaskQueue
    from app.services.task_runners import f2_import as runner

    monkeypatch.setattr(runner, "_run_subprocess", lambda cmd, cwd: 0)

    async def cancelled(_db, _task_id):
        return "cancelled"

    monkeypatch.setattr(runner, "_current_status", cancelled)

    def no_scan(_root):
        raise AssertionError("下载被取消后不应再扫描目录")

    monkeypatch.setattr(f2, "scan_directory", no_scan)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True)
        task_id = task.id
        # 上一次运行（暂停前）残留在 result 里的产物字段
        task.result = {**task.result, "plan": {"files": 99}, "import": {"imported": 99}}
        await db.commit()
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.status == "cancelled"
        assert stored.result["fetch"]["aborted"] is True
        # 旧产物不残留：opts 只取创建参数，界面不会出现自相矛盾的统计
        assert "plan" not in stored.result and "import" not in stored.result


async def test_execute_f2_import_paused_keeps_progress_below_full(
    client, f2_tree, monkeypatch
):
    """暂停发生在入库阶段：任务保持 paused，进度停在当前值（不写成 100%）。

    回归点：中断的任务原先也写 progress=100，任务中心里「已暂停」看起来像跑完了。
    """
    from app.models.task import TaskQueue
    from app.services.task_runners import f2_import as runner

    def fake_apply(decisions, **kwargs):
        # 线程内的停止标记已生效（watcher 会把 paused 转成 stop）
        return {"imported": 0, "failed": 0, "batch_file": "", "ids": []}

    monkeypatch.setattr(f2, "apply_import", fake_apply)

    async def paused(_db, _task_id):
        return "paused"

    monkeypatch.setattr(runner, "_current_status", paused)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=False)
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.status == "paused"
        assert stored.progress < 100
        assert stored.result["stage"] == "done"


async def test_create_f2_import_task_if_idle_reuses_running(client):
    """并发保护入口：已有进行中任务时返回其 id、不新建。"""
    first = client.post("/api/scraper/f2-import", params={"fetch": False}).json()["task_id"]

    async with async_session() as db:
        task, running_id = await task_runner.create_f2_import_task_if_idle(db, fetch=False)

    assert task is None and running_id == first


async def test_concurrent_creation_creates_single_task(client):
    """自动调度与手动点击同时通过检查时，也只应创建一个任务（进程内锁）。"""

    async def _one():
        async with async_session() as db:
            return await task_runner.create_f2_import_task_if_idle(db, fetch=False)

    results = await asyncio.gather(_one(), _one(), _one())
    assert sum(1 for task, _rid in results if task is not None) == 1

    async with async_session() as db:
        assert await _count_f2_tasks(db) == 1


# ── 未登记账号过滤（f2 用户库混进无关账号：网易第五人格事故）──


def test_f2_status_marks_unregistered_f2_accounts(
    client, f2_tree, create_blogger, monkeypatch
):
    """状态里要列出「f2 有、库里没有」的账号：卡片据此提示默认跳过它们。"""
    import sqlite3 as _sq

    f2_dir, _root = f2_tree
    create_blogger("里香", platform="douyin")
    _make_author_db(f2_dir)  # 插入 里香1√
    conn = _sq.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute("INSERT INTO user_info_web VALUES ('sec_game', '网易第五人格', 171)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(f2, "f2_available", lambda: True)

    status = task_runner.f2_import_status()

    assert status["available"] is True
    assert status["authors"] == 1  # 只算已登记的那个
    assert status["unknown_authors"] == ["网易第五人格"]
    assert "1 个" in status["reason"] and "未登记" in status["reason"]


def test_create_f2_import_passes_include_unknown(client):
    """高级选项「包含未登记账号」要能透传到任务参数（执行阶段据此放开过滤）。"""
    body = client.post(
        "/api/scraper/f2-import",
        params={"fetch": False, "include_unknown_authors": True},
    ).json()

    opts = client.get(f"/api/tasks/{body['task_id']}").json()["result"]

    assert opts["include_unknown_authors"] is True


async def test_execute_f2_import_skips_unregistered_author_dirs(
    client, f2_tree, create_blogger
):
    """回归：库里只登记了 里香 时，下载目录里别的作者目录不入库。

    事故现场：f2 用户库混进「网易第五人格」官方号，被一键获取原样下载并入库 142 条
    （0 条博主绑定），而界面写的是「增量下载已采集博主的新作品」。
    """
    from app.models.task import TaskQueue

    create_blogger("里香", platform="douyin")

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=False)
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["plan"]["files"] == 2  # 只有 里香1√ 的两张图
        skipped = stored.result["plan"]["skipped"]
        assert skipped["作者不在指定范围（--authors / 已登记博主）"] == 1
        assert stored.result["import"]["imported"] == 2


async def test_execute_f2_import_include_unknown_restores_old_scope(
    client, f2_tree, create_blogger
):
    """勾选「包含未登记账号」后退回旧口径：目录里的其他作者照常入库。"""
    from app.models.task import TaskQueue

    create_blogger("里香", platform="douyin")

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=False, include_unknown_authors=True
        )
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["plan"]["files"] == 3
        assert stored.result["import"]["imported"] == 3


async def test_execute_f2_import_download_skips_unregistered_accounts(
    client, f2_tree, create_blogger, monkeypatch
):
    """下载阶段同样按白名单收窄：未登记账号不会被调 f2 子进程（省流量 + 免风控）。"""
    import sqlite3 as _sq

    from app.models.task import TaskQueue
    from app.services.task_runners import f2_import as runner

    f2_dir, _root = f2_tree
    create_blogger("里香", platform="douyin")
    _make_author_db(f2_dir)
    conn = _sq.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute("INSERT INTO user_info_web VALUES ('sec_game', '网易第五人格', 171)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(f2, "f2_available", lambda: True)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        runner, "_run_subprocess", lambda cmd, cwd: (commands.append(cmd) or 0)
    )

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True)
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["fetch"]["total"] == 1
        assert stored.result["fetch"]["skipped_authors"] == ["网易第五人格"]

    assert len(commands) == 1
    assert all("sec_game" not in " ".join(cmd) for cmd in commands)


# ── 结果浏览与审查（按批次清单定位本批素材）──


async def _import_once(fetch: bool = False) -> int:
    """跑一次 f2 导入（临时目录 + 临时库），返回任务 id。"""
    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=fetch)
        task_id = task.id
        await task_runner.execute_f2_import(db, task)
    return task_id


async def test_f2_task_results_lists_batch(client, f2_tree):
    """浏览本批结果：条目来自批次清单，状态（在库/垃圾桶/已彻底删除）以库内现状为准。"""
    task_id = await _import_once()

    resp = client.get(f"/api/scraper/f2-tasks/{task_id}/results")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_batch"] is True
    assert data["batch_id"]
    assert data["counts"] == {
        "total": 3,
        "live": 3,
        "trash": 0,
        "gone": 0,
        "pending": 3,
        "approved": 0,
        "rejected": 0,
    }
    assert data["total"] == 3
    assert len(data["items"]) == 3
    first = data["items"][0]
    assert {
        "id",
        "state",
        "quality_status",
        "media_type",
        "caption",
        "author",
        "file_path",
        "thumbnail_path",
        "trash_reason",
    } <= set(first)
    assert first["state"] == "pending"
    # 实际文件路径以库内为准（清单路径只作备份）；f2 树里有图也有视频
    assert first["file_path"].split("/", 1)[0] in {"images", "videos", "trash"}
    # 作者维度：两个目录 → 至少 2 个作者可筛
    assert sum(a["count"] for a in data["authors"]) == 3


async def test_f2_task_results_filters(client, f2_tree):
    """筛选口径：状态与作者都能收窄，且 total 随筛选变化。"""
    task_id = await _import_once()
    data = client.get(f"/api/scraper/f2-tasks/{task_id}/results").json()
    author = data["authors"][0]["name"]

    only_author = client.get(
        f"/api/scraper/f2-tasks/{task_id}/results", params={"author": author}
    ).json()
    assert only_author["total"] == data["authors"][0]["count"]
    assert {i["author"] for i in only_author["items"]} == {author}

    assert (
        client.get(
            f"/api/scraper/f2-tasks/{task_id}/results", params={"state": "trash"}
        ).json()["total"]
        == 0
    )
    assert (
        client.get(
            f"/api/scraper/f2-tasks/{task_id}/results", params={"state": "不支持"}
        ).status_code
        == 400
    )


async def test_f2_task_results_trash_then_restore(client, f2_tree):
    """审查动作：移入垃圾桶（软删除）→ 计数与筛选随之变化 → 还原回素材库。"""
    task_id = await _import_once()
    ids = [i["id"] for i in client.get(f"/api/scraper/f2-tasks/{task_id}/results").json()["items"]]

    resp = client.post(
        f"/api/scraper/f2-tasks/{task_id}/results/trash",
        json={"ids": ids[:2], "reason": "质量差"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"requested": 2, "trashed": 2, "skipped": 0}

    trashed = client.get(
        f"/api/scraper/f2-tasks/{task_id}/results", params={"state": "trash"}
    ).json()
    assert trashed["total"] == 2
    assert trashed["counts"]["trash"] == 2 and trashed["counts"]["live"] == 1
    assert {i["trash_reason"] for i in trashed["items"]} == {"质量差"}
    # 已不在素材库列表（与素材库口径一致）
    assert all(i["id"] not in {x["id"] for x in client.get("/api/inspirations?size=50").json()["items"]} for i in trashed["items"])

    resp = client.post(
        f"/api/scraper/f2-tasks/{task_id}/results/restore", json={"ids": ids[:2]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["restored"] == 2

    back = client.get(f"/api/scraper/f2-tasks/{task_id}/results").json()
    assert back["counts"]["trash"] == 0 and back["counts"]["live"] == 3


async def test_f2_task_results_trash_rejects_bad_reason(client, f2_tree):
    """删除原因必须是素材库枚举之一（状态机口径统一，不另开后门）。"""
    task_id = await _import_once()
    item_id = client.get(f"/api/scraper/f2-tasks/{task_id}/results").json()["items"][0]["id"]

    resp = client.post(
        f"/api/scraper/f2-tasks/{task_id}/results/trash",
        json={"ids": [item_id], "reason": "随便写的"},
    )

    assert resp.status_code == 400
    assert "删除原因" in resp.json()["detail"]


async def test_f2_task_results_ignores_ids_outside_batch(client, f2_tree):
    """只允许操作属于本批的素材：批次外的 ID 被忽略，全为批次外时 400。"""
    task_id = await _import_once()
    item_id = client.get(f"/api/scraper/f2-tasks/{task_id}/results").json()["items"][0]["id"]

    mixed = client.post(
        f"/api/scraper/f2-tasks/{task_id}/results/trash",
        json={"ids": [item_id, "不在本批的素材"], "reason": "重复"},
    )
    assert mixed.status_code == 200
    assert mixed.json() == {"requested": 2, "trashed": 1, "skipped": 1}

    foreign = client.post(
        f"/api/scraper/f2-tasks/{task_id}/results/restore", json={"ids": ["不在本批的素材"]}
    )
    assert foreign.status_code == 400


async def test_f2_task_results_delete_creates_batch_delete_task(client, f2_tree):
    """彻底删除：立即返回 batch_delete 任务（worker 执行物理删除），并留审计。"""
    from sqlalchemy import select

    from app.models.audit import AuditLog
    from app.models.task import TaskQueue

    task_id = await _import_once()
    ids = [i["id"] for i in client.get(f"/api/scraper/f2-tasks/{task_id}/results").json()["items"]]

    resp = client.post(f"/api/scraper/f2-tasks/{task_id}/results/delete", json={"ids": ids})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 3 and body["task_id"]
    async with async_session() as db:
        delete_task = await db.get(TaskQueue, body["task_id"])
        assert delete_task.type == "batch_delete"
        assert set(delete_task.result["inspiration_ids"]) == set(ids)
        audits = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "batch_delete")
            )
        ).scalars().all()
        assert audits and audits[-1].count == 3


async def test_f2_task_results_without_batch(client):
    """还没跑完入库（没有批次清单）的任务：不给结果入口，返回空批次而非报错。"""
    body = client.post("/api/scraper/f2-import", params={"fetch": False}).json()

    data = client.get(f"/api/scraper/f2-tasks/{body['task_id']}/results").json()

    assert data["has_batch"] is False
    assert data["items"] == [] and data["total"] == 0
    assert data["counts"]["total"] == 0


async def test_f2_task_results_404_for_other_task_type(client):
    """非 f2 导入任务（如批量删除）不能借这个接口浏览结果。"""
    async with async_session() as db:
        other = await task_runner.create_batch_delete_task(db, ["x"], label="t")
        other_id = other.id

    assert client.get(f"/api/scraper/f2-tasks/{other_id}/results").status_code == 404
    assert client.get("/api/scraper/f2-tasks/999999/results").status_code == 404


# ── 「我的喜欢」（点赞模式）──


@pytest.fixture
def f2_like_tree(tmp_path, monkeypatch):
    """把「我的喜欢」产物根指向临时目录：一个作品两张图，文件名带原作者前缀。"""
    like_root = tmp_path / "like"
    author_dir = like_root / "我的账号"  # f2 把喜欢的作品统统下在「我的昵称」目录下
    _jpeg(author_dir / "不养羊_2026-09-14 10-31-14_下一站再见吧#地铁jk_#jk_image_1.jpg")
    _jpeg(author_dir / "不养羊_2026-09-14 10-31-14_下一站再见吧#地铁jk_#jk_image_2.jpg", "blue")
    monkeypatch.setattr(f2, "DEFAULT_F2_LIKE_ROOT", like_root)
    return like_root


def _stub_like_fetch(monkeypatch, calls: dict | None = None):
    """打桩 f2 的点赞抓取：只记录入参并返回成功，不真的跑 f2。"""
    monkeypatch.setattr(f2, "f2_available", lambda: True)

    def _fake(f2_dir, like_user, download_root=None, **kwargs):
        if calls is not None:
            calls["like_user"] = like_user
            calls["download_root"] = str(download_root)
        return {
            "total": 1,
            "ok": 1,
            "failed": 0,
            "results": [{"nickname": "我的喜欢", "rc": 0, "cmd": "f2 dy -M like"}],
        }

    monkeypatch.setattr(f2, "run_fetch_likes", _fake)


def test_create_f2_import_like_mode_passes_params(client):
    """API 透传 mode/like_user（任务参数里能查到，执行阶段据此走点赞链路）。"""
    body = client.post(
        "/api/scraper/f2-import",
        params={"fetch": False, "mode": "like", "like_user": "MS4wLjABAAAAme"},
    ).json()

    opts = client.get(f"/api/tasks/{body['task_id']}").json()["result"]

    assert opts["fetch_mode"] == "like"
    assert opts["like_user"] == "MS4wLjABAAAAme"


def test_f2_like_user_endpoint_persists_and_rejects_bad_input(client, auto_settings, monkeypatch):
    """「我的主页链接」保存：归一成链接 + 写 .env + 状态回读；非法输入 400。"""
    saved: dict[str, str] = {}

    async def fake_update(updates):
        saved.update(updates)

    monkeypatch.setattr("app.routers.ai_shared._update_env_file", fake_update)

    body = client.put(
        "/api/scraper/f2-like-user", params={"like_user": "MS4wLjABAAAAme"}
    ).json()

    assert body["like_user"] == "https://www.douyin.com/user/MS4wLjABAAAAme"
    assert saved == {"F2_LIKE_USER": "https://www.douyin.com/user/MS4wLjABAAAAme"}
    # 状态接口回读，前端据此回填输入框
    assert client.get("/api/scraper/f2-status").json()["like_user"] == body["like_user"]

    assert (
        client.put("/api/scraper/f2-like-user", params={"like_user": "我 的主页"}).status_code
        == 400
    )

    cleared = client.put("/api/scraper/f2-like-user", params={"like_user": ""}).json()
    assert cleared["like_user"] == "" and saved["F2_LIKE_USER"] == ""


async def test_execute_f2_import_like_mode_imports_by_original_author(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """点赞端到端：作者取自文件名前缀，素材归到原作者而不是「我的账号」。"""
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    calls: dict = {}
    _stub_like_fetch(monkeypatch, calls)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True, fetch_mode="like")
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["fetch"]["mode"] == "like"
        assert stored.result["fetch"]["ok"] == 1
        assert stored.result["plan"]["files"] == 2
        assert stored.result["import"]["imported"] == 2

    assert calls["like_user"] == "https://www.douyin.com/user/MS4wLjABAAAAme"

    items = [
        i
        for i in client.get("/api/inspirations?size=50").json()["items"]
        if str(i["source_platform_id"]).startswith("f2:")
    ]
    assert len(items) == 2
    assert {i["source_author"] for i in items} == {"不养羊"}


async def test_execute_f2_import_like_mode_dedups_on_rerun(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """去重要求：同一批喜欢的作品重跑（或重复点赞）不再入库。"""
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    _stub_like_fetch(monkeypatch)

    for index in range(2):
        async with async_session() as db:
            task = await task_runner.create_f2_import_task(db, fetch=True, fetch_mode="like")
            task_id = task.id
            await task_runner.execute_f2_import(db, task)
        async with async_session() as db:
            stored = await db.get(TaskQueue, task_id)
            if index == 0:
                assert stored.result["import"]["imported"] == 2
            else:
                assert stored.result["plan"]["files"] == 0
                assert stored.result["import"]["imported"] == 0

    items = [
        i
        for i in client.get("/api/inspirations?size=50").json()["items"]
        if str(i["source_platform_id"]).startswith("f2:")
    ]
    assert len(items) == 2  # 没有因重跑翻倍


async def test_execute_f2_import_like_mode_keeps_unregistered_authors(
    client, f2_like_tree, create_blogger, auto_settings, monkeypatch
):
    """口径：点赞模式不做作者白名单（喜欢的作品天然跨作者），未登记作者照样入库。

    与发布模式对照：发布模式只处理已登记博主（见
    test_execute_f2_import_skips_unregistered_author_dirs）；点赞是用户自己的
    明确收藏行为，全收才符合语义，去重仍然生效。
    """
    from app.models.task import TaskQueue

    create_blogger("里香", platform="douyin")  # 让白名单生效，但不含「不养羊」
    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    _stub_like_fetch(monkeypatch)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True, fetch_mode="like")
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["plan"]["files"] == 2
        assert (
            stored.result["plan"]["skipped"].get("作者不在指定范围（--authors / 已登记博主）", 0)
            == 0
        )


async def test_execute_f2_import_like_mode_requires_like_user(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """没填「我的主页链接」：直接给出可操作错误，不白跑一次 f2。"""
    auto_settings.f2_like_user = ""
    monkeypatch.setattr(f2, "f2_available", lambda: True)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True, fetch_mode="like")
        with pytest.raises(RuntimeError, match="未配置「我的主页链接」"):
            await task_runner.execute_f2_import(db, task)


def test_f2_status_like_availability_is_independent_of_blogger_whitelist(
    client, tmp_path, auto_settings, monkeypatch
):
    """「我的喜欢」可用性只取决于 f2 + 目录 + 主页链接，与「已登记博主」白名单无关。

    否则「库里没登记抖音博主」的用户会被按钮挡住——而点赞列表本来就跨作者。
    """
    f2_dir = tmp_path / "f2proj"
    f2_dir.mkdir()
    monkeypatch.setattr(f2, "f2_available", lambda: True)
    monkeypatch.setattr(f2, "DEFAULT_F2_DIR", f2_dir)
    monkeypatch.setattr(f2, "DEFAULT_F2_ROOT", f2_dir / "Download")

    auto_settings.f2_like_user = ""
    without_user = task_runner.f2_import_status()
    assert without_user["like_available"] is False
    assert "未配置「我的主页链接」" in without_user["like_reason"]
    assert without_user["available"] is False  # 用户库为空，发布模式仍不可用

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    with_user = task_runner.f2_import_status()
    assert with_user["like_available"] is True
    assert with_user["like_user"] == auto_settings.f2_like_user
    assert "我的喜欢" in with_user["like_reason"]
