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
