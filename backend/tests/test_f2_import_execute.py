"""f2 导入任务执行链路测试（下载→扫描→入库→登记，含暂停/取消/重试与进度上报）。"""

import asyncio
import json
import os
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
import pytest
from app.database import async_session
from app.services import task_runner
from app.services.task_runners import f2_import as f2_runner
# 切片 D 之后下载阶段住在 f2_fetch_stages：`_run_subprocess` / `_WATCH_INTERVAL` 由
# **该模块**读取，patch 旧模块（f2_import）不生效且不报错（调用的是真子进程），
# 所以这两项必须 patch f2_fetch_stages。`_current_status` 仍 patch f2_import：
# 编排层（execute_f2_import）持有这个名字，并把它作为 `status_of` **注入**给各阶段函数，
# 所以 patch 一处就能同时影响取数阶段与入库阶段（见 f2_fetch_stages 的 StatusReader 注释）。
from app.services.task_runners import f2_fetch_stages
from app.services.task_runners import f2_progress
from app.services.task_runners.common import utcnow
from f2_patch import patch_f2
from scripts import import_f2_downloads as f2
from f2_import_fixtures import (  # noqa: F401 —— fixture 与助手需显式导入
    SEC,
    _jpeg,
    _make_f2_author_db,
    _make_author_db,
    _count_f2_tasks,
    _import_once,
    _stub_like_fetch,
    _stub_collect_fetch,
    _write_collect_folder_map,
    _stub_collect_folder_download,
    _run_like_import,
    isolate_personal_roots,
    f2_tree,
    auto_env,
    auto_settings,
    f2_like_tree,
)


async def test_execute_f2_import_rejects_authors_absent_from_f2_db(
    client, f2_tree, monkeypatch
):
    """**回归任务 351**：点名的作者不在 f2 用户库里时，必须显式失败并给可操作原因。

    此前的行为是「下载 0 个作者 + 入库 0」但状态 success——用户完全看不出为什么。
    """
    f2_dir, _root = f2_tree
    import sqlite3

    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.execute("INSERT INTO user_info_web VALUES ('sec1', '里香1√', 171)")
    conn.commit()
    conn.close()
    patch_f2(monkeypatch, "f2_available", lambda: True)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=True, authors=["唐思瑶ya"]
        )
        with pytest.raises(RuntimeError) as ei:
            await task_runner.execute_f2_import(db, task)

    msg = str(ei.value)
    assert "不在 f2 用户库里" in msg
    assert "唐思瑶ya" in msg
    # 文案要指向可操作的出路，而不是只说「失败了」
    assert "按博主全量下载" in msg
    # 顺带把 f2 到底认识谁列出来，用户能自己对照
    assert "里香" in msg



async def test_execute_f2_import_profiles_downloads_named_blogger(
    client, f2_tree, monkeypatch
):
    """核心：点名一个 f2 **不认识**的博主，也能下她全部作品并入她的库。

    回归任务 351 的反面——那次请求的博主不在 f2 用户库，结果下载 0 个作者、
    入库 0，状态却是 success。
    """
    f2_dir, root = f2_tree
    patch_f2(monkeypatch, "f2_available", lambda: True)
    # 下载目录里放一件她的产物（模拟 f2 下载完成）
    _jpeg(root / "唐思瑶ya" / "2026-01-01 10-00-00_#穿搭_image_1.jpg")
    # f2 下载后已把账号写进用户库（本用例模拟：按 sec 反查得到昵称）
    patch_f2(
        monkeypatch, "load_f2_authors", lambda _d: [{"sec_user_id": SEC, "nickname": "唐思瑶ya", "aweme_count": 1}]
    )

    from app.services.task_runners import f2_import as runner

    commands: list[list[str]] = []
    monkeypatch.setattr(
        f2_fetch_stages, "_run_subprocess", lambda cmd, cwd: (commands.append(cmd) or 0)
    )

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=True, profiles=[f"https://www.douyin.com/user/{SEC}"]
        )
        await task_runner.execute_f2_import(db, task)
        task_id = task.id

    assert len(commands) == 1
    cmd = commands[0]
    assert cmd[cmd.index("-u") + 1] == f"https://www.douyin.com/user/{SEC}"
    # 首次点名 → 全量（否则只拿到最近 14 天，用户以为下全了）
    assert cmd[cmd.index("-i") + 1] == "all"

    # 她的产物要真的入库（入库白名单是本例的另一个回归点：按昵称匹配）
    result = client.get(f"/api/tasks/{task_id}").json()["result"]
    assert result["plan"]["files"] == 1
    assert result["import"]["imported"] == 1
    assert result["fetch"]["profiles"] == [
        {"sec_user_id": SEC, "nickname": "唐思瑶ya", "url": f"https://www.douyin.com/user/{SEC}"}
    ]



async def test_execute_f2_import_profiles_fails_loudly_when_nothing_downloaded(
    client, f2_tree, monkeypatch
):
    """**回归任务 351**：一个都没下成时必须显式失败并说明原因，不能报 success + 入库 0。"""
    _f2_dir, _root = f2_tree
    patch_f2(monkeypatch, "f2_available", lambda: True)

    from app.services.task_runners import f2_import as runner

    monkeypatch.setattr(f2_fetch_stages, "_run_subprocess", lambda cmd, cwd: 0)
    # 反查不到任何昵称 → 说明 f2 没把她登记进用户库（下载没成）
    patch_f2(
        monkeypatch, "resolve_profile_nicknames",
        lambda _d, _s: {},
    )

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True, profiles=[SEC])
        with pytest.raises(RuntimeError, match="一个都没下成"):
            await task_runner.execute_f2_import(db, task)



async def test_execute_f2_import_profiles_rejects_unsupported_input(
    client, f2_tree, monkeypatch
):
    """抖音号 / 短链要明确报错，而不是拼一个必然失败的 URL 然后静默入库 0。"""
    _f2_dir, _root = f2_tree
    patch_f2(monkeypatch, "f2_available", lambda: True)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True, profiles=["72906514384"])
        with pytest.raises(RuntimeError, match="无法识别的博主"):
            await task_runner.execute_f2_import(db, task)



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

    patch_f2(monkeypatch, "f2_available", lambda: True)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        f2_fetch_stages, "_run_subprocess", lambda cmd, cwd: (commands.append(cmd) or 0)
    )

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True, since_days=7)
        await task_runner.execute_f2_import(db, task)

    assert commands, "没有调用 f2"
    interval = commands[0][commands[0].index("-i") + 1]
    assert interval != "all"
    assert interval.endswith(f"|{utcnow().date():%Y-%m-%d}")
    assert interval.startswith(f"{(utcnow() - timedelta(days=7)).date():%Y-%m-%d}")



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
        # 在会话内取批号清单路径（会话关闭后不再访问 ORM 属性）
        batch_path = Path(stored.result["import"]["batch_file"])

    # 素材已入库且**未打标**（导入不做标签分析）
    detail = client.get("/api/inspirations?size=50").json()
    f2_items = [
        item for item in detail["items"] if str(item["source_platform_id"]).startswith("f2:")
    ]
    assert len(f2_items) == 3
    for item in f2_items:
        assert item["source_type"] == "douyin"
        assert item["quality_status"] == "pending"

    # 批次清单落盘（可回滚）——落在 storage 根目录下，与素材/缩略图同根。
    # ⚠ 必须按**任务结果里的 batch_file** 取，不能 glob 目录再取「最新的那份」：
    # storage 根在 xdist 多 worker 间共享，别的用例（如 profiles/like 模式用例）
    # 也会往同一目录写清单；清单名是 `f2-{秒级时间戳}-{4位随机}`，同秒写入时
    # 字典序由随机后缀决定 —— glob 出来的「最新」可能是**别人的那一批**，
    # 断言就会读到别的批次的 imported 数量（CI 实测：期望 3 实得 1）。
    batch_file = batch_path
    assert batch_file.exists(), f"批次清单未落盘：{batch_file}"
    payload = json.loads(batch_file.read_text(encoding="utf-8"))
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
    patch_f2(monkeypatch, "f2_available", lambda: False)

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

    patch_f2(monkeypatch, "f2_available", lambda: True)
    monkeypatch.setattr(f2_fetch_stages, "_run_subprocess", lambda cmd, cwd: 1)  # 每个作者都失败

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

    patch_f2(monkeypatch, "scan_directory", slow_scan)

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

    patch_f2(monkeypatch, "f2_available", lambda: True)
    seen: list[str] = []

    async def fake_status(_db, task_id):
        """在下载阶段（第一个作者）偷看一眼落库的 stage。"""
        if not seen:
            async with async_session() as probe:
                row = await probe.get(TaskQueue, task_id)
                seen.append(str((row.result or {}).get("stage")))
        return "running"

    monkeypatch.setattr(runner, "_current_status", fake_status)
    monkeypatch.setattr(f2_fetch_stages, "_run_subprocess", lambda cmd, cwd: 0)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True)
        await task_runner.execute_f2_import(db, task)

    assert seen == ["download"]



async def test_execute_f2_import_cancel_during_download_skips_scan(
    client, auto_env, monkeypatch
):
    """取消发生在下载阶段：立即收尾，不再做重活扫描。

    回归点：原先 break 之后仍会跑完整扫描（全量实测约 81 秒）才停下，用户点了
    取消却迟迟没反应；顺带确认重跑不会把上一次的产物字段带进新结果。
    """
    from app.models.task import TaskQueue
    from app.services.task_runners import f2_import as runner

    monkeypatch.setattr(f2_fetch_stages, "_run_subprocess", lambda cmd, cwd: 0)

    async def cancelled(_db, _task_id):
        return "cancelled"

    monkeypatch.setattr(runner, "_current_status", cancelled)

    def no_scan(_root):
        raise AssertionError("下载被取消后不应再扫描目录")

    patch_f2(monkeypatch, "scan_directory", no_scan)

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

    patch_f2(monkeypatch, "apply_import", fake_apply)

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

    patch_f2(monkeypatch, "f2_available", lambda: True)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        f2_fetch_stages, "_run_subprocess", lambda cmd, cwd: (commands.append(cmd) or 0)
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
