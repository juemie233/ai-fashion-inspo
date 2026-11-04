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

