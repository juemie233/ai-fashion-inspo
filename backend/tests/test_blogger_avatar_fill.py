"""博主头像批量补全测试：头像来源筛选 / 只补空缺 / 下载失败不覆盖 / 任务执行与明细。

头像来源（f2 用户库 ``user_info_web.avatar_url``）用临时 SQLite 库模拟，
下载器与图片编码用假实现替换——用例不联网、不依赖真实 CDN 与 Pillow 编码。
"""

import sqlite3
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.person import Blogger
from app.models.task import TaskQueue
from app.services.task_runners import blogger_avatar_fill as fill
from f2_patch import patch_f2
from scripts import import_f2_downloads as f2


def _create_douyin_blogger(client, name, platform_user_id=None):
    """建一个抖音博主（补全只覆盖抖音）。"""
    body = {"name": name, "platform": "douyin"}
    if platform_user_id:
        body["platform_user_id"] = platform_user_id
    r = client.post("/api/bloggers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _write_f2_avatar_db(tmp_path, rows) -> Path:
    """造一个带 avatar_url 列的 f2 用户库；rows 为 (sec_user_id, nickname, avatar_url)。"""
    f2_dir = tmp_path / "f2avatars"
    f2_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, ip_location TEXT, avatar_url TEXT)"
    )
    conn.executemany(
        "INSERT INTO user_info_web VALUES (?, ?, '', ?)",
        [(uid, name, url) for uid, name, url in rows],
    )
    conn.commit()
    conn.close()
    return f2_dir


def test_load_f2_avatars_reads_urls_and_tolerates_missing(tmp_path):
    """f2 头像表读取：只收非空 URL；库缺失/无列时返回空（不影响其它字段读取）。"""
    f2_dir = _write_f2_avatar_db(
        tmp_path,
        [
            ("MS4x_a", "有头像", "https://cdn.example/a.jpg"),
            ("MS4x_b", "无头像", ""),
        ],
    )
    assert f2.load_f2_avatars(f2_dir) == {"MS4x_a": "https://cdn.example/a.jpg"}

    # 库不存在 → 空
    assert f2.load_f2_avatars(tmp_path / "不存在") == {}

    # 旧库没有 avatar_url 列 → 空（只少头像，不抛）
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    conn = sqlite3.connect(old_dir / f2.F2_AUTHOR_DB)
    conn.execute("CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, ip_location TEXT)")
    conn.execute("INSERT INTO user_info_web VALUES ('MS4x_a', '旧库', 'IP属地：浙江')")
    conn.commit()
    conn.close()
    assert f2.load_f2_avatars(old_dir) == {}


async def test_create_task_only_targets_bloggers_with_source_and_no_avatar(client, tmp_path, monkeypatch):
    """只收「有 f2 头像来源」且「当前没头像」的博主；同一批里的已有头像者被跳过。"""
    patch_f2(
        monkeypatch,
        "DEFAULT_F2_DIR",
        _write_f2_avatar_db(
            tmp_path,
            [
                ("MS4x_has", "可补头像博", "https://cdn.example/has.jpg"),
                ("MS4x_none", "库里没头像博", ""),
            ],
        ),
    )
    target = _create_douyin_blogger(client, "可补头像博", platform_user_id="MS4x_has")
    # 库里该账号没有头像 URL：不该进目标
    _create_douyin_blogger(client, "库里没头像博", platform_user_id="MS4x_none")
    # 已在 f2 库里但用户已手动设过头像：默认不覆盖
    with_avatar = _create_douyin_blogger(client, "已有头像博", platform_user_id="MS4x_has")
    async with async_session() as db:
        blogger = await db.get(Blogger, with_avatar["id"])
        blogger.avatar_path = "avatars/avatar_manual.jpg"
        await db.commit()

    async with async_session() as db:
        task, total = await fill.create_avatar_fill_task(db)
        assert total == 1, "只应命中「有来源 + 无头像」的那一位"
        assert task is not None
        assert task.result["blogger_ids"] == [target["id"]]
        # overwrite=True 时才把已有头像的博主纳入
        task2, total2 = await fill.create_avatar_fill_task(db, overwrite=True)
        assert total2 == 2
        assert task2 is not None


async def test_create_task_returns_none_without_source(client, tmp_path, monkeypatch):
    """f2 用户库不存在（没有头像来源）→ 不建任务，调用方给出人话提示。"""
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", tmp_path / "不存在")
    _create_douyin_blogger(client, "无来源博", platform_user_id="MS4x_x")
    async with async_session() as db:
        task, total = await fill.create_avatar_fill_task(db)
    assert task is None
    assert total == 0


async def test_execute_fills_avatar_and_records_details(client, tmp_path, monkeypatch):
    """执行：下载成功 → 落盘 + 写 avatar_path；下载失败 → 保留原头像并记失败原因。"""
    ok_blogger = _create_douyin_blogger(client, "下载成功博", platform_user_id="MS4x_ok")
    bad_blogger = _create_douyin_blogger(client, "下载失败博", platform_user_id="MS4x_bad")

    def fake_source() -> dict[str, str]:
        return {
            "MS4x_ok": "https://cdn.example/ok.jpg",
            "MS4x_bad": "https://cdn.example/bad.jpg",
        }

    async def fake_download(_client, url, _semaphore):
        if url.endswith("bad.jpg"):
            return None, "下载失败（HTTP 404）"
        return b"fake-jpeg-bytes", ""

    monkeypatch.setattr(fill, "load_avatar_source", fake_source)
    monkeypatch.setattr(fill, "_download", fake_download)
    # 图片编码是 Pillow 的活，这里只验证「字节 → 落盘 → 写库」的接线
    monkeypatch.setattr(fill, "prepare_avatar_image", lambda _data: b"jpeg")

    async with async_session() as db:
        task, total = await fill.create_avatar_fill_task(db)
        assert total == 2
        assert task is not None
        await fill.execute_avatar_fill(db, task)
        result = task.result
        assert result["updated"] == 1
        assert result["failed"] == 1
        by_id = {d["blogger_id"]: d for d in result["results"]}
        assert by_id[ok_blogger["id"]]["status"] == "updated"
        assert by_id[bad_blogger["id"]]["status"] == "failed"
        assert "404" in by_id[bad_blogger["id"]]["reason"]

    async with async_session() as db:
        ok_row = await db.get(Blogger, ok_blogger["id"])
        bad_row = await db.get(Blogger, bad_blogger["id"])
    assert ok_row is not None and bad_row is not None
    assert ok_row.avatar_path == f"avatars/avatar_{ok_blogger['id']}.jpg"
    assert (Path(settings.storage_root) / ok_row.avatar_path).is_file()
    # 下载失败的不写库，前端继续显示占位/原头像
    assert not (bad_row.avatar_path or "").strip()


async def test_execute_cancelled_keeps_done_and_marks_status(client, tmp_path, monkeypatch):
    """取消：任务状态变 cancelled（worker 不会覆盖为 success），已补的保留。"""
    blogger = _create_douyin_blogger(client, "取消博", platform_user_id="MS4x_cancel")

    def fake_source() -> dict[str, str]:
        return {"MS4x_cancel": "https://cdn.example/cancel.jpg"}

    async def fake_download(_client, _url, _semaphore):
        return b"fake-jpeg-bytes", ""

    monkeypatch.setattr(fill, "load_avatar_source", fake_source)
    monkeypatch.setattr(fill, "_download", fake_download)
    monkeypatch.setattr(fill, "prepare_avatar_image", lambda _data: b"jpeg")

    async def fake_cancelled(_db, _task) -> bool:
        return True

    monkeypatch.setattr(fill, "is_cancelled", fake_cancelled)

    async with async_session() as db:
        task, _total = await fill.create_avatar_fill_task(db)
        assert task is not None
        await fill.execute_avatar_fill(db, task)
        assert task.status == "cancelled"
        assert task.result["cancelled"] is True
        assert task.result["updated"] == 0

    async with async_session() as db:
        row = await db.get(Blogger, blogger["id"])
        assert row is not None
        assert not (row.avatar_path or "").strip()


async def test_task_registered_in_handlers():
    """新任务类型必须在 TASK_HANDLERS 里登记，否则 worker 认领后会报「未知任务类型」。"""
    from app.services.task_runner import TASK_HANDLERS

    assert TASK_HANDLERS["blogger_avatar_fill"] is fill.execute_avatar_fill


async def test_api_creates_task_or_400(client, tmp_path, monkeypatch):
    """接口：有来源 → 201 返回 task_id/total；无来源 → 400 且提示人话。"""
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", tmp_path / "不存在")
    _create_douyin_blogger(client, "接口无来源博", platform_user_id="MS4x_api1")
    resp = client.post("/api/bloggers/avatars/fill-missing", json={})
    assert resp.status_code == 400
    assert "f2 用户库" in resp.json()["detail"]

    patch_f2(
        monkeypatch,
        "DEFAULT_F2_DIR",
        _write_f2_avatar_db(
            tmp_path / "second",
            [("MS4x_api2", "接口有来源博", "https://cdn.example/api2.jpg")],
        ),
    )
    _create_douyin_blogger(client, "接口有来源博", platform_user_id="MS4x_api2")
    resp = client.post("/api/bloggers/avatars/fill-missing", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert body["task_id"] > 0

    # 任务行确实落库（前端据此轮询进度）
    async with async_session() as db:
        rows = list(
            (await db.execute(select(TaskQueue).where(TaskQueue.type == "blogger_avatar_fill")))
            .scalars()
            .all()
        )
    assert any(r.id == body["task_id"] for r in rows)
