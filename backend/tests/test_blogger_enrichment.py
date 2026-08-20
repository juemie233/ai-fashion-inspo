"""博主主页信息补全测试：本地互推 / 搜索匹配策略 / 失败原因 / 任务执行与进度。

搜索层（XiaohongshuScraper.search_users）通过注入假实现模拟，不依赖真实浏览器；
任务执行器通过替换 XiaohongshuScraper 类模拟。
"""

import pytest
from pathlib import Path
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.person import Blogger
from app.models.task import TaskQueue
from app.services.blogger_enrichment_service import (
    build_profile_url,
    enrich_one,
    extract_user_id_from_url,
    list_missing_profile_bloggers,
)
from app.services.task_runners.common import PermanentTaskError
from app.services.task_runners.enrich_blogger_profile import (
    MAX_ENRICH_PER_TASK,
    create_enrich_blogger_profile_task,
    execute_enrich_blogger_profile,
)


def _create_fake_cookie() -> Path:
    """创建小红书假 Cookie 文件（执行器前置检查用，仅需存在）。"""
    path = Path(settings.storage_root) / "cookies" / "xiaohongshu_cookies.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")
    return path


def _create_blogger(client, name, xhs_id=None, profile_url=None, platform_user_id=None):
    body = {"name": name, "platform": "xiaohongshu"}
    if xhs_id:
        body["xhs_id"] = xhs_id
    if profile_url:
        body["profile_url"] = profile_url
    if platform_user_id:
        body["platform_user_id"] = platform_user_id
    r = client.post("/api/bloggers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ═══════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════


def test_extract_user_id_from_url():
    assert extract_user_id_from_url("https://www.xiaohongshu.com/user/profile/abc123?x=y") == "abc123"
    assert extract_user_id_from_url("https://www.xiaohongshu.com/explore/123") is None
    assert build_profile_url("abc123") == "https://www.xiaohongshu.com/user/profile/abc123"


def test_normalize_cookies():
    """Chrome 扩展导出 Cookie → Playwright 兼容格式（sameSite/expires/多余字段）。"""
    from app.scrapers.xiaohongshu import normalize_cookies

    raw = [
        # 扩展导出典型条目：sameSite=null + expirationDate + 多余字段
        {
            "domain": ".xiaohongshu.com",
            "expirationDate": 1818762106,
            "hostOnly": False,
            "httpOnly": True,
            "name": "web_session",
            "path": "/",
            "sameSite": None,
            "secure": True,
            "session": False,
            "storeId": None,
            "value": "abc123",
        },
        # no_restriction / unspecified / lax / strict 归一化
        {"name": "a", "value": "1", "domain": ".x.com", "path": "/", "sameSite": "no_restriction"},
        {"name": "b", "value": "2", "domain": ".x.com", "path": "/", "sameSite": "unspecified"},
        {"name": "c", "value": "3", "domain": ".x.com", "path": "/", "sameSite": "lax"},
        {"name": "d", "value": "4", "domain": ".x.com", "path": "/", "sameSite": "Strict"},
        # 缺 name 的条目丢弃
        {"value": "no-name", "domain": ".x.com", "path": "/"},
    ]
    out = normalize_cookies(raw)
    assert len(out) == 5
    first = out[0]
    assert first["name"] == "web_session"
    assert first["sameSite"] == "Lax"  # null → Lax
    assert first["expires"] == 1818762106  # expirationDate → expires
    assert first["httpOnly"] is True
    assert first["secure"] is True
    assert "expirationDate" not in first and "session" not in first and "storeId" not in first
    assert [c["sameSite"] for c in out] == ["Lax", "None", "Lax", "Lax", "Strict"]


# ═══════════════════════════════════════════════════════════════
#  enrich_one 各分支（注入假 search_users）
# ═══════════════════════════════════════════════════════════════


async def _blogger_row(bid: int) -> Blogger:
    async with async_session() as db:
        return await db.get(Blogger, bid)


async def test_enrich_from_url_without_search(client):
    """本地互推：有主页 URL 无用户 ID → 从 URL 提取，不触发搜索。"""
    b = _create_blogger(
        client, "URL博", profile_url="https://www.xiaohongshu.com/user/profile/uid99"
    )
    called = {"n": 0}

    async def fake_search(keyword):
        called["n"] += 1
        return []

    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, search_users=fake_search)
    assert result["status"] == "updated"
    assert result["platform_user_id"] == "uid99"
    assert called["n"] == 0  # 未搜索
    row = await _blogger_row(b["id"])
    assert row.platform_user_id == "uid99"
    assert row.profile_url == "https://www.xiaohongshu.com/user/profile/uid99"


async def test_enrich_build_url_without_search(client):
    """本地互推：有用户 ID 无主页 URL → 拼接 URL，不触发搜索。"""
    b = _create_blogger(client, "ID博", platform_user_id="uid88")
    called = {"n": 0}

    async def fake_search(keyword):
        called["n"] += 1
        return []

    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, search_users=fake_search)
    assert result["status"] == "updated"
    assert result["profile_url"] == "https://www.xiaohongshu.com/user/profile/uid88"
    assert called["n"] == 0


async def test_enrich_invalid_url_failed(client):
    """主页 URL 无法解析用户 ID → failed 并记录原因。"""
    b = _create_blogger(client, "坏URL博", profile_url="https://www.xiaohongshu.com/explore/xx")
    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, search_users=None)
    assert result["status"] == "failed"
    assert "无法解析" in result["reason"]


async def test_enrich_single_candidate_adopted(client):
    """两者都缺：搜索返回唯一候选 → 采纳并更新。"""
    b = _create_blogger(client, "独苗博", xhs_id="xhs123")
    candidate = {
        "name": "独苗博",
        "profile_url": "https://www.xiaohongshu.com/user/profile/cand1",
        "platform_user_id": "cand1",
    }

    async def fake_search(keyword):
        assert keyword == "xhs123"
        return [candidate]

    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, search_users=fake_search)
    assert result["status"] == "updated"
    assert result["platform_user_id"] == "cand1"
    row = await _blogger_row(b["id"])
    assert row.profile_url == candidate["profile_url"]
    assert row.platform_user_id == "cand1"


async def test_enrich_multi_candidates_name_exact_match(client):
    """多候选：昵称完全匹配才采纳。"""
    b = _create_blogger(client, "重名博", xhs_id="xhs456")
    candidates = [
        {"name": "别人", "profile_url": "https://www.xiaohongshu.com/user/profile/a", "platform_user_id": "a"},
        {"name": "重名博", "profile_url": "https://www.xiaohongshu.com/user/profile/b", "platform_user_id": "b"},
    ]

    async def fake_search(keyword):
        return candidates

    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, search_users=fake_search)
    assert result["status"] == "updated"
    assert result["platform_user_id"] == "b"


async def test_enrich_multi_candidates_no_match_failed(client):
    """多候选无昵称匹配 → failed（需人工核对）。"""
    b = _create_blogger(client, "无名博", xhs_id="xhs789")
    candidates = [
        {"name": "甲", "profile_url": "https://www.xiaohongshu.com/user/profile/a", "platform_user_id": "a"},
        {"name": "乙", "profile_url": "https://www.xiaohongshu.com/user/profile/b", "platform_user_id": "b"},
    ]

    async def fake_search(keyword):
        return candidates

    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, search_users=fake_search)
    assert result["status"] == "failed"
    assert "无法唯一确认" in result["reason"]


async def test_enrich_no_result_failed(client):
    """搜索无结果 → failed。"""
    b = _create_blogger(client, "无果博", xhs_id="xhs000")

    async def fake_search(keyword):
        return []

    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, search_users=fake_search)
    assert result["status"] == "failed"
    assert "无结果" in result["reason"]


async def test_enrich_missing_xhs_id_failed(client):
    """两者都缺且无小红书号 → failed（无法定位）。"""
    b = _create_blogger(client, "无号博")
    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, search_users=None)
    assert result["status"] == "failed"
    assert "缺少小红书号" in result["reason"]


async def test_enrich_login_wall_failure_reason(client):
    """搜索遇登录墙（未登录）→ failed 且原因明确提示未登录。"""
    b = _create_blogger(client, "登录博", xhs_id="xhs111")

    async def fake_search(keyword):
        raise RuntimeError("小红书未登录（搜索页登录墙拦截），请确认已导入有效 Cookie")

    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, search_users=fake_search)
    assert result["status"] == "failed"
    assert "未登录" in result["reason"]


# ═══════════════════════════════════════════════════════════════
#  接口与任务执行
# ═══════════════════════════════════════════════════════════════


def test_enrich_api_no_missing_bloggers(client):
    """没有缺失博主 → 400。"""
    r = client.post("/api/bloggers/enrich-missing-profile", json={})
    assert r.status_code == 400
    assert "没有缺失" in r.json()["detail"]


def test_enrich_api_invalid_blogger_ids(client):
    """blogger_ids 格式错误 → 422。"""
    r = client.post("/api/bloggers/enrich-missing-profile", json={"blogger_ids": ["a"]})
    assert r.status_code == 422


async def test_enrich_api_and_execute(client):
    """接口创建任务 → 执行器补全（本地互推博主成功、搜索博主失败）→ 明细与进度。"""
    _create_fake_cookie()
    _create_blogger(client, "本地博", profile_url="https://www.xiaohongshu.com/user/profile/uid1")
    _create_blogger(client, "搜索博", xhs_id="xhs777")

    r = client.post("/api/bloggers/enrich-missing-profile", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["total"] == 2
    assert body["truncated"] is False
    task_id = body["task_id"]

    # mock 任务执行器里的 scraper：唯一候选命中「搜索博」
    class _FakeScraper:
        async def search_users(self, keyword):
            return [
                {
                    "name": "搜索博",
                    "profile_url": "https://www.xiaohongshu.com/user/profile/found1",
                    "platform_user_id": "found1",
                }
            ]

        async def close(self):
            pass

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "app.scrapers.xiaohongshu.XiaohongshuScraper", lambda **kw: _FakeScraper()
    )
    try:
        async with async_session() as db:
            task = await db.get(TaskQueue, task_id)
            await execute_enrich_blogger_profile(db, task)
            await db.refresh(task)
            assert task.progress == 100
            assert task.done == 2
            assert task.result["updated"] == 2
            assert task.result["failed"] == 0
    finally:
        monkeypatch.undo()

    # 两位博主均已补全
    async with async_session() as db:
        rows = (
            await db.execute(select(Blogger).where(Blogger.id.in_([1, 2])))
        ).scalars().all()
        by_id = {b.id: b for b in rows}
        assert by_id[1].platform_user_id == "uid1"
        assert by_id[2].platform_user_id == "found1"

    # 再次查询缺失列表应为空 → 接口 400
    r2 = client.post("/api/bloggers/enrich-missing-profile", json={})
    assert r2.status_code == 400


async def test_enrich_task_scope_and_cap(client):
    """范围限定与单次上限：blogger_ids 过滤 + 超过上限只处理前 MAX 个。"""
    ids = []
    for i in range(MAX_ENRICH_PER_TASK + 3):
        b = _create_blogger(client, f"缺博{i}", xhs_id=f"xhs{i:03d}")
        ids.append(b["id"])

    # 范围限定：只补全前 2 个
    async with async_session() as db:
        task, total = await create_enrich_blogger_profile_task(db, ids[:2])
        assert total == 2
        assert task.result["blogger_ids"] == ids[:2]

    # 无范围：全量但截断到上限
    async with async_session() as db:
        task2, total2 = await create_enrich_blogger_profile_task(db, None)
        assert total2 == MAX_ENRICH_PER_TASK
        assert len(task2.result["blogger_ids"]) == MAX_ENRICH_PER_TASK


async def test_enrich_task_failure_does_not_block(client, monkeypatch):
    """单博主失败不阻塞整体：失败原因记录，其余继续。"""
    _create_fake_cookie()
    _create_blogger(client, "成功博", profile_url="https://www.xiaohongshu.com/user/profile/ok1")
    _create_blogger(client, "失败博", xhs_id="xhs404")

    class _FakeScraper:
        async def search_users(self, keyword):
            return []  # 搜索无结果 → 失败

        async def close(self):
            pass

    import app.services.task_runners.enrich_blogger_profile as mod  # noqa: F401

    monkeypatch.setattr(
        "app.scrapers.xiaohongshu.XiaohongshuScraper", lambda **kw: _FakeScraper()
    )
    async with async_session() as db:
        task, _ = await create_enrich_blogger_profile_task(db, None)
        await execute_enrich_blogger_profile(db, task)
        await db.refresh(task)
        assert task.progress == 100
        assert task.result["updated"] == 1
        assert task.result["failed"] == 1
        results = task.result["results"]
        by_name = {r["name"]: r for r in results}
        assert by_name["成功博"]["status"] == "updated"
        assert by_name["失败博"]["status"] == "failed"
        assert "无结果" in by_name["失败博"]["reason"]


async def test_enrich_task_missing_cookie_fails_fast(client):
    """未导入小红书 Cookie → 任务直接失败（不逐个跑登录墙），原因明确。"""
    _create_blogger(client, "无Cookie博", xhs_id="xhs555")
    # 确保 cookie 文件不存在（本测试不创建）
    cookie = Path(settings.storage_root) / "cookies" / "xiaohongshu_cookies.json"
    if cookie.exists():
        cookie.unlink()

    async with async_session() as db:
        task, _ = await create_enrich_blogger_profile_task(db, None)
        with pytest.raises(PermanentTaskError, match="Cookie"):
            await execute_enrich_blogger_profile(db, task)
