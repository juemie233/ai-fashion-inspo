"""f2 导入任务状态与调度测试（状态查询、可用性、自动调度与节流）。"""

import asyncio
from datetime import timedelta
import pytest
from app.database import async_session
from app.services import task_runner
from app.services.task_runners import f2_import as f2_runner
# 切片 D 之后「我的列表」下载阶段住在 f2_fetch_stages，`_watch_personal_download`
# 由该模块读取：patch 旧模块（f2_import）不生效且不报错。
from app.services.task_runners import f2_fetch_stages
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


async def test_status_exposes_collect_fields(client):
    """可用性状态要带收藏模式的字段（前端据此门控「采集我的收藏」）。"""
    from app.services.task_runner import f2_import_status

    info = f2_import_status()
    assert "collect_root" in info and "collect_available" in info and "collect_reason" in info
    assert str(info["collect_root"]).endswith("collection")
    # 点赞与收藏的前提相同（f2 + 工作目录 + 已配置主页链接）
    assert info["collect_available"] == info["like_available"]



async def test_finalize_import_reports_batch_error_as_notice(client):
    """批次清单写失败（本批无法回滚）要进 notices，而不是被 worker 清掉的 error。"""
    from app.models.task import TaskQueue

    async with async_session() as db:
        task = TaskQueue(
            type="f2_import", status="running", progress=50, total=1, done=1,
            result={}, max_retries=1,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        await f2_runner._finalize_import(
            db,
            task,
            {"imported": 1, "failed": 0, "batch_file": "", "batch_error": "磁盘满"},
            {"done": 1, "total": 1},
            None,
            False,
            "running",
        )
        assert task.error is None
        assert "批次清单写入失败：本批无法回滚" in (task.result or {}).get("notices", [])



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



async def test_f2_authors_endpoint_lists_whitelist_with_material_counts(
    client, tmp_path, monkeypatch
):
    """f2 博主清单：白名单账号带「已入库素材数」，未登记账号单独一档。

    回归背景：卡片只报「可增量下载 19 个已登记博主」，用户看不到这 19 个是谁、
    各自带来多少素材，也看不到 f2 库里还有哪些账号会被跳过。
    「自动登记」的博主（我的喜欢来源作者）不算已登记 → 必须落在未登记那一档。
    """
    from app.models.person import Blogger
    from app.models.inspiration import Inspiration

    f2_dir = tmp_path / "f2proj"
    _make_f2_author_db(
        f2_dir,
        [
            ("sec-lixiang", "里香1√", 171),  # 归一化名命中库内博主「里香」
            ("sec-wy", "网易第五人格", 42),  # 库内只有自动登记记录 → 未登记
        ],
    )
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", f2_dir)

    async with async_session() as db:
        db.add(
            Blogger(name="里香", platform="douyin", platform_user_id="sec-lixiang")
        )
        db.add(
            Blogger(
                name="网易第五人格",
                platform="douyin",
                source=f2.AUTO_BLOGGER_SOURCE,
            )
        )
        # 素材：里香 2 条（一条已进垃圾桶，不计入）、网易 1 条
        for path, author, deleted in (
            ("images/a.jpg", "里香", False),
            ("images/b.jpg", "里香", False),
            ("images/c.jpg", "里香", True),
            ("images/d.jpg", "网易第五人格", False),
        ):
            db.add(
                Inspiration(
                    source_type="douyin",
                    source_author=author,
                    file_path=path,
                    media_type="image",
                    deleted_at=utcnow() if deleted else None,
                )
            )
        await db.commit()

    resp = client.get("/api/scraper/f2-authors")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["available"] is True
    assert body["filter_active"] is True
    assert body["registered_count"] == 1
    assert body["unknown_count"] == 1

    registered = body["registered"][0]
    assert registered["nickname"] == "里香1√"
    assert registered["sec_user_id"] == "sec-lixiang"
    assert registered["aweme_count"] == 171
    assert registered["materials"] == 2  # 垃圾桶那条不计
    assert registered["blogger_id"] is not None
    assert registered["blogger_name"] == "里香"
    assert registered["profile_url"] == "https://www.douyin.com/user/sec-lixiang"

    unknown = body["unknown"][0]
    assert unknown["nickname"] == "网易第五人格"
    assert unknown["materials"] == 1
    assert unknown["blogger_id"] is None
    assert "未登记到博主库" in body["note"]



async def test_f2_authors_endpoint_without_douyin_bloggers(client, tmp_path, monkeypatch):
    """库里一个抖音博主都没登记：白名单不生效（全部账号都会被处理），如实说明。"""
    f2_dir = tmp_path / "f2proj"
    _make_f2_author_db(f2_dir, [("sec-wy", "网易第五人格", 42)])
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", f2_dir)

    body = client.get("/api/scraper/f2-authors").json()

    assert body["filter_active"] is False
    assert body["registered_count"] == 1
    assert body["unknown_count"] == 0
    assert "白名单尚未生效" in body["note"]



def test_f2_authors_endpoint_without_f2_dir(client, tmp_path, monkeypatch):
    """f2 目录不存在：返回空清单与原因，而不是 500。"""
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", tmp_path / "不存在")

    body = client.get("/api/scraper/f2-authors").json()

    assert body["available"] is False
    assert body["registered"] == [] and body["unknown"] == []
    assert "用户库为空或不存在" in body["note"]



def test_f2_status_exposes_like_max_counts(client):
    """状态里要带 like_max_counts，前端才能显示「增量 N 条 / 全量」。"""
    body = client.get("/api/scraper/f2-status").json()
    assert "like_max_counts" in body
    assert isinstance(body["like_max_counts"], int)



async def test_like_max_counts_explicit_zero_beats_nonzero_setting(
    client, monkeypatch, f2_like_tree
):
    """回归：任务显式传 0（要求本次全量）不能被非零的配置项覆盖。

    用 `or` 取默认值会把 0 当成「没传」，于是「偶发一次全量」永远做不到。
    本用例走真实执行路径（execute_f2_import），打桩的 run_fetch_likes 记录入参。
    """
    from app.config import settings
    from app.services.task_runners import f2_import as runner

    monkeypatch.setattr(settings, "f2_fetch_since_days", None, raising=False)
    monkeypatch.setattr(settings, "f2_like_max_counts", 150, raising=False)

    async def fake_download(_db, _task, _future, _root, _baseline, _opts):
        return ({"ok": 1, "failed": 0, "results": []}, {"files": 0, "bytes": 0})

    monkeypatch.setattr(f2_fetch_stages, "_watch_personal_download", fake_download)

    for task_value, expected in ((0, 0), (None, 150), (88, 88)):
        calls: dict = {}
        _stub_like_fetch(monkeypatch, calls)

        async with async_session() as db:
            task = await task_runner.create_f2_import_task(
                db,
                fetch=True,
                fetch_mode="like",
                like_user="sec1",
                like_max_counts=task_value,
            )
            await task_runner.execute_f2_import(db, task)

        assert calls.get("max_counts") == expected, (
            f"like_max_counts={task_value!r} 应解析为 {expected}"
        )



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
    from datetime import datetime

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



async def test_maybe_schedule_skips_collection_mode(client, auto_env, auto_settings):
    """自动获取**不支持**收藏模式：跳过而不是造一个「导入全部收藏」的任务。

    回归：收藏模式必须手选收藏夹（execute_f2_import 的硬前置），自动获取没有「勾选」
    这回事——原来它会创建一个平铺收藏任务，把整个收藏目录（含用户不想要的夹）
    全收进素材库。
    """
    auto_settings.f2_import_auto_enabled = True
    auto_settings.f2_import_auto_mode = "collection"
    auto_settings.f2_like_user = "https://www.douyin.com/user/sec1"

    async with async_session() as db:
        assert await task_runner.maybe_schedule_auto_import(db) is None
        assert await _count_f2_tasks(db) == 0



async def test_auto_status_flags_collection_unsupported(client, auto_env, auto_settings):
    """状态接口要把「收藏模式不支持自动获取」告诉前端（否则开关看着能用却永远不跑）。"""
    auto_settings.f2_import_auto_enabled = True
    auto_settings.f2_import_auto_mode = "collection"

    async with async_session() as db:
        status = await task_runner.get_f2_auto_status(db)
    assert status["mode"] == "collection"
    assert status["collection_unsupported"] is True



async def test_maybe_schedule_personal_mode_skips_without_like_user(
    client, auto_env, auto_settings
):
    """个人列表模式缺「我的主页链接」时跳过：不制造注定失败的任务。"""
    auto_settings.f2_import_auto_enabled = True
    auto_settings.f2_import_auto_mode = "like"
    auto_settings.f2_like_user = ""

    async with async_session() as db:
        assert await task_runner.maybe_schedule_auto_import(db) is None
        assert await _count_f2_tasks(db) == 0



async def test_maybe_schedule_falls_back_to_post_for_unknown_mode(
    client, auto_env, auto_settings
):
    """配置里的模式值不合法时按 post 处理，不因脏配置停掉自动获取。"""
    from app.models.task import TaskQueue

    auto_settings.f2_import_auto_enabled = True
    auto_settings.f2_import_auto_mode = "nonsense"

    async with async_session() as db:
        task_id = await task_runner.maybe_schedule_auto_import(db)
        assert task_id is not None
        task = await db.get(TaskQueue, task_id)
        assert task.result["fetch_mode"] == "post"



async def test_maybe_schedule_respects_interval(client, auto_env, auto_settings):
    """间隔判定：未到间隔跳过，已过间隔才创建（锚点是最近任务的创建时间）。"""

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
    patch_f2(monkeypatch, "f2_available", lambda: False)
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



async def test_concurrent_creation_creates_single_task(client):
    """自动调度与手动点击同时通过检查时，也只应创建一个任务（进程内锁）。"""

    async def _one():
        async with async_session() as db:
            return await task_runner.create_f2_import_task_if_idle(db, fetch=False)

    results = await asyncio.gather(_one(), _one(), _one())
    assert sum(1 for task, _rid in results if task is not None) == 1

    async with async_session() as db:
        assert await _count_f2_tasks(db) == 1



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
    patch_f2(monkeypatch, "f2_available", lambda: True)

    status = task_runner.f2_import_status()

    assert status["available"] is True
    assert status["authors"] == 1  # 只算已登记的那个
    assert status["unknown_authors"] == ["网易第五人格"]
    assert "1 个" in status["reason"] and "未登记" in status["reason"]



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



async def test_f2_task_results_restore_writes_one_summary_audit(client, f2_tree):
    """批量还原只写一条汇总审计（对齐批量移入垃圾桶的口径，不逐条留痕）。"""
    from sqlalchemy import select

    from app.models.audit import AuditLog

    task_id = await _import_once()
    ids = [i["id"] for i in client.get(f"/api/scraper/f2-tasks/{task_id}/results").json()["items"]]

    client.post(
        f"/api/scraper/f2-tasks/{task_id}/results/trash",
        json={"ids": ids, "reason": "质量差"},
    )
    resp = client.post(f"/api/scraper/f2-tasks/{task_id}/results/restore", json={"ids": ids})
    assert resp.json()["restored"] == len(ids)

    async with async_session() as db:
        restore_audits = (
            (
                await db.execute(
                    select(AuditLog).where(AuditLog.action.in_(("restore", "batch_restore")))
                )
            )
            .scalars()
            .all()
        )
    # 3 条素材只留 1 条 batch_restore（逐条的 restore 被 audit=False 抑制）
    assert [a.action for a in restore_audits] == ["batch_restore"]
    assert restore_audits[0].count == len(ids)



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



async def test_aggregate_collect_stage_keeps_root_empty(client):
    """归位阶段直接验证：有归属的进各二级、没归属的进「未分类收藏」，一级不动。"""
    from sqlalchemy import select

    from app.models.collection import Collection, CollectionItem
    from app.models.inspiration import Inspiration
    from app.models.task import TaskQueue

    async with async_session() as db:
        for aweme_id in ("7400000000000000011", "7400000000000000012"):
            db.add(
                Inspiration(
                    id=f"unc-{aweme_id}",
                    source_type="douyin",
                    source_platform_id=f"f2:{aweme_id}#image_1",
                    source_url=f"https://www.douyin.com/note/{aweme_id}",
                    file_path=f"images/2026-09/{aweme_id}.webp",
                    media_type="image",
                )
            )
        await db.commit()

    async with async_session() as db:
        task = TaskQueue(type="f2_import", status="running", result={}, max_retries=1)
        db.add(task)
        await db.commit()
        await db.refresh(task)

        stats = await f2_runner._aggregate_collect_stage(
            db,
            task,
            {"ids": []},
            extra_ids=["unc-7400000000000000011", "unc-7400000000000000012"],
            folder_map={"穿搭": ["7400000000000000011"]},
        )
        assert stats["attributed"] == 1
        assert stats["unclassified"] == 1
        assert stats["added"] == 0

        root = (
            await db.execute(
                select(Collection).where(Collection.name == "抖音入库自动收藏")
            )
        ).scalars().one()
        by_name: dict[str, set] = {}
        for child in (
            await db.execute(select(Collection).where(Collection.parent_id == root.id))
        ).scalars().all():
            by_name[child.name] = set(
                (
                    await db.execute(
                        select(CollectionItem.inspiration_id).where(
                            CollectionItem.collection_id == child.id
                        )
                    )
                ).scalars().all()
            )
        assert by_name == {
            "穿搭": {"unc-7400000000000000011"},
            "未分类收藏": {"unc-7400000000000000012"},
        }
        assert (
            await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == root.id
                )
            )
        ).scalars().all() == []



async def test_scope_empty_fails_loudly_through_worker(client, monkeypatch):
    """勾了夹却没有归属记录：必须是一条**可见的失败**，而不是绿色的 0 入库。

    回归（实测过）：这条分支原先只写 task.error，而 worker 在任务正常返回时执行
    ``task.error = None``（见 app/worker.py 成功分支）——最终用户看到的是
    status=success / progress=100 / error=None / 一件没入库，没有任何解释。
    现在改为抛 PermanentTaskError：worker 走永久错误分支，状态 failed、原因可见。
    """
    from app.config import settings as app_settings
    from app.models.task import TaskQueue
    from app.worker import _claim_next_task, _run_task_safe

    monkeypatch.setattr(
        app_settings,
        "f2_like_user",
        "https://www.douyin.com/user/MS4wLjABAAAAme",
        raising=False,
    )
    patch_f2(monkeypatch, "f2_available", lambda: True)
    patch_f2(
        monkeypatch,
        "run_fetch_collects",
        lambda *_a, **_k: pytest.fail("选中收藏夹时不该走平铺收藏命令"),
    )

    def _fake(f2_dir, user, collect_ids, max_counts=0, should_stop=None, on_progress=None,
              existing_aweme_ids=None, link_from_root=None):
        # 关键：**不写** _collect_folders.json（模拟清单缺失/写失败/夹是空的）
        stats = {
            "cookie_source": "假配置",
            "root": str(f2.DEFAULT_F2_COLLECT_ROOT / "我的账号"),
            "folders": [{"id": collect_ids[0], "name": "秘书OL", "total": 3, "works": 0,
                         "skipped_existing": 0}],
            "works": 0,
            "total_works": 3,
            "current": {},
            "stopped": False,
            "missing_folders": [],
            "skipped_existing": 0,
            "skipped_existing_ids": [],
            "skipped_ids_truncated": False,
            "prelinked": 0,
            "link_index_size": 0,
            "folder_map_file": "",
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

    assert await _claim_next_task("worker-review") == task_id
    await _run_task_safe(task_id)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.status == "failed"
        assert "勾选的收藏夹没有归属记录" in (stored.error or "")
        assert (stored.result or {}).get("scope_empty") is True



async def test_ensure_collection_survives_insert_race(client, monkeypatch):
    """并发建同一个夹时（唯一索引是最终裁决者）回退成「查询已有」，不让任务失败。

    竞态：SELECT 到 INSERT 之间别人先建出来了。实测撞的是 IntegrityError
    （`UNIQUE constraint failed: collections.name`），不是 HTTPException——
    原实现只捕 HTTPException，所以这条路径会直接把归位阶段打挂。
    """
    from sqlalchemy import select

    from app.models.collection import Collection
    from app.services import collection_service

    real_create = collection_service.create_collection

    async def _racing_create(db, **kwargs):
        # 对手（另一个进程）在我们 INSERT 之前先建好了同名的夹
        async with async_session() as other:
            other.add(
                Collection(
                    name=kwargs["name"],
                    position=99,
                    parent_id=kwargs.get("parent_id"),
                    auto_source=kwargs.get("auto_source"),
                )
            )
            await other.commit()
        return await real_create(db, **kwargs)

    monkeypatch.setattr(collection_service, "create_collection", _racing_create)

    async with async_session() as db:
        collection_id, created = await f2_runner.ensure_collection(
            db, "并发新建的夹", auto_source="douyin"
        )
        winner = (
            await db.execute(select(Collection).where(Collection.name == "并发新建的夹"))
        ).scalars().one()

    assert created is False
    assert collection_id == winner.id



def test_f2_collects_endpoint_returns_folder_list(client, monkeypatch):
    """GET /api/scraper/f2-collects：扫描（只读）返回收藏夹清单。"""
    patch_f2(
        monkeypatch,
        "list_collect_folders",
        lambda: {
            "folders": [
                {"id": "111", "name": "秘书OL", "total": 96, "last_collect_at": ""},
                {"id": "222", "name": "股票", "total": 1, "last_collect_at": ""},
            ],
            "total_folders": 2,
            "total_works": 97,
            "cookie_source": "conf/app.yaml",
        },
    )

    data = client.get("/api/scraper/f2-collects").json()

    assert data["total_folders"] == 2
    assert data["total_works"] == 97
    assert [f["name"] for f in data["folders"]] == ["秘书OL", "股票"]



def test_f2_collects_endpoint_reports_readable_error(client, monkeypatch):
    """Cookie 失效 / 风控：接口返回 400 + 可读原因（而不是 500 堆栈）。"""
    def _boom():
        raise RuntimeError("f2 配置里没有 Cookie")

    patch_f2(monkeypatch, "list_collect_folders", _boom)

    response = client.get("/api/scraper/f2-collects")

    assert response.status_code == 400
    assert "Cookie" in response.json()["detail"]



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



def test_personal_registers_bloggers_truth_table():
    """「我的列表」是否补建博主：**只有 like 可能为 True**（纯函数口径）。

    - collection：恒不补建（2026-10-01 口径，显式要求也不生效）；
    - post：那条链路的作者本来就受白名单约束，不建号（口径不同不混用）——这一格曾经是
      True，只靠调用点的 personal_mode 兜着，太容易踩，改成显式 False。
    """
    from app.services.task_runners.f2_import import personal_registers_bloggers

    assert personal_registers_bloggers("like", True) is True
    assert personal_registers_bloggers("like", False) is False
    assert personal_registers_bloggers("collection", True) is False
    assert personal_registers_bloggers("collection", False) is False
    assert personal_registers_bloggers("post", True) is False
    assert personal_registers_bloggers("post", False) is False



async def test_f2_task_register_bloggers_endpoint_backfills_and_is_idempotent(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """结果面板「登记博主」：为关掉自动登记/更早的批次手工回填，重复点幂等。"""
    from sqlalchemy import select

    from app.models.person import Blogger, InspirationBlogger

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    _stub_like_fetch(monkeypatch)
    task_id = await _run_like_import(register_bloggers=False)

    first = client.post(f"/api/scraper/f2-tasks/{task_id}/results/register-bloggers").json()
    assert first["created"] == 1 and first["linked"] == 2 and first["task_id"] == task_id

    again = client.post(f"/api/scraper/f2-tasks/{task_id}/results/register-bloggers").json()
    assert again["created"] == 0 and again["reused"] == 1
    assert again["linked"] == 0 and again["existing"] == 2  # 已建立的关联不重复计数

    async with async_session() as db:
        assert len((await db.execute(select(Blogger))).scalars().all()) == 1
        assert len((await db.execute(select(InspirationBlogger))).scalars().all()) == 2



def test_f2_register_bloggers_endpoint_404_for_other_task_type(client):
    """非 f2 任务不能借这个接口建博主。"""
    assert (
        client.post("/api/scraper/f2-tasks/999999/results/register-bloggers").status_code == 404
    )



async def test_promote_auto_registered_blogger_enters_download_whitelist(
    client, auto_settings
):
    """「纳入追踪」：source 改回 manual，之后就算已登记博主（进下载白名单）。"""
    from app.models.person import Blogger

    async with async_session() as db:
        blogger = Blogger(name="点赞过的作者", platform="douyin", source="auto_collect")
        db.add(blogger)
        await db.commit()
        await db.refresh(blogger)
        blogger_id = blogger.id

    assert f2.load_douyin_bloggers() == {}  # 自动登记：不算已登记博主

    body = client.post(f"/api/bloggers/{blogger_id}/promote").json()

    assert body["source"] == "manual"
    assert "点赞过的作者" in f2.load_douyin_bloggers()



def test_f2_status_like_availability_is_independent_of_blogger_whitelist(
    client, tmp_path, auto_settings, monkeypatch
):
    """「我的喜欢」可用性只取决于 f2 + 目录 + 主页链接，与「已登记博主」白名单无关。

    否则「库里没登记抖音博主」的用户会被按钮挡住——而点赞列表本来就跨作者。
    """
    f2_dir = tmp_path / "f2proj"
    f2_dir.mkdir()
    patch_f2(monkeypatch, "f2_available", lambda: True)
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", f2_dir)
    patch_f2(monkeypatch, "DEFAULT_F2_ROOT", f2_dir / "Download")

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

