"""f2 一键获取素材任务（task type: f2_import）测试：API 创建 / 可用性 / 执行接线。

不真跑 f2、不真下抖音：下载阶段用 monkeypatch 关掉（fetch=False）或打桩子进程，
执行阶段用临时 f2 目录 + 项目自带的临时素材库（conftest 已隔离）。
"""

import asyncio
import json
import os
import time
from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image

from app.database import async_session
from app.services import task_runner
from app.services.task_runners import f2_import as f2_runner
from app.services.task_runners.common import utcnow
from f2_patch import patch_f2
from scripts import import_f2_downloads as f2


@pytest.fixture(autouse=True)
def isolate_personal_roots(tmp_path, monkeypatch):
    """把「我的喜欢 / 我的收藏」两个产物根目录都指到临时目录（本模块 autouse）。

    为什么必须两侧一起 patch：个人列表模式下载结束后会自动跑一次跨模式重复合并
    （见 ``f2.merge_personal_duplicates``），它按默认值读这两个根目录。本模块的
    「我的喜欢」用例只 patch like 侧（``f2_like_tree``），不一起 patch collect 侧
    就会让合并去扫用户**真实的** ``Download/douyin/collection``（实测：全量套件里
    11 次调用带着真实收藏目录）。这里统一指到临时目录，越界由 conftest 的
    ``guard_f2_merge_off_real_roots`` 断言兜底。
    """
    like_root = tmp_path / "like"
    collect_root = tmp_path / "collection"
    like_root.mkdir()
    collect_root.mkdir()
    patch_f2(monkeypatch, "DEFAULT_F2_LIKE_ROOT", like_root)
    patch_f2(monkeypatch, "DEFAULT_F2_COLLECT_ROOT", collect_root)


def _jpeg(path: Path, color: str = "red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 48), color).save(path, "JPEG")
    return path


async def test_status_exposes_collect_fields(client):
    """可用性状态要带收藏模式的字段（前端据此门控「采集我的收藏」）。"""
    from app.services.task_runner import f2_import_status

    info = f2_import_status()
    assert "collect_root" in info and "collect_available" in info and "collect_reason" in info
    assert str(info["collect_root"]).endswith("collection")
    # 点赞与收藏的前提相同（f2 + 工作目录 + 已配置主页链接）
    assert info["collect_available"] == info["like_available"]


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
        assert "收藏夹归位失败（素材已入库）" in (task.result or {}).get("notices", [])
    finally:
        monkeypatch.undo()


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


@pytest.fixture
def f2_tree(tmp_path, monkeypatch):
    """把 f2 的工作目录/下载目录指向临时目录，并放两个作品的文件。"""
    f2_dir = tmp_path / "f2proj"
    root = f2_dir / "Download" / "douyin" / "post"
    _jpeg(root / "里香1√" / "2025-01-01 10-00-00_#jk_标题_image_1.jpg")
    _jpeg(root / "里香1√" / "2025-01-01 10-00-00_#jk_标题_image_2.jpg", "blue")
    _jpeg(root / "某博主" / "2025-02-02 11-00-00_#通勤_标题_video.mp4".replace(".mp4", ".jpg"), "green")
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", f2_dir)
    patch_f2(monkeypatch, "DEFAULT_F2_ROOT", root)
    return f2_dir, root


# ── 可用性检查 ──
# 注意：f2_import_status 的分支顺序是「f2 是否安装 → 工作目录是否存在 → 作者库」，
# 而 f2_available() 取决于**运行环境是否装了 f2**（本机装了、CI 没装）。因此本组
# 用例一律显式打桩 f2_available，否则在 CI（无 f2）会先命中「未检测到 f2」分支。


def test_f2_import_status_reports_missing_f2(monkeypatch):
    """f2 未安装：给出安装指引（CI 等未装 f2 的环境走的就是这条分支）。"""
    patch_f2(monkeypatch, "f2_available", lambda: False)

    status = task_runner.f2_import_status()

    assert status["available"] is False
    assert "未检测到 f2" in status["reason"]


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
    import sqlite3

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


def _make_f2_author_db(f2_dir, rows: list[tuple[str, str, int]]) -> None:
    """造一个 f2 用户库（douyin_users.db），rows 为 (sec_user_id, nickname, aweme_count)。"""
    import sqlite3

    f2_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.executemany("INSERT INTO user_info_web VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


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


# ── 「我的喜欢」增量翻页（like_max_counts / -o）：f2 点赞分页无「遇到已下载就停」──


def test_f2_status_exposes_like_max_counts(client):
    """状态里要带 like_max_counts，前端才能显示「增量 N 条 / 全量」。"""
    body = client.get("/api/scraper/f2-status").json()
    assert "like_max_counts" in body
    assert isinstance(body["like_max_counts"], int)


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

    monkeypatch.setattr(runner, "_watch_personal_download", fake_download)

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


# ── 按博主全量下载（profiles：给一个博主 → 下她全部作品）──

SEC = "MS4wLjABAAAACyG6qmWLGt5BbCvwkAfMpEf3nhGwlQqSG1MjwDIGokuUHJnIkwJzxDPu-1RRrfvk"


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
        runner, "_run_subprocess", lambda cmd, cwd: (commands.append(cmd) or 0)
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

    monkeypatch.setattr(runner, "_run_subprocess", lambda cmd, cwd: 0)
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
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", tmp_path / "不存在")
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

    patch_f2(monkeypatch, "f2_available", lambda: True)
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
    patch_f2(monkeypatch, "f2_available", lambda: True)
    return f2_dir


@pytest.fixture
def auto_settings(monkeypatch):
    """隔离自动获取与「我的喜欢」相关配置项：用例结束后还原（含 API 直接改 settings 的情况）。"""
    from app.config import settings

    original = (
        settings.f2_import_auto_enabled,
        settings.f2_import_interval_hours,
        settings.f2_import_auto_skip_live,
        settings.f2_import_auto_mode,
        settings.f2_fetch_since_days,
        settings.f2_like_user,
    )
    yield settings
    (
        settings.f2_import_auto_enabled,
        settings.f2_import_interval_hours,
        settings.f2_import_auto_skip_live,
        settings.f2_import_auto_mode,
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
    patch_f2(monkeypatch, "f2_available", lambda: True)

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

    patch_f2(monkeypatch, "f2_available", lambda: True)
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


# ── 「我的喜欢」（点赞模式）──


@pytest.fixture
def f2_like_tree(tmp_path, monkeypatch):
    """把「我的喜欢」产物根指向临时目录：一个作品两张图，文件名带原作者前缀。"""
    like_root = tmp_path / "like"
    author_dir = like_root / "我的账号"  # f2 把喜欢的作品统统下在「我的昵称」目录下
    _jpeg(author_dir / "不养羊_2026-09-14 10-31-14_下一站再见吧#地铁jk_#jk_image_1.jpg")
    _jpeg(author_dir / "不养羊_2026-09-14 10-31-14_下一站再见吧#地铁jk_#jk_image_2.jpg", "blue")
    patch_f2(monkeypatch, "DEFAULT_F2_LIKE_ROOT", like_root)
    return like_root


def _stub_like_fetch(monkeypatch, calls: dict | None = None):
    """打桩 f2 的点赞抓取：只记录入参并返回成功，不真的跑 f2。"""
    patch_f2(monkeypatch, "f2_available", lambda: True)

    def _fake(f2_dir, like_user, download_root=None, **kwargs):
        if calls is not None:
            calls["like_user"] = like_user
            calls["download_root"] = str(download_root)
            calls["max_counts"] = kwargs.get("max_counts")
        return {
            "total": 1,
            "ok": 1,
            "failed": 0,
            "results": [{"nickname": "我的喜欢", "rc": 0, "cmd": "f2 dy -M like"}],
        }

    patch_f2(monkeypatch, "run_fetch_likes", _fake)


def _stub_collect_fetch(monkeypatch, calls: dict | None = None):
    """打桩 f2 的收藏抓取：只记录入参并返回成功，不真的跑 f2。"""
    patch_f2(monkeypatch, "f2_available", lambda: True)

    def _fake(f2_dir, collect_user, download_root=None, **kwargs):
        if calls is not None:
            calls["fetch"] = "collect"
            calls["like_user"] = collect_user
            calls["download_root"] = str(download_root)
            calls["max_counts"] = kwargs.get("max_counts")
        return {
            "total": 1,
            "ok": 1,
            "failed": 0,
            "results": [{"nickname": "我的收藏", "rc": 0, "cmd": "f2 dy -M collection"}],
        }

    patch_f2(monkeypatch, "run_fetch_collects", _fake)


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


def _write_collect_folder_map(root, folders: dict) -> None:
    """写收藏夹归属清单（格式契约见 scripts/f2_collects.py 的 ``_merge_folder_map``）。

    Args:
        root: 收藏产物根目录（清单写在 ``{root}/_collect_folders.json``）。
        folders: ``{收藏夹 ID: (收藏夹名, [作品 ID, ...])}``。
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "_collect_folders.json").write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-09-23 16:00:00",
                "folders": {
                    str(fid): {"name": name, "aweme_ids": list(ids)}
                    for fid, (name, ids) in folders.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _stub_collect_folder_download(monkeypatch, calls: dict):
    """打桩「按选中收藏夹下载」：往收藏产物根写一张真图 + 归属清单，模拟 f2 落盘。"""
    patch_f2(monkeypatch, "f2_available", lambda: True)
    # 平铺链路必须**不被走到**（走到就说明 collect_ids 没生效）
    patch_f2(
        monkeypatch,
        "run_fetch_collects",
        lambda *_a, **_k: pytest.fail("选中收藏夹时不该走平铺收藏命令"),
    )

    def _fake(f2_dir, user, collect_ids, max_counts=0, should_stop=None, on_progress=None,
              existing_aweme_ids=None, link_from_root=None):
        calls["collect_ids"] = list(collect_ids)
        calls["max_counts"] = max_counts
        calls["user"] = user
        calls["existing_aweme_ids"] = set(existing_aweme_ids or set())
        calls["link_from_root"] = str(link_from_root or "")
        root = f2.DEFAULT_F2_COLLECT_ROOT / "我的账号"
        _jpeg(root / "不养羊_2026-09-14 10-31-14_下一站再见吧#jk_7670881947199742833_image_1.jpg")
        # 真实下载会把「作品 ID → 收藏夹」写进清单；入库阶段按它限定范围与建二级
        _write_collect_folder_map(
            root, {collect_ids[0]: ("秘书OL", ["7670881947199742833"])}
        )
        stats = {
            "cookie_source": "假配置",
            "root": str(root),
            "folders": [{"id": collect_ids[0], "name": "秘书OL", "total": 1, "works": 1,
                         "skipped_existing": 0}],
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
    monkeypatch.setattr(f2_runner, "_WATCH_INTERVAL", 0.01)
    monkeypatch.setattr(f2_runner, "_LIKE_PROGRESS_HALF_SECONDS", 1.0)

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
            info = (task.result or {}).get("like_progress")
            if info:
                live.append((task.progress, dict(info)))
            return real_stats(root)

        patch_f2(monkeypatch, "download_tree_stats", _spy_stats)
        await task_runner.execute_f2_import(db, task)

    assert live, "下载期间没有写入任何 like_progress 快照"
    assert any(progress > 0 for progress, _ in live), "软进度始终为 0%"
    assert any(info["files"] == 3 for _, info in live), "实时计数没有反映新落盘的文件"
    assert max(info["added"] for _, info in live) == 1

    # 下载产出统计保留到任务结果里（入库阶段的 plan 只讲「有多少要入库」）
    async with async_session() as db:
        stored = await db.get(TaskQueue, task_id)
        assert stored.result["fetch"]["downloaded"]["added"] == 1
        assert stored.result["import"]["imported"] == 3


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


async def _run_like_import(**kwargs) -> int:
    """跑一次「我的喜欢」导入（临时目录 + 临时库），返回任务 id。"""
    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=True, fetch_mode="like", **kwargs
        )
        task_id = task.id
        await task_runner.execute_f2_import(db, task)
    return task_id


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


def test_f2_import_like_mode_not_blocked_by_blogger_whitelist(
    client, tmp_path, create_blogger, auto_settings, monkeypatch
):
    """点赞入口不被「已登记博主」白名单挡住（回归：曾沿用发布模式的 available 判定）。

    同一个环境（f2 已装 + 工作目录在 + 用户库里有账号，但那个账号**没有**对应到
    已登记博主）下：发布模式应拒绝并说明白名单原因，点赞模式（已配置主页链接）
    应照常建任务——点赞列表本来就跨作者，与白名单无关。
    """
    import sqlite3

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
