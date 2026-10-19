"""f2 导入任务 API 测试（创建任务、点赞/收藏参数、按作者选择、合集归位）。"""

import sqlite3
import pytest
from app.database import async_session
from app.services import task_runner
from app.services.task_runners import f2_import as f2_runner
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


async def test_collect_stage_aggregates_into_douyin_root_collection(client, upload):
    """没有归属信息的素材进二级「未分类收藏」，**一级恒为空**（分类节点不装素材）。

    回归：早先这批没有归属的素材被塞进一级，于是「抖音入库自动收藏」又变成一个
    几千件混在一起的大列表——用户口径是一级不允许有素材加进来。
    """
    from sqlalchemy import select

    from app.models.collection import Collection, CollectionItem
    from app.models.task import TaskQueue

    ids = [upload().json()["id"] for _ in range(2)]

    async with async_session() as db:
        task = TaskQueue(
            type="f2_import", status="running", progress=90, total=2, done=2,
            result={}, max_retries=1,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        stats = await f2_runner._aggregate_collect_stage(db, task, {"ids": ids})
        assert stats is not None
        assert stats["name"] == "抖音入库自动收藏"
        assert stats["added"] == 0, "一级不装素材"
        assert stats["unclassified"] == 2
        assert stats["folder_count"] == 1  # 只有「未分类收藏」

        # 幂等：同一批再跑一次不重复加入
        again = await f2_runner._aggregate_collect_stage(db, task, {"ids": ids})
        assert again["created"] is False
        assert again["folders"][0]["added"] == 0
        assert again["folders"][0]["skipped"] == 2

        root = (
            await db.execute(
                select(Collection).where(Collection.name == "抖音入库自动收藏")
            )
        ).scalars().one()
        assert root.parent_id is None
        assert root.auto_source == "douyin"
        unc = (
            await db.execute(
                select(Collection).where(
                    Collection.parent_id == root.id,
                    Collection.name == f2_runner.UNCLASSIFIED_COLLECTION_NAME,
                )
            )
        ).scalars().one()
        items = (
            await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == unc.id
                )
            )
        ).scalars().all()
        assert set(items) == set(ids)



async def test_collect_stage_builds_second_level_from_folder_map(client):
    """有归属清单时建二级收藏夹：名字 = 抖音收藏夹名；一级只当分类、不装作品。

    这是本次修复的核心：原先所有收藏作品都倒进一个扁平合集（2658 条混在一起），
    现在二级对应抖音收藏夹，一级点进去看到的是收藏夹而不是混合列表。
    """
    from sqlalchemy import select

    from app.models.collection import Collection, CollectionItem
    from app.models.inspiration import Inspiration
    from app.models.task import TaskQueue

    # 两件已在库的收藏作品（平台 ID 形态与真实数据一致，供按作品 ID 反查）
    async with async_session() as db:
        for aweme_id in ("7400000000000000001", "7400000000000000002"):
            db.add(
                Inspiration(
                    id=f"collect-{aweme_id}",
                    source_type="douyin",
                    source_platform_id=f"f2:{aweme_id}#image_1",
                    source_url=f"https://www.douyin.com/note/{aweme_id}",
                    file_path=f"images/2026-09/{aweme_id}.webp",
                    media_type="image",
                )
            )
        await db.commit()

    async with async_session() as db:
        task = TaskQueue(
            type="f2_import", status="running", progress=90, total=0, done=0,
            result={}, max_retries=1,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        stats = await f2_runner._aggregate_collect_stage(
            db,
            task,
            {"ids": []},
            extra_ids=["collect-7400000000000000001", "collect-7400000000000000002"],
            folder_map={
                "秘书OL": ["7400000000000000001"],
                "股票": ["7400000000000000002"],
                # 清单里有、但库里没有的夹：不建空合集
                "哲学": ["7400000000000000999"],
            },
        )

        assert stats is not None
        assert stats["name"] == "抖音入库自动收藏"
        assert stats["folder_count"] == 2
        assert {f["name"] for f in stats["folders"]} == {"秘书OL", "股票"}
        assert stats["attributed"] == 2
        # 两件作品都有归属 → 没有「未分类」这一项
        assert stats["unclassified"] == 0

        root = (
            await db.execute(
                select(Collection).where(Collection.name == "抖音入库自动收藏")
            )
        ).scalars().one()
        children = (
            await db.execute(
                select(Collection).where(Collection.parent_id == root.id)
            )
        ).scalars().all()
        assert {c.name for c in children} == {"秘书OL", "股票"}
        assert all(c.auto_source == "douyin" for c in children)

        async def _members(cid: int) -> set[str]:
            rows = await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == cid
                )
            )
            return set(rows.scalars().all())

        # 一级只当分类（不汇总），作品各自归位到对应收藏夹
        assert await _members(root.id) == set()
        by_name = {c.name: await _members(c.id) for c in children}
        assert by_name == {
            "秘书OL": {"collect-7400000000000000001"},
            "股票": {"collect-7400000000000000002"},
        }

    # 平铺下载（无归属清单）时进二级「未分类收藏」：没有夹可归，但也不能塞进一级
    async with async_session() as db:
        task = TaskQueue(
            type="f2_import", status="running", progress=90, total=0, done=0,
            result={}, max_retries=1,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        flat = await f2_runner._aggregate_collect_stage(
            db,
            task,
            {"ids": []},
            extra_ids=["collect-7400000000000000002"],
            folder_map={},
        )
        assert flat["folder_count"] == 1
        assert flat["unclassified"] == 1
        assert flat["added"] == 0
        root_id = flat["id"]
        unc_id = flat["folders"][0]["id"]
        assert flat["folders"][0]["name"] == f2_runner.UNCLASSIFIED_COLLECTION_NAME
        members = (
            await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == unc_id
                )
            )
        ).scalars().all()
        assert list(members) == ["collect-7400000000000000002"]
        # 一级仍然是空的
        assert (
            await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == root_id
                )
            )
        ).scalars().all() == []



async def test_collect_stage_noop_without_imported_ids(client):
    """没有入库素材、也没有收藏夹归属时不写结果（不产生空合集聚合）。"""
    from app.models.task import TaskQueue

    async with async_session() as db:
        task = TaskQueue(
            type="f2_import", status="running", progress=90, total=0, done=0,
            result={}, max_retries=1,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        assert await f2_runner._aggregate_collect_stage(db, task, {"ids": []}) is None
        assert "collection" not in (task.result or {})



async def test_collect_stage_failure_lands_in_notices_not_error(client):
    """归位失败属于「任务成功但有话要说」：必须进 result.notices。

    回归：原先写 task.error，而 worker 在任务正常返回时会把 error 清成 None
    （``app/worker.py`` 成功分支），于是这条提示**从来没被用户看到过**。
    """
    from app.models.task import TaskQueue
    from app.services import collection_service

    async def _boom(*_a, **_k):
        raise RuntimeError("模拟归位失败")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(collection_service, "add_inspirations", _boom)
    try:
        async with async_session() as db:
            task = TaskQueue(
                type="f2_import", status="running", progress=90, total=0, done=0,
                result={}, max_retries=1,
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)

            result = await f2_runner._aggregate_collect_stage(
                db, task, {"ids": []}, extra_ids=["x"], folder_map={"秘书OL": ["1"]}
            )
        assert result is None
        assert task.error is None, "不能再依赖 error：worker 成功时会清掉它"
        notices = (task.result or {}).get("notices", [])
        assert any(n.startswith("收藏夹归位失败（素材已入库）") for n in notices)
        # 原因要带上（否则用户只看到「失败了」却不知道该怎么办）
        assert "模拟归位失败" in notices[0]
    finally:
        monkeypatch.undo()



def test_f2_import_status_reports_missing_f2(monkeypatch):
    """f2 未安装：给出安装指引（CI 等未装 f2 的环境走的就是这条分支）。"""
    patch_f2(monkeypatch, "f2_available", lambda: False)

    status = task_runner.f2_import_status()

    assert status["available"] is False
    assert "未检测到 f2" in status["reason"]
    # 按博主全量下载同样不可用（它只依赖 f2 + 工作目录，比发布模式宽松）
    assert status["profiles_available"] is False
    assert "未检测到 f2" in status["profiles_reason"]



def test_f2_import_status_reports_reason_when_unavailable(tmp_path, monkeypatch):
    """f2 已装但工作目录不存在：报「未找到 f2 工作目录」而不是安装指引。"""
    patch_f2(monkeypatch, "f2_available", lambda: True)
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", tmp_path / "不存在")

    status = task_runner.f2_import_status()

    assert status["available"] is False
    assert "未找到 f2 工作目录" in status["reason"]



def test_f2_import_status_available_with_authors(tmp_path, monkeypatch):
    """f2 已装 + 目录存在 + 作者库有账号：可用。"""
    f2_dir = tmp_path / "f2proj"
    f2_dir.mkdir()

    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.execute("INSERT INTO user_info_web VALUES ('sec1', '里香1√', 171)")
    conn.commit()
    conn.close()
    patch_f2(monkeypatch, "f2_available", lambda: True)
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", f2_dir)
    patch_f2(monkeypatch, "DEFAULT_F2_ROOT", f2_dir / "Download")

    status = task_runner.f2_import_status()

    assert status["available"] is True
    assert status["authors"] == 1
    assert "1 个" in status["reason"]



def test_select_post_targets_parses_profiles_and_rejects_bad_input():
    """阶段 1b 目标选择（纯函数）：点名博主可解析主页链接/sec_user_id；
    抖音号与短链明确报错——不接受「拼一个必然失败的 URL」再静默失败。"""
    from app.services.task_runners.f2_import import _select_post_targets

    sec = "MS4wLjABAAAAexample01"
    targets = _select_post_targets(
        [f"https://www.douyin.com/user/{sec}", sec], [], None, None
    )
    assert [a["sec_user_id"] for a in targets] == [sec, sec]

    with pytest.raises(RuntimeError, match="无法识别的博主"):
        _select_post_targets(["72906514384"], [], None, None)



def test_select_post_targets_fails_loudly_when_named_authors_not_in_f2_db():
    """点名作者一个都不在 f2 用户库：响亮失败（任务 351 曾静默「成功 + 入库 0」）。

    fetch_limit 只截断目标数量（试跑用），不影响上面的失败判定。
    """
    from app.services.task_runners.f2_import import _select_post_targets

    known = [{"sec_user_id": "s1", "nickname": "里香", "aweme_count": 10}]
    with pytest.raises(RuntimeError, match="不在 f2 用户库里"):
        _select_post_targets([], known, {"唐思瑶ya"}, None)

    three = [
        {"sec_user_id": f"s{i}", "nickname": f"博主{i}", "aweme_count": 1}
        for i in range(3)
    ]
    assert len(_select_post_targets([], three, None, 2)) == 2



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



@pytest.mark.parametrize(
    "query",
    [
        # 文档口径：逗号分隔（前端现在显式拼串，就用这个）
        "collect_ids=111,222",
        # 重复键：其它客户端 / 手写 curl 常见写法
        "collect_ids=111&collect_ids=222",
        # **axios 默认的数组序列化**——真实事故的形态，必须兼容
        "collect_ids[]=111&collect_ids[]=222",
    ],
)
def test_create_f2_import_task_accepts_every_collect_ids_encoding(client, query):
    """三种收藏夹 ID 编码都必须解析出同一批夹。

    回归（真实事故）：前端用 axios 的 `params` 传数组 → `collect_ids[]=111&...`，
    而后端参数名是 `collect_ids`（无方括号）→ **永远收到空**。于是「我勾了 19 个收藏夹」
    被后端理解成「一个都没勾」：旧代码退回平铺收藏、把整棵收藏目录收进素材库
    （任务 #386/#387），加上硬前置之后又变成任务一开始就失败（#389）。

    ⚠ 用 `fetch=false` 是有意的：`fetch=true` 时路由**先做 f2 可用性预检**，CI 的
    Linux runner 没装 f2 → 直接返回「未检测到 f2」而不建任务（这条用例只在本地绿、
    CI 红过一次）。这里验的是「查询串怎么解析、怎么落到任务参数」，与下载可用性无关，
    与本文件既有约定一致（见 test_create_f2_import_task_endpoint_accepts_since_days）。
    """
    resp = client.post(f"/api/scraper/f2-import?fetch=false&mode=collection&{query}")
    body = resp.json()
    assert body["task_id"], body

    opts = client.get(f"/api/tasks/{body['task_id']}").json()["result"]
    assert opts["collect_ids"] == ["111", "222"]



def test_create_f2_import_task_accepts_axios_profiles_array(client):
    """「按博主全量下载」的 profiles 同样要认 axios 的数组写法（同一个根因）。

    `fetch=false` 同样是为了跳过 f2 可用性预检（见上一条用例的说明）。
    """
    resp = client.post(
        "/api/scraper/f2-import?fetch=false"
        "&profiles[]=https://www.douyin.com/user/sec1"
        "&profiles[]=https://www.douyin.com/user/sec2"
    )
    body = resp.json()
    assert body["task_id"], body
    opts = client.get(f"/api/tasks/{body['task_id']}").json()["result"]
    assert opts["profiles"] == [
        "https://www.douyin.com/user/sec1",
        "https://www.douyin.com/user/sec2",
    ]



async def test_create_f2_import_task_endpoint_forwards_allow_all_collect(client):
    """接口层的 `allow_all_collect` 必须真的落到任务参数里。

    这是「确认后才允许导入全部收藏」这条安全链的**接线点**：前端点了确认 → 带上参数 →
    路由 → create_f2_import_task_if_idle(**kwargs) → 任务 result。任何一环改名都会让
    用户确认过的操作仍然被拒（fail-closed，不会误导入，但功能静默失效）。

    `fetch=false` 是为了跳过路由的 f2 可用性预检（CI 上没有 f2，`fetch=true` 会直接
    返回「未检测到 f2」而不建任务）；参数的透传路径与 `fetch` 取值无关。
    """
    from app.models.task import TaskQueue

    body = client.post(
        "/api/scraper/f2-import",
        params={"fetch": False, "mode": "collection", "allow_all_collect": True},
    ).json()
    assert body["task_id"], body
    opts = client.get(f"/api/tasks/{body['task_id']}").json()["result"]
    assert opts["fetch_mode"] == "collection"
    assert opts["allow_all_collect"] is True

    # 收尾掉第一个任务（并发保护会复用进行中的任务，见 create_f2_import_task_if_idle），
    # 再建一个不传该参数的：默认必须是 false（平铺收藏被拒的那条路径）
    async with async_session() as db:
        first = await db.get(TaskQueue, body["task_id"])
        first.status = "success"
        await db.commit()

    body = client.post(
        "/api/scraper/f2-import", params={"fetch": False, "mode": "collection"}
    ).json()
    assert body["task_id"], body
    opts = client.get(f"/api/tasks/{body['task_id']}").json()["result"]
    assert opts["allow_all_collect"] is False



def test_set_like_max_counts_endpoint_persists(client, monkeypatch):
    """保存端点：写 settings + 落 .env（测试里打桩落盘，不碰真实 .env）。"""
    from app.config import settings

    written: list[dict] = []

    async def fake_update(payload: dict) -> None:
        written.append(payload)

    monkeypatch.setattr("app.routers.ai_shared._update_env_file", fake_update)

    resp = client.put("/api/scraper/f2-like-max-counts", params={"max_counts": 150})
    assert resp.status_code == 200
    body = resp.json()
    assert body["like_max_counts"] == 150
    assert "150" in body["message"]
    assert settings.f2_like_max_counts == 150
    assert written and written[0]["F2_LIKE_MAX_COUNTS"] == "150"

    # 0 = 恢复全量
    zero = client.put("/api/scraper/f2-like-max-counts", params={"max_counts": 0}).json()
    assert zero["like_max_counts"] == 0
    assert "全量" in zero["message"]
    assert settings.f2_like_max_counts == 0



def test_set_like_max_counts_endpoint_rejects_negative(client):
    assert (
        client.put("/api/scraper/f2-like-max-counts", params={"max_counts": -1}).status_code
        == 422
    )



def test_create_f2_import_task_endpoint_accepts_like_max_counts(client):
    """点赞增量条数透传到任务参数；越界同样被拦住。"""
    body = client.post(
        "/api/scraper/f2-import", params={"fetch": False, "mode": "like", "like_max_counts": 88}
    ).json()
    opts = client.get(f"/api/tasks/{body['task_id']}").json()["result"]
    assert opts["like_max_counts"] == 88 and opts["fetch_mode"] == "like"

    assert (
        client.post(
            "/api/scraper/f2-import", params={"fetch": False, "like_max_counts": -1}
        ).status_code
        == 422
    )



def test_create_f2_import_task_endpoint_accepts_profiles(client):
    """profiles 透传到任务参数（逗号/空格分隔都认）。"""
    body = client.post(
        "/api/scraper/f2-import",
        params={
            "fetch": False,
            "profiles": f"https://www.douyin.com/user/{SEC}, MS4wLjABAAAAother0000",
        },
    ).json()
    opts = client.get(f"/api/tasks/{body['task_id']}").json()["result"]

    assert opts["profiles"] == [f"https://www.douyin.com/user/{SEC}", "MS4wLjABAAAAother0000"]



def test_create_f2_import_rejects_when_unavailable(client, tmp_path, monkeypatch):
    """fetch=True 但环境不可用时：不建任务，返回原因（供前端提示）。"""
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", tmp_path / "不存在")
    resp = client.post("/api/scraper/f2-import", params={"fetch": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] is None
    assert "f2" in body["message"]



def test_create_f2_import_reuses_running_task(client):
    """修复（审查 L4）：已有进行中的任务时直接复用，避免连点起多个任务。"""
    first = client.post("/api/scraper/f2-import", params={"fetch": False}).json()
    second = client.post("/api/scraper/f2-import", params={"fetch": False}).json()

    assert first["task_id"] and second["task_id"] == first["task_id"]
    assert second.get("reused") is True
    assert "进行中" in second["message"]



async def test_create_f2_import_task_if_idle_reuses_running(client):
    """并发保护入口：已有进行中任务时返回其 id、不新建。"""
    first = client.post("/api/scraper/f2-import", params={"fetch": False}).json()["task_id"]

    async with async_session() as db:
        task, running_id = await task_runner.create_f2_import_task_if_idle(db, fetch=False)

    assert task is None and running_id == first



def test_create_f2_import_passes_include_unknown(client):
    """高级选项「包含未登记账号」要能透传到任务参数（执行阶段据此放开过滤）。"""
    body = client.post(
        "/api/scraper/f2-import",
        params={"fetch": False, "include_unknown_authors": True},
    ).json()

    opts = client.get(f"/api/tasks/{body['task_id']}").json()["result"]

    assert opts["include_unknown_authors"] is True



async def test_collect_mode_without_folder_selection_is_rejected(
    client, auto_settings, monkeypatch
):
    """收藏模式**没勾收藏夹**时直接失败，一个文件都不下也不入库。

    回归（实测两次）：任务 #386、#387 都是「平铺收藏」——没带 collect_ids，于是把
    整棵收藏目录（用户明确不想要的股票/哲学夹也在内）各收了 2627 件进素材库。
    现在平铺路径默认拒绝，必须显式 `allow_all_collect=true` 才放行。
    """
    from app.models.inspiration import Inspiration
    from app.models.task import TaskQueue
    from app.worker import _claim_next_task, _run_task_safe
    from sqlalchemy import select

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    patch_f2(monkeypatch, "f2_available", lambda: True)
    patch_f2(
        monkeypatch,
        "run_fetch_collects",
        lambda *_a, **_k: pytest.fail("被拒绝的任务不该真的去下载"),
    )
    patch_f2(
        monkeypatch,
        "download_collect_folders",
        lambda *_a, **_k: pytest.fail("没勾收藏夹时不该走逐夹下载"),
    )

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=True, fetch_mode="collection")
        task_id = task.id

    # 走真实 worker 收尾：状态与错误文案由 worker 落库
    assert await _claim_next_task("worker-review") == task_id
    await _run_task_safe(task_id)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.status == "failed"
        assert "需要先选收藏夹" in (stored.error or "")
        assert (stored.result or {}).get("selection_required") is True
        assert (
            await db.execute(select(Inspiration).where(Inspiration.deleted_at.is_(None)))
        ).scalars().all() == []



async def test_collect_mode_allow_all_puts_everything_in_unclassified(
    client, f2_like_tree, auto_settings, monkeypatch
):
    """显式确认「全部收藏」时：能导入，但素材进二级「未分类收藏」，**一级恒为空**。

    用户口径：一级收藏夹不允许有素材加进来（它是分类节点）。
    """
    from sqlalchemy import select

    from app.models.collection import Collection, CollectionItem
    from app.models.task import TaskQueue

    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"
    calls: dict = {}
    _stub_collect_fetch(monkeypatch, calls)
    patch_f2(monkeypatch, "DEFAULT_F2_COLLECT_ROOT", f2.DEFAULT_F2_LIKE_ROOT)

    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=True, fetch_mode="collection", allow_all_collect=True
        )
        task_id = task.id
        await task_runner.execute_f2_import(db, task)

    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["import"]["imported"] == 2
        stats = stored.result["collection"]
        assert stats["added"] == 0, "一级不允许有素材"
        assert stats["unclassified"] == 2
        assert stats["folder_count"] == 1  # 只有「未分类收藏」这一个二级
        assert stats["folders"][0]["name"] == "未分类收藏"

        root = (
            await db.execute(
                select(Collection).where(Collection.name == "抖音入库自动收藏")
            )
        ).scalars().one()
        root_members = (
            await db.execute(
                select(CollectionItem.inspiration_id).where(
                    CollectionItem.collection_id == root.id
                )
            )
        ).scalars().all()
        assert list(root_members) == [], "一级收藏夹必须是空的"

        unc = (
            await db.execute(
                select(Collection).where(
                    Collection.parent_id == root.id,
                    Collection.name == "未分类收藏",
                )
            )
        ).scalars().one()
        assert len(
            (
                await db.execute(
                    select(CollectionItem.inspiration_id).where(
                        CollectionItem.collection_id == unc.id
                    )
                )
            ).scalars().all()
        ) == 2



def test_create_f2_import_like_mode_passes_params(client):
    """API 透传 mode/like_user（任务参数里能查到，执行阶段据此走点赞链路）。"""
    body = client.post(
        "/api/scraper/f2-import",
        params={"fetch": False, "mode": "like", "like_user": "MS4wLjABAAAAme"},
    ).json()

    opts = client.get(f"/api/tasks/{body['task_id']}").json()["result"]

    assert opts["fetch_mode"] == "like"
    assert opts["like_user"] == "MS4wLjABAAAAme"



def test_f2_import_like_mode_not_blocked_by_blogger_whitelist(
    client, tmp_path, create_blogger, auto_settings, monkeypatch
):
    """点赞入口不被「已登记博主」白名单挡住（回归：曾沿用发布模式的 available 判定）。

    同一个环境（f2 已装 + 工作目录在 + 用户库里有账号，但那个账号**没有**对应到
    已登记博主）下：发布模式应拒绝并说明白名单原因，点赞模式（已配置主页链接）
    应照常建任务——点赞列表本来就跨作者，与白名单无关。
    """

    f2_dir = tmp_path / "f2proj"
    f2_dir.mkdir()
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.execute("INSERT INTO user_info_web VALUES ('sec-x', '某未登记号', 3)")
    conn.commit()
    conn.close()
    patch_f2(monkeypatch, "f2_available", lambda: True)
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", f2_dir)
    patch_f2(monkeypatch, "DEFAULT_F2_ROOT", f2_dir / "Download")
    create_blogger("里香", platform="douyin")  # 白名单生效，但不含 f2 里的账号
    auto_settings.f2_like_user = "https://www.douyin.com/user/MS4wLjABAAAAme"

    blocked = client.post("/api/scraper/f2-import", params={"fetch": True}).json()
    assert blocked["task_id"] is None
    assert "已登记" in blocked["message"]  # 发布模式：讲的是博主白名单

    created = client.post(
        "/api/scraper/f2-import", params={"fetch": True, "mode": "like"}
    ).json()
    assert created["task_id"] is not None

    # 未配置主页链接时，点赞入口才被自己的理由挡住
    auto_settings.f2_like_user = ""
    blocked_like = client.post(
        "/api/scraper/f2-import", params={"fetch": True, "mode": "like"}
    ).json()
    assert blocked_like["task_id"] is None
    assert "我的主页链接" in blocked_like["message"]



def test_f2_import_profiles_not_blocked_by_f2_user_library(
    client, tmp_path, create_blogger, monkeypatch
):
    """按博主全量下载（profiles）不被「f2 用户库 / 已登记博主白名单」挡住。

    回归（2026-09-27 给博主详情页加「下载所有作品」时发现）：这个入口的用途正是
    「点名一个**还没进 f2 用户库**的博主，下她全部作品」（见 routers/scraper.py 的
    profiles 说明），而可用性判定原先沿用发布模式的 available——它要求 f2 用户库非空、
    且至少一个账号对应到已登记博主，于是这个入口在它最该起作用的场景被拒。
    点赞入口栽过同一个坑（见上一用例），这里是同一根因的第四个可用性口径。
    """

    f2_dir = tmp_path / "f2proj"
    f2_dir.mkdir()
    # 用户库存在但为空：发布模式没有账号可下 → available=False
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.commit()
    conn.close()
    patch_f2(monkeypatch, "f2_available", lambda: True)
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", f2_dir)
    patch_f2(monkeypatch, "DEFAULT_F2_ROOT", f2_dir / "Download")
    create_blogger("唐思瑶ya", platform="douyin")
    profile = f"https://www.douyin.com/user/{SEC}"

    # 口径差异：发布模式不可用（用户库空），按博主全量下载可用
    status = task_runner.f2_import_status()
    assert status["available"] is False
    assert status["profiles_available"] is True
    assert "点名" in status["profiles_reason"]

    blocked = client.post("/api/scraper/f2-import", params={"fetch": True}).json()
    assert blocked["task_id"] is None
    assert "用户库为空" in blocked["message"]

    created = client.post(
        "/api/scraper/f2-import", params={"fetch": True, "profiles": profile}
    ).json()
    assert created["task_id"] is not None, created
    assert created["message"].startswith("已提交")

