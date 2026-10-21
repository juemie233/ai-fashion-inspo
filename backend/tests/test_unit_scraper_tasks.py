"""采集任务服务测试（创建校验 / 列表 / 取消 / 删除 / 重试 / 日志）。

刻意避开的依赖：真实 CDP 端口探测、cookie 校验、子进程启动、Playwright。
这里全部 monkeypatch 掉，只验「状态机与校验规则」——因为这些才是出错时
让用户看到 500 或采错人的地方。

其中「按博主采集」的三条拒绝分支（博主不存在 / 平台不符 / 缺主页信息）
最值得锁：错采会把别人的作品入库，事后还要人工清理。
"""

import json

import pytest
from sqlalchemy import select
from fastapi import HTTPException

from app.database import async_session
from app.models.person import Blogger
from app.models.scraper import ScraperTask
from app.schemas.scraper import ScraperTaskCreate
from app.services.scraper import tasks as scraper_tasks


@pytest.fixture(autouse=True)
def _patch_externals(monkeypatch):
    """把 CDP 探测、cookie 校验、子进程启动全部替身化。"""

    async def _launch_ok(db, task):
        return None

    monkeypatch.setattr(scraper_tasks, "_safe_launch", _launch_ok)

    async def _cookie_ok(platform):
        return {"state": "valid", "detail": ""}

    monkeypatch.setattr(
        "app.services.scraper.cookie_verify.verify_platform_cookie", _cookie_ok
    )
    monkeypatch.setattr(scraper_tasks, "_check_cdp", lambda port: (True, "", True))


def _payload(**over) -> ScraperTaskCreate:
    base = {"platform": "xiaohongshu", "keywords": ["穿搭"], "max_count": 5}
    base.update(over)
    return ScraperTaskCreate(**base)


async def test_sources_list():
    sources = await scraper_tasks.get_scraper_sources()
    assert {s["platform"] for s in sources["sources"]} >= {
        "xiaohongshu",
        "douyin",
        "browser_extension",
    }
    assert isinstance(sources["tombstone_count"], int)


async def test_create_task_basic(client):
    async with async_session() as db:
        task = await scraper_tasks.create_scraper_task(db, _payload())

    assert task.id > 0
    assert task.status == "pending"
    config = json.loads(task.config)
    assert config["keywords"] == ["穿搭"]
    assert config["max_count"] == 5


async def test_create_task_rejects_bad_cdp_port(client, monkeypatch):
    monkeypatch.setattr(scraper_tasks, "_check_cdp", lambda port: (False, "连接被拒", False))

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await scraper_tasks.create_scraper_task(db, _payload(cdp_port=9222))

    assert exc.value.status_code == 400
    detail = exc.value.detail
    assert "调试端口不可用" in detail["error"]
    assert "command" in detail  # 给出可复制执行的启动命令


async def test_create_task_rejects_non_chrome_client(client, monkeypatch):
    """CDP 必须用真 Chrome：360 等衍生版协议不兼容，要明确拒绝。"""
    monkeypatch.setattr(
        scraper_tasks, "_check_cdp", lambda port: (True, "不是 Chrome", False)
    )

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await scraper_tasks.create_scraper_task(db, _payload(cdp_port=9222))

    assert exc.value.status_code == 400
    assert "Google Chrome" in exc.value.detail["hint"]


async def test_create_task_rejects_invalid_cookie(client, monkeypatch):
    async def _invalid(platform):
        return {"state": "invalid", "detail": "登录已过期"}

    monkeypatch.setattr(
        "app.services.scraper.cookie_verify.verify_platform_cookie", _invalid
    )

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await scraper_tasks.create_scraper_task(db, _payload())

    assert exc.value.status_code == 400
    assert "Cookie 已失效" in exc.value.detail["error"]


async def _blogger(**over) -> int:
    async with async_session() as db:
        blogger = Blogger(
            name=over.get("name", "测试博主"),
            platform=over.get("platform", "xiaohongshu"),
            profile_url=over.get("profile_url"),
            platform_user_id=over.get("platform_user_id"),
        )
        db.add(blogger)
        await db.commit()
        return blogger.id


async def test_create_user_mode_requires_existing_blogger(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await scraper_tasks.create_scraper_task(
                db, _payload(collect_mode="user", blogger_id=999999)
            )
    assert exc.value.status_code == 404


async def test_create_user_mode_fills_profile_from_blogger(client):
    """未传主页信息时从博主记录补齐（前端只选人，不必再填链接）。"""
    blogger_id = await _blogger(platform_user_id="abc123")

    async with async_session() as db:
        task = await scraper_tasks.create_scraper_task(
            db, _payload(collect_mode="user", blogger_id=blogger_id)
        )

    config = json.loads(task.config)
    assert config["platform_user_id"] == "abc123"
    assert config["profile_url"] == "https://www.xiaohongshu.com/user/profile/abc123"


async def test_create_user_mode_rejects_platform_mismatch(client):
    """博主平台与任务平台不符 → 400，绝不"静默采错人"。"""
    blogger_id = await _blogger(platform="douyin", platform_user_id="dy1")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await scraper_tasks.create_scraper_task(
                db, _payload(collect_mode="user", blogger_id=blogger_id)
            )

    assert exc.value.status_code == 400
    assert "与任务平台" in str(exc.value.detail)


async def test_create_user_mode_rejects_blogger_without_profile(client):
    blogger_id = await _blogger()  # 既没 profile_url 也没 platform_user_id

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await scraper_tasks.create_scraper_task(
                db, _payload(collect_mode="user", blogger_id=blogger_id)
            )

    assert exc.value.status_code == 400
    assert "缺少主页信息" in str(exc.value.detail)


async def test_douyin_profile_url_built_from_puid(client):
    blogger_id = await _blogger(platform="douyin", platform_user_id="MS4wLjAB")

    async with async_session() as db:
        task = await scraper_tasks.create_scraper_task(
            db, _payload(platform="douyin", collect_mode="user", blogger_id=blogger_id)
        )

    config = json.loads(task.config)
    assert config["profile_url"] == "https://www.douyin.com/user/MS4wLjAB"


async def test_list_tasks_filters_and_paginates(client):
    async with async_session() as db:
        await scraper_tasks.create_scraper_task(db, _payload(keywords=["a"]))
        await scraper_tasks.create_scraper_task(db, _payload(platform="douyin", keywords=["b"]))

        items, total, stats = await scraper_tasks.list_scraper_tasks(
            db, platform="douyin", page=1, size=10
        )

    assert total >= 1
    assert all(t.platform == "douyin" for t in items)
    # 统计覆盖全部筛选结果（不只当前页），键固定为五个状态
    assert set(stats) >= {"pending", "running", "completed", "failed", "cancelled"}


async def test_cancel_pending_task(client):
    async with async_session() as db:
        task = await scraper_tasks.create_scraper_task(db, _payload())
        task_id = task.id

        result = await scraper_tasks.cancel_scraper_task(db, task_id)

    assert "已取消" in result["message"]
    async with async_session() as db:
        refreshed = await db.get(ScraperTask, task_id)
    assert refreshed.status == "cancelled"


async def test_cancel_finished_task_rejected(client):
    async with async_session() as db:
        task = await scraper_tasks.create_scraper_task(db, _payload())
        task.status = "success"
        await db.commit()
        task_id = task.id

        with pytest.raises(HTTPException) as exc:
            await scraper_tasks.cancel_scraper_task(db, task_id)

    assert exc.value.status_code == 400


async def test_cancel_missing_task_404(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await scraper_tasks.cancel_scraper_task(db, 999999)
    assert exc.value.status_code == 404


async def test_task_log_reads_last_200_lines(client):
    from app.config import settings

    task_id = 424242
    log_dir = settings.storage_root / "logs" / "scraper"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"task_{task_id}.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(500)), encoding="utf-8")

    data = await scraper_tasks.get_task_log(task_id)

    assert data["task_id"] == task_id
    assert data["total_lines"] == 500
    assert len(data["content"].split("\n")) == 200
    assert data["content"].endswith("line 499")
    assert data["size_bytes"] > 0


async def test_task_log_missing_404(client):
    with pytest.raises(HTTPException) as exc:
        await scraper_tasks.get_task_log(999999)
    assert exc.value.status_code == 404


async def test_delete_single_terminal_task(client):
    """只允许删终态记录（pending/running 要先取消）。"""
    async with async_session() as db:
        task = await scraper_tasks.create_scraper_task(db, _payload())
        task.status = "success"
        await db.commit()
        task_id = task.id
        await scraper_tasks.delete_single_scraper_task(db, task_id)

    async with async_session() as db:
        assert await db.get(ScraperTask, task_id) is None


@pytest.mark.parametrize("status", ["pending", "running"])
async def test_delete_live_task_rejected(client, status):
    """在跑/待跑的任务记录不能删：子进程还在跑却再无可查记录。"""
    async with async_session() as db:
        task = await scraper_tasks.create_scraper_task(db, _payload())
        task.status = status
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await scraper_tasks.delete_single_scraper_task(db, task.id)
    assert exc.value.status_code == 400
    assert "请先取消" in str(exc.value.detail)


async def test_clear_all_keeps_running(client):
    """清空历史时不能删掉正在跑的任务（否则子进程还在写、记录没了）。"""
    async with async_session() as db:
        running = await scraper_tasks.create_scraper_task(db, _payload())
        running.status = "running"
        done = await scraper_tasks.create_scraper_task(db, _payload())
        done.status = "success"
        await db.commit()
        running_id, done_id = running.id, done.id

        result = await scraper_tasks.clear_all_scraper_tasks(db)

    assert result["deleted"] >= 1
    async with async_session() as db:
        # 运行中的留着（否则子进程还在跑、记录没了，无法取消也看不到进度）
        assert await db.get(ScraperTask, running_id) is not None
        assert await db.get(ScraperTask, done_id) is None


async def test_retry_failed_tasks_batch(client):
    async with async_session() as db:
        failed = await scraper_tasks.create_scraper_task(db, _payload())
        failed.status = "failed"
        failed.error = "端口占用"
        await db.commit()
        failed_id = failed.id

        result = await scraper_tasks.retry_failed_scraper_tasks(db)

    assert result["retried"] >= 1
    async with async_session() as db:
        original = await db.get(ScraperTask, failed_id)
        # 批量重试是「照配置新建任务」，原失败记录保留供追溯
        assert original.status == "failed"
        pending = (
            await db.execute(
                select(ScraperTask).where(
                    ScraperTask.status == "pending", ScraperTask.id != failed_id
                )
            )
        ).scalars().all()
    assert any(t.config == original.config for t in pending)


async def test_retry_single_task(client):
    async with async_session() as db:
        task = await scraper_tasks.create_scraper_task(db, _payload())
        task.status = "failed"
        await db.commit()
        task_id = task.id

        await scraper_tasks.retry_single_task(db, task_id)

    async with async_session() as db:
        refreshed = await db.get(ScraperTask, task_id)
    assert refreshed.status == "pending"


async def test_retry_single_non_failed_rejected(client):
    async with async_session() as db:
        task = await scraper_tasks.create_scraper_task(db, _payload())
        task.status = "success"
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await scraper_tasks.retry_single_task(db, task.id)
    assert exc.value.status_code == 400
