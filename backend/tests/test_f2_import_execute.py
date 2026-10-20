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
# 所以这两项必须 patch f2_fetch_stages。`_current_status` 仍 patch f2_import——
# f2_fetch_stages 的同名函数是转发壳（见其 docstring），两边都指向同一个实现。
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



async def test_execute_f2_import_collect_mode_aggregates_into_collection(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """收藏端到端：走收藏命令与收藏产物目录，素材进二级「未分类收藏」。

    这是本功能与「我的喜欢」唯一的差别所在，其余（下载阶段、五层判重、来源作者
    补登记）都是共用实现——所以这里重点锁「跑的是收藏链路」+「素材落进收藏夹」。
    平铺批次没有夹归属，所以进「未分类收藏」；**一级恒为空**。
    """
    from sqlalchemy import select

    from app.models.collection import Collection, CollectionItem
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    calls: dict = {}
    _stub_collect_fetch(monkeypatch, calls)
    # 收藏产物根指向同一棵假树（两种模式的目录结构一致：{我的昵称}/{原作者}_{时间}_…）
    patch_f2(monkeypatch, "DEFAULT_F2_COLLECT_ROOT", f2.DEFAULT_F2_LIKE_ROOT)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=True, fetch_mode="collection", allow_all_collect=True
        )
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        # 状态由 worker 收尾（这里直接调执行器，故不判 status，与点赞用例一致）
        assert stored.result["fetch"]["mode"] == "collection"
        assert stored.result["import"]["imported"] == 2
        collection_stats = stored.result["collection"]
        assert collection_stats["name"] == "抖音入库自动收藏"
        assert collection_stats["added"] == 0, "一级不装素材"
        assert collection_stats["unclassified"] == 2
        assert collection_stats["folder_count"] == 1

        root = (
            await db.execute(
                select(Collection).where(Collection.name == "抖音入库自动收藏")
            )
        ).scalars().one()
        assert root.parent_id is None and root.auto_source == "douyin"
        assert (
            await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == root.id
                )
            )
        ).scalars().all() == []
        child = (
            await db.execute(
                select(Collection).where(
                    Collection.parent_id == root.id,
                    Collection.name == f2_runner.UNCLASSIFIED_COLLECTION_NAME,
                )
            )
        ).scalars().one()
        member_ids = (
            await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == child.id
                )
            )
        ).scalars().all()
        assert len(member_ids) == 2

    # 走的必须是收藏链路（run_fetch_collects），而不是点赞/主页作品
    assert calls["fetch"] == "collect"
    assert calls["like_user"] == "https://www.douyin.com/user/MS4wLjABAAAAme"



async def test_execute_f2_import_collect_ids_downloads_only_selected_folders(
    client, auto_settings, monkeypatch
):
    """「先扫描、后下载」：带 collect_ids 时逐夹下载，入库与合集聚合照常。"""
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    calls: dict = {}
    _stub_collect_folder_download(monkeypatch, calls)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=True, fetch_mode="collection", collect_ids=["7650133299343595322"]
        )
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        fetch = stored.result["fetch"]
        assert fetch["collect"]["folders"][0]["name"] == "秘书OL"
        assert fetch["collect"]["works"] == 1
        assert fetch["mode"] == "collection"
        # 落盘的那张图照常入库，并归位到二级「秘书OL」（一级是纯分类节点）
        assert stored.result["import"]["imported"] == 1
        stats = stored.result["collection"]
        assert stats["added"] == 0  # 一级不装作品
        assert stats["folder_count"] == 1
        assert stats["folders"][0]["name"] == "秘书OL"
        assert stats["folders"][0]["added"] == 1

    assert calls["collect_ids"] == ["7650133299343595322"]
    assert calls["max_counts"] == 0  # 未配置「每次最多翻」= 每个夹全量
    assert calls["user"] == "https://www.douyin.com/user/MS4wLjABAAAAme"
    # F 的接线：库内已有作品 ID 集合与「同类目录预链接来源」都要传下去
    assert calls["link_from_root"] == str(f2.DEFAULT_F2_LIKE_ROOT)
    assert isinstance(calls["existing_aweme_ids"], set)



async def test_execute_f2_import_collect_ids_scopes_import_to_selected_folders(
    client, auto_settings, monkeypatch
):
    """勾选的收藏夹 = **入库范围**：未勾选夹里已躺在硬盘上的文件一件都不入库。

    回归（用户实测）：下载阶段只下勾选的夹，但入库阶段原先扫的是**整个**收藏目录
    ——没勾的夹只要文件在硬盘上就照样被收进素材库。结果就是「我明明只勾了两个夹，
    股票/哲学那些也全进来了」。
    """
    from sqlalchemy import select

    from app.models.collection import Collection, CollectionItem
    from app.models.inspiration import Inspiration
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    patch_f2(monkeypatch, "f2_available", lambda: True)
    patch_f2(
        monkeypatch,
        "run_fetch_collects",
        lambda *_a, **_k: pytest.fail("选中收藏夹时不该走平铺收藏命令"),
    )

    selected_aweme = "7000000000000000001"
    unselected_aweme = "7000000000000000002"

    def _fake(f2_dir, user, collect_ids, max_counts=0, should_stop=None, on_progress=None,
              existing_aweme_ids=None, link_from_root=None):
        root = f2.DEFAULT_F2_COLLECT_ROOT / "我的账号"
        # 勾选的夹里的作品
        _jpeg(root / f"作者A_2026-09-01 10-00-00_穿搭#jk_{selected_aweme}_image_1.jpg")
        # 没勾的夹里早就下好的作品（平铺那次留下的）——不该被顺带入库
        _jpeg(root / f"作者B_2026-09-01 10-00-00_股票_{unselected_aweme}_image_1.jpg")
        _write_collect_folder_map(root, {"111": ("秘书OL", [selected_aweme])})
        stats = {
            "cookie_source": "假配置",
            "root": str(root),
            "folders": [
                {"id": "111", "name": "秘书OL", "total": 1, "works": 1, "skipped_existing": 0}
            ],
            "works": 1,
            "total_works": 1,
            "current": {},
            "stopped": False,
            "missing_folders": [],
            "skipped_existing": 0,
            "skipped_existing_ids": [],
            "skipped_ids_truncated": False,
            "prelinked": 0,
            "link_index_size": 0,
        }
        if on_progress:
            on_progress(stats)
        return stats

    patch_f2(monkeypatch, "download_collect_folders", _fake)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=True, fetch_mode="collection", collect_ids=["111"]
        )
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["import"]["imported"] == 1, "只该入库勾选夹里的那件"
        # 没勾的夹那件被范围过滤挡掉，并在结果里留痕（不静默丢弃）
        assert stored.result["plan"]["scoped_out"] == 1
        assert "未勾选的收藏夹跳过 1 个文件" in stored.result["notices"]

        rows = (
            await db.execute(
                select(Inspiration.source_platform_id).where(
                    Inspiration.deleted_at.is_(None)
                )
            )
        ).scalars().all()
        assert len(rows) == 1 and selected_aweme in rows[0]

        # 它归到了「秘书OL」这个二级收藏夹，一级仍为空（纯分类节点）
        root = (
            await db.execute(
                select(Collection).where(Collection.name == "抖音入库自动收藏")
            )
        ).scalars().one()
        child = (
            await db.execute(
                select(Collection).where(
                    Collection.parent_id == root.id, Collection.name == "秘书OL"
                )
            )
        ).scalars().one()
        child_members = (
            await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == child.id
                )
            )
        ).scalars().all()
        assert len(child_members) == 1
        root_members = (
            await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == root.id
                )
            )
        ).scalars().all()
        assert list(root_members) == []



async def test_execute_f2_import_collect_mode_adds_existing_library_works_to_collection(
    client, auto_settings, monkeypatch
):
    """E：已在库、本次没重新入库（被 F 跳过）的收藏作品也要补进「抖音入库自动收藏」。"""
    from sqlalchemy import select

    from app.models.collection import Collection, CollectionItem
    from app.models.inspiration import Inspiration
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    # 仓库里已有一件「早就采过」的收藏作品（真实作品 ID 形态的平台 ID）
    async with async_session() as db:
        db.add(
            Inspiration(
                id="existing-collect-material",
                source_type="douyin",
                source_platform_id="f2:7670881947199742833#image_1",
                source_url="https://www.douyin.com/note/7670881947199742833",
                file_path="images/2026-08/x.jpg",
                media_type="image",
            )
        )
        await db.commit()

    calls: dict = {}
    patch_f2(monkeypatch, "f2_available", lambda: True)
    # 下载阶段：这件作品被 F 跳过（没产生文件），只记下作品 ID
    patch_f2(
        monkeypatch,
        "run_fetch_collects",
        lambda *_a, **_k: pytest.fail("选中收藏夹时不该走平铺收藏命令"),
    )

    def _fake(f2_dir, user, collect_ids, max_counts=0, should_stop=None, on_progress=None,
              existing_aweme_ids=None, link_from_root=None):
        calls["existing_aweme_ids"] = set(existing_aweme_ids or set())
        # 按夹下载即使一件都没新下（全被 F 跳过）也会写归属清单
        _write_collect_folder_map(
            f2.DEFAULT_F2_COLLECT_ROOT / "我的账号",
            {collect_ids[0]: ("秘书OL", ["7670881947199742833"])},
        )
        stats = {
            "cookie_source": "假配置",
            "root": str(f2.DEFAULT_F2_COLLECT_ROOT),
            "folders": [{"id": collect_ids[0], "name": "秘书OL", "total": 1, "works": 1,
                         "skipped_existing": 1}],
            "works": 1,
            "total_works": 1,
            "current": {},
            "stopped": False,
            "missing_folders": [],
            "skipped_existing": 1,
            "skipped_existing_ids": ["7670881947199742833"],
            "skipped_ids_truncated": False,
            "prelinked": 0,
            "link_index_size": 0,
        }
        if on_progress:
            on_progress(stats)
        return stats

    patch_f2(monkeypatch, "download_collect_folders", _fake)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=True, fetch_mode="collection", collect_ids=["7650133299343595322"]
        )
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["import"]["imported"] == 0  # 本次一件都没入库
        stats = stored.result["collection"]
        assert stats["folder_count"] == 1
        folder = stats["folders"][0]
        assert folder["name"] == "秘书OL"
        assert folder["added"] == 1
        # 本次一件都没新入库，这件是从「已在库」补进来的（E 的语义，按夹同口径）
        assert folder["from_existing"] == 1
        assert stats["added"] == 0  # 一级不装作品

        root = (
            await db.execute(
                select(Collection).where(Collection.name == "抖音入库自动收藏")
            )
        ).scalars().one()
        child = (
            await db.execute(
                select(Collection).where(
                    Collection.parent_id == root.id, Collection.name == "秘书OL"
                )
            )
        ).scalars().one()
        member_ids = (
            await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == child.id
                )
            )
        ).scalars().all()
        assert list(member_ids) == ["existing-collect-material"]

    # F 的过滤集合里要有这件作品，否则它根本不会被跳过
    assert "7670881947199742833" in calls["existing_aweme_ids"]



async def test_execute_f2_import_without_collect_ids_keeps_flat_collect_path(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """显式 allow_all_collect=true 时才走平铺收藏（含未分类）——这条老口径仍可用。

    默认情况下它会被拒（见 test_collect_mode_without_folder_selection_is_rejected）：
    平铺收藏把用户没勾的夹也一起收进来，正是 #386/#387 两次误入库的成因。
    """
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    calls: dict = {}
    _stub_collect_fetch(monkeypatch, calls)
    patch_f2(monkeypatch, "DEFAULT_F2_COLLECT_ROOT", f2.DEFAULT_F2_LIKE_ROOT)
    patch_f2(
        monkeypatch,
        "download_collect_folders",
        lambda *_a, **_k: pytest.fail("没有选中收藏夹时不该走逐夹下载"),
    )

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=True, fetch_mode="collection", allow_all_collect=True
        )
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["fetch"]["mode"] == "collection"
        assert stored.result["import"]["imported"] == 2
        # 平铺批次没有归属 → 全部进「未分类收藏」，一级仍空
        assert stored.result["collection"]["added"] == 0
        assert stored.result["collection"]["unclassified"] == 2

    assert calls["fetch"] == "collect"



async def test_execute_f2_import_ignores_collect_ids_for_non_collection_mode(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """collect_ids 只对收藏模式生效：点赞模式仍走平铺点赞链路（不误用收藏夹下载）。"""
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    calls: dict = {}
    _stub_like_fetch(monkeypatch, calls)
    patch_f2(
        monkeypatch,
        "download_collect_folders",
        lambda *_a, **_k: pytest.fail("点赞模式不该走收藏夹下载"),
    )

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=True, fetch_mode="like", collect_ids=["7650133299343595322"]
        )
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["fetch"]["mode"] == "like"
        assert stored.result["import"]["imported"] == 2

    assert calls["like_user"] == "https://www.douyin.com/user/MS4wLjABAAAAme"



async def test_execute_f2_import_like_mode_merges_cross_mode_duplicates(
    client, f2_like_tree, auto_settings, monkeypatch, tmp_path
):
    """下载结束后自动合并跨模式重复：like 与 collection 同名同内容 → 硬链接。

    f2 判断「下过没有」只看**当前模式目录里有没有同名文件**（没有下载台账），
    所以同一作品被点赞又被收藏时会各存一份；合并后两个目录里文件都还在（两侧的
    「存在即跳过」继续有效），磁盘只占一份。
    """
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    _stub_like_fetch(monkeypatch)
    like_dir = f2.DEFAULT_F2_LIKE_ROOT / "我的账号"
    dup_name = "不养羊_2026-09-14 10-31-14_下一站再见吧#地铁jk_#jk_image_1.jpg"
    payload = (like_dir / dup_name).read_bytes()
    coll_root = tmp_path / "collection"
    coll_dir = coll_root / "我的账号"
    coll_dir.mkdir(parents=True)
    dup_copy = coll_dir / dup_name
    dup_copy.write_bytes(payload)  # 同一作品又被收藏：同名同内容
    patch_f2(monkeypatch, "DEFAULT_F2_COLLECT_ROOT", coll_root)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True, fetch_mode="like")
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        merge = stored.result["fetch"]["merge"]
        assert stored.result["import"]["imported"] == 2

    assert merge["linked"] == 1
    assert merge["saved_bytes"] == len(payload)
    assert (merge["conflict"], merge["failed"]) == (0, 0)
    like_file = like_dir / dup_name
    assert dup_copy.exists() and like_file.exists()  # 两侧路径都必须留着
    assert os.stat(like_file).st_ino == os.stat(dup_copy).st_ino
    assert like_file.read_bytes() == payload



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



async def test_execute_f2_import_like_mode_reports_live_download_progress(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """「我的喜欢」下载期间持续写入「已落盘文件数 / 本次新增」与软进度。

    回归背景：点赞是单条命令全量翻页，下载期可能十几分钟，此前进度只在 f2 返回后
    一次性写 0→40%，界面上整段时间停在 0%，看不出是在下载还是卡住。
    """
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    patch_f2(monkeypatch, "f2_available", lambda: True)
    # 缩短轮询间隔与软进度时间常数，让「下载中」的中间态在测试里可观测
    monkeypatch.setattr(f2_fetch_stages, "_WATCH_INTERVAL", 0.01)
    monkeypatch.setattr(f2_progress, "_WATCH_INTERVAL", 0.01)
    monkeypatch.setattr(f2_progress, "_SOFT_PROGRESS_HALF_SECONDS", 1.0)

    real_stats = f2.download_tree_stats

    def _slow_like_fetch(f2_dir, like_user, download_root=None, **kwargs):
        """模拟 f2：先落一个新文件，再慢慢「翻页」（此刻 watcher 应已把进度落库）。"""
        _jpeg(
            f2_like_tree
            / "我的账号"
            / "新作者_2026-09-15 09-00-00_新点赞的作品#tag_image_1.jpg",
            "green",
        )
        time.sleep(0.3)
        return {
            "total": 1,
            "ok": 1,
            "failed": 0,
            "results": [{"nickname": "我的喜欢", "rc": 0, "cmd": "f2 dy -M like"}],
        }

    patch_f2(monkeypatch, "run_fetch_likes", _slow_like_fetch)

    live: list[tuple[int, dict]] = []

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True, fetch_mode="like")
        task_id = task.id

        def _spy_stats(root):
            """每次统计时记下**上一次**落库的实时进度（任务对象由执行器就地改写）。"""
            info = (task.result or {}).get("download_progress")
            if info:
                live.append((task.progress, dict(info)))
            return real_stats(root)

        patch_f2(monkeypatch, "download_tree_stats", _spy_stats)
        await task_runner.execute_f2_import(db, task)

    assert live, "下载期间没有写入任何 download_progress 快照"
    assert any(progress > 0 for progress, _ in live), "软进度始终为 0%"
    assert any(info["files"] == 3 for _, info in live), "实时计数没有反映新落盘的文件"
    assert max(info["added"] for _, info in live) == 1

    # 下载产出统计保留到任务结果里（入库阶段的 plan 只讲「有多少要入库」）
    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["fetch"]["downloaded"]["added"] == 1
        assert stored.result["import"]["imported"] == 3



async def test_execute_f2_import_post_mode_reports_live_download_progress(
    client, f2_tree, monkeypatch
):
    """发布模式（逐作者）下载期也要有实时进度：第几位作者 + 已落盘文件数。

    2026-10-01 用户报：下载目录里已经落了两千张图，界面还停在「第 0/1 个作者 · 0%」——
    因为进度只在**每个作者跑完**后才写一次。现在改为共用「我的喜欢」那套观察器：
    下载期间每 2 秒写 download_progress（含 author_index/author_total）+ 软进度。
    """
    from app.models.task import TaskQueue
    from app.services.task_runners import f2_import as f2_runner

    f2_dir, root = f2_tree
    # f2 用户库里放一个作者，否则发布模式没有下载目标（不会进入下载阶段）
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.execute("INSERT INTO user_info_web VALUES ('sec-lixiang', '里香1√', 171)")
    conn.commit()
    conn.close()
    patch_f2(monkeypatch, "f2_available", lambda: True)
    monkeypatch.setattr(f2_fetch_stages, "_WATCH_INTERVAL", 0.01)
    monkeypatch.setattr(f2_progress, "_WATCH_INTERVAL", 0.01)
    monkeypatch.setattr(f2_progress, "_SOFT_PROGRESS_HALF_SECONDS", 1.0)

    real_stats = f2.download_tree_stats

    def _slow_download(cmd, cwd):
        """模拟 f2 逐作者下载：先落一个新文件，再「翻页」一会儿（此刻观察器应已落库）。"""
        _jpeg(root / "里香1√" / "2025-01-02 10-00-00_#jk_新作品_image_1.jpg", "yellow")
        time.sleep(0.3)
        return 0

    monkeypatch.setattr(f2_fetch_stages, "_run_subprocess", _slow_download)

    live: list[tuple[int, dict]] = []
    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True)
        task_id = task.id

        def _spy_stats(path):
            """每次统计时记下**上一次**落库的实时进度。"""
            info = (task.result or {}).get("download_progress")
            if info:
                live.append((task.progress, dict(info)))
            return real_stats(path)

        patch_f2(monkeypatch, "download_tree_stats", _spy_stats)
        await task_runner.execute_f2_import(db, task)

    assert live, "发布模式下载期间没有写入任何 download_progress 快照"
    assert any(progress > 0 for progress, _ in live), "发布模式下载期软进度始终为 0%"
    # 正在跑的是第 1 个作者（本用例 f2 用户库里只有一位）——修好前这里会是 0
    assert {info["author_index"] for _, info in live} == {1}
    assert all(info["author_total"] == 1 for _, info in live)
    assert any(info["added"] >= 1 for _, info in live), "实时计数没有反映新落盘的文件"

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["import"]["imported"] == 4  # 原 3 个 + 模拟新落的 1 个



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



async def test_execute_f2_import_like_mode_registers_source_authors(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """「我的喜欢」入库后：未登记的来源作者自动建成抖音博主并绑定本批素材。

    同时锁死两个口径：
      - 补建的博主标记「自动登记」，**不算已登记博主**（不进下载白名单）
      - 素材归属指向原作者的博主记录（而不是「我的账号」）
    """
    from sqlalchemy import select

    from app.models.person import Blogger, InspirationBlogger
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    _stub_like_fetch(monkeypatch)

    task_id = await _run_like_import()

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        stats = stored.result["bloggers"]
        assert stats["created"] == 1 and stats["linked"] == 2
        bloggers = (await db.execute(select(Blogger))).scalars().all()
        assert [(b.name, b.platform, b.source) for b in bloggers] == [
            ("不养羊", "douyin", "auto_collect")
        ]
        links = (await db.execute(select(InspirationBlogger))).scalars().all()
        assert {link.blogger_id for link in links} == {bloggers[0].id}
        assert len(links) == 2

    # 白名单口径：自动登记的博主不算「已登记博主」→ 不会进一键获取素材的下载名单
    assert f2.load_douyin_bloggers() == {}
    assert "不养羊" in f2.load_douyin_bloggers(include_auto=True)



async def test_execute_f2_import_like_mode_can_skip_blogger_registration(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """建任务时关掉自动登记：素材照常入库，但不动博主库。"""
    from sqlalchemy import select

    from app.models.person import Blogger
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    _stub_like_fetch(monkeypatch)

    task_id = await _run_like_import(register_bloggers=False)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert "bloggers" not in stored.result
        assert stored.result["import"]["imported"] == 2
        assert (await db.execute(select(Blogger))).scalars().all() == []



async def test_execute_f2_import_collect_mode_does_not_register_bloggers(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """「我的收藏」入库后**不补建博主**：素材照常入库，博主库一个不动。

    背景（2026-10-01）：收藏是「先收起来」的池子，天然跨主题（股票/哲学/公考/游戏），
    自动建号只在库里堆与穿搭无关的空壳（实测 451 位自动登记博主里只有 1% 与穿搭相关）。
    「我的喜欢」保持自动登记，所以这里同时断言任务记录里的开关被归一成 False——
    口径由 ``personal_registers_bloggers`` 一处说了算。
    """
    from sqlalchemy import select

    from app.models.person import Blogger
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    _stub_collect_fetch(monkeypatch)
    # 收藏产物根指向同一棵树（文件名同样带原作者前缀，具备补建博主的一切条件）
    patch_f2(monkeypatch, "DEFAULT_F2_COLLECT_ROOT", f2_like_tree)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db,
            fetch=True,
            fetch_mode="collection",
            register_bloggers=True,  # 显式要求也无效：收藏恒不补建
            allow_all_collect=True,
        )
        task_id = task.id
        assert task.result["register_bloggers"] is False
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert "bloggers" not in stored.result  # 登记阶段没有跑
        assert stored.result["import"]["imported"] == 2  # 素材照常入库
        assert (await db.execute(select(Blogger))).scalars().all() == []



async def test_execute_f2_import_post_mode_does_not_register_bloggers(
    client, f2_tree, auto_settings, monkeypatch
):
    """发布模式不自动建博主：那条链路的作者本来就受白名单约束，口径不同不混用。"""
    from sqlalchemy import select

    from app.models.person import Blogger
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    patch_f2(monkeypatch, "f2_available", lambda: True)

    task_id = await _import_once()

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert "bloggers" not in stored.result
        assert (await db.execute(select(Blogger))).scalars().all() == []



async def test_execute_f2_import_like_mode_requires_like_user(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """没填「我的主页链接」：直接给出可操作错误，不白跑一次 f2。"""
    auto_settings.f2_like_user = ""
    patch_f2(monkeypatch, "f2_available", lambda: True)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True, fetch_mode="like")
        with pytest.raises(RuntimeError, match="未配置「我的主页链接」"):
            await task_runner.execute_f2_import(db, task)

