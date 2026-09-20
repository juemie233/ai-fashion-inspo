"""博主主页信息补全测试：本地互推 / 关注列表匹配 / 失败原因 / 任务执行与进度。

匹配依据（关注列表 `XiaohongshuScraper.list_following_sync`）通过注入假实现模拟，
不依赖真实浏览器；任务执行器通过替换 XiaohongshuScraper 类模拟。
"""

import sqlite3

import pytest
from pathlib import Path
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.person import Blogger
from app.models.task import TaskQueue
from app.services.blogger_enrichment_service import (
    build_following_index,
    build_profile_url,
    enrich_one,
    extract_user_id_from_url,
    list_missing_profile_bloggers,
    list_skipped,
)
from app.services.task_runners.common import PermanentTaskError
from app.services.task_runners.enrich_blogger_profile import (
    create_enrich_blogger_profile_task,
    execute_enrich_blogger_profile,
)
from f2_patch import patch_f2
from scripts import import_f2_downloads as f2


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


def test_build_following_index_skips_incomplete_rows():
    """关注列表 → {归一化昵称: uid}：全角/emoji/大小写归一化，缺字段的行丢弃。"""
    index = build_following_index(
        [
            {"nickname": "Ｋｉｔｔｔｔｙ 🐱", "uid": "u1"},
            {"nickname": "  空格博  ", "uid": "u2"},
            {"nickname": "没Uid博", "uid": ""},
            {"nickname": "", "uid": "u3"},
            {"nickname": "同号博", "uid": "u4"},
            {"nickname": "同号博", "uid": "u4"},  # 同一 uid 重复出现无害
        ]
    )
    assert index["kitttty"] == "u1"
    assert index["空格博"] == "u2"
    assert index["同号博"] == "u4"
    assert len(index) == 3  # 缺昵称/缺 uid 的行被丢弃


def test_build_following_index_drops_ambiguous_nicknames():
    """归一化重名的不同用户整条剔除（宁可不填，也不写错 uid）。"""
    index = build_following_index(
        [
            {"nickname": "oo", "uid": "u1"},
            {"nickname": "oo-", "uid": "u2"},  # 归一化后同为 oo
            {"nickname": "唯一博", "uid": "u3"},
        ]
    )
    assert "oo" not in index
    assert index == {"唯一博": "u3"}


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
#  enrich_one 各分支（关注列表索引由调用方传入，本层零请求）
# ═══════════════════════════════════════════════════════════════


async def _blogger_row(bid: int) -> Blogger:
    async with async_session() as db:
        return await db.get(Blogger, bid)


async def test_enrich_from_url_without_following(client):
    """本地互推：有主页 URL 无用户 ID → 从 URL 提取，不依赖关注列表。"""
    b = _create_blogger(
        client, "URL博", profile_url="https://www.xiaohongshu.com/user/profile/uid99"
    )
    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger)
    assert result["status"] == "updated"
    assert result["platform_user_id"] == "uid99"
    row = await _blogger_row(b["id"])
    assert row.platform_user_id == "uid99"
    assert row.profile_url == "https://www.xiaohongshu.com/user/profile/uid99"


async def test_enrich_build_url_without_following(client):
    """本地互推：有用户 ID 无主页 URL → 拼接 URL。"""
    b = _create_blogger(client, "ID博", platform_user_id="uid88")
    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, following={})
    assert result["status"] == "updated"
    assert result["profile_url"] == "https://www.xiaohongshu.com/user/profile/uid88"


async def test_enrich_invalid_url_skipped(client):
    """主页 URL 无法解析用户 ID → 确定性失败自动跳过并写跳过表。"""
    b = _create_blogger(client, "坏URL博", profile_url="https://www.xiaohongshu.com/explore/xx")
    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger)
        assert result["status"] == "skipped"
        assert "无法解析" in result["reason"]
        # 已写入跳过表：不再出现在缺失列表
        missing = await list_missing_profile_bloggers(db)
        assert all(m.id != b["id"] for m in missing)
        skips = await list_skipped(db)
        assert any(s["blogger_id"] == b["id"] for s in skips)


async def test_enrich_from_following_by_nickname(client):
    """两者都缺：关注列表里昵称命中 → 用 uid 拼主页 URL 并落库。"""
    b = _create_blogger(client, "关注博", xhs_id="xhs123")
    following = build_following_index([{"nickname": "关注博", "uid": "cand1"}])
    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, following=following)
    assert result["status"] == "updated"
    assert result["platform_user_id"] == "cand1"
    row = await _blogger_row(b["id"])
    assert row.profile_url == "https://www.xiaohongshu.com/user/profile/cand1"
    assert row.platform_user_id == "cand1"


async def test_enrich_following_normalized_name_match(client):
    """昵称归一化匹配：容忍大小写/全角/emoji/空格差异。"""
    b = _create_blogger(client, "Kitttty")
    following = build_following_index([{"nickname": "Ｋｉｔｔｔｔｙ 🐱", "uid": "b"}])
    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, following=following)
    assert result["status"] == "updated"
    assert result["platform_user_id"] == "b"


async def test_enrich_not_in_following_skipped(client):
    """两者都缺且不在关注列表里 → 确定性失败自动跳过，原因给出两条出路。"""
    b = _create_blogger(client, "取关博", xhs_id="xhs000")
    async with async_session() as db:
        blogger = await db.get(Blogger, b["id"])
        result = await enrich_one(db, blogger, following={"别人": "u9"})
        assert result["status"] == "skipped"
        assert "关注列表" in result["reason"]
        assert "手工填写" in result["reason"]
        # 已跳过 → 不再出现在缺失列表
        missing = await list_missing_profile_bloggers(db)
        assert all(m.id != b["id"] for m in missing)


# ═══════════════════════════════════════════════════════════════
#  接口与任务执行
# ═══════════════════════════════════════════════════════════════


def test_enrich_api_no_missing_bloggers(client):
    """没有资料有缺口的博主 → 400。"""
    r = client.post("/api/bloggers/enrich-missing-profile", json={})
    assert r.status_code == 400
    assert "没有资料有缺口" in r.json()["detail"]


def test_enrich_api_invalid_blogger_ids(client):
    """blogger_ids 格式错误 → 422。"""
    r = client.post("/api/bloggers/enrich-missing-profile", json={"blogger_ids": ["a"]})
    assert r.status_code == 422


async def test_enrich_api_and_execute(client):
    """接口创建任务 → 执行器补全（本地互推 + 关注列表匹配）→ 明细与进度。"""
    _create_fake_cookie()
    _create_blogger(client, "本地博", profile_url="https://www.xiaohongshu.com/user/profile/uid1")
    _create_blogger(client, "关注博", xhs_id="xhs777")

    r = client.post("/api/bloggers/enrich-missing-profile", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["total"] == 2
    task_id = body["task_id"]

    # mock 任务执行器里的 scraper：关注列表里能按昵称找到「关注博」
    class _FakeScraper:
        def list_following_sync(self, max_pages=3):
            return [{"nickname": "关注博", "uid": "found1"}]

        def close_sync(self):
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


async def test_enrich_task_scope_and_no_cap(client):
    """范围限定：blogger_ids 过滤；不设数量上限（只发一次关注列表请求，无需分批）。"""
    ids = []
    for i in range(23):
        b = _create_blogger(client, f"缺博{i}", xhs_id=f"xhs{i:03d}")
        ids.append(b["id"])

    # 范围限定：只补全前 2 个
    async with async_session() as db:
        task, total = await create_enrich_blogger_profile_task(db, ids[:2])
        assert total == 2
        assert task.result["blogger_ids"] == ids[:2]

    # 无范围：全部纳入，不截断
    async with async_session() as db:
        task2, total2 = await create_enrich_blogger_profile_task(db, None)
        assert total2 == 23
        assert len(task2.result["blogger_ids"]) == 23


async def test_enrich_task_failure_does_not_block(client, monkeypatch):
    """单博主拿不到 uid 不阻塞整体：原因记录，其余继续。"""
    _create_fake_cookie()
    _create_blogger(client, "成功博", profile_url="https://www.xiaohongshu.com/user/profile/ok1")
    _create_blogger(client, "缺料博", xhs_id="xhs404")

    class _FakeScraper:
        def list_following_sync(self, max_pages=3):
            return []  # 关注列表里没有「缺料博」（改过名 / 已取关）

        def close_sync(self):
            pass

    monkeypatch.setattr(
        "app.scrapers.xiaohongshu.XiaohongshuScraper", lambda **kw: _FakeScraper()
    )
    async with async_session() as db:
        task, _ = await create_enrich_blogger_profile_task(db, None)
        await execute_enrich_blogger_profile(db, task)
        await db.refresh(task)
        assert task.progress == 100
        assert task.result["updated"] == 1
        assert task.result["skipped"] == 1
        assert task.result["failed"] == 0
        results = task.result["results"]
        by_name = {r["name"]: r for r in results}
        assert by_name["成功博"]["status"] == "updated"
        assert by_name["缺料博"]["status"] == "skipped"
        assert "关注列表" in by_name["缺料博"]["reason"]

        # 跳过后不再出现在缺失列表（下一批只处理未跳过的）
        missing = await list_missing_profile_bloggers(db)
        assert all(m.name != "缺料博" for m in missing)


async def test_enrich_skip_unskip_roundtrip(client):
    """跳过/解除跳过接口：手动跳过后缺失列表排除，解除后重新纳入。"""
    b = _create_blogger(client, "手动跳过博", xhs_id="xhs999")
    r = client.post("/api/bloggers/enrich-skip", json={"blogger_ids": [b["id"]], "reason": "测试跳过"})
    assert r.status_code == 200
    assert r.json()["skipped"] == 1

    # 缺失列表排除
    missing = client.get("/api/bloggers/missing-profile").json()
    assert missing["total"] == 0
    # 已跳过列表包含
    skips = client.get("/api/bloggers/enrich-skips").json()
    assert skips["total"] == 1
    assert skips["items"][0]["blogger_id"] == b["id"]

    # 解除后重新纳入
    r2 = client.post("/api/bloggers/enrich-unskip", json={"blogger_ids": [b["id"]]})
    assert r2.json()["unskipped"] == 1
    missing2 = client.get("/api/bloggers/missing-profile").json()
    assert missing2["total"] == 1


async def test_enrich_priority_two_missing_first(client):
    """处理顺序：两项信息都缺失的博主优先于只缺一项的（本地互推类靠后）。"""
    # 只缺 profile_url（有 platform_user_id → 本地互推可补，排在后面）
    _create_blogger(client, "缺URL博", platform_user_id="uid111")
    # 两项都缺（需要关注列表解析，排在前面）
    _create_blogger(client, "全缺博", xhs_id="xhs222")
    # 只缺 platform_user_id（有 URL → 本地互推，排在后面）
    _create_blogger(client, "缺ID博", profile_url="https://www.xiaohongshu.com/user/profile/uid333")

    async with async_session() as db:
        bloggers = await list_missing_profile_bloggers(db)
        names = [b.name for b in bloggers]
        # 两项都缺的「全缺博」必须在最前
        assert names[0] == "全缺博"
        assert names.index("全缺博") < names.index("缺URL博")
        assert names.index("全缺博") < names.index("缺ID博")
        # 其余顺序按 id（创建顺序）
        assert set(names) == {"全缺博", "缺URL博", "缺ID博"}

    # 前端缺失列表同样顺序
    resp = client.get("/api/bloggers/missing-profile").json()
    assert resp["items"][0]["name"] == "全缺博"


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


async def test_enrich_task_following_fetch_error_fails_task(client, monkeypatch):
    """关注列表接口异常 → 整任务抛出（由 worker 按重试策略处理），不留半成品结果。"""
    _create_fake_cookie()
    _create_blogger(client, "异常博", xhs_id="xhs666")

    class _FakeScraper:
        def list_following_sync(self, max_pages=3):
            raise RuntimeError("关注列表接口 461（风控拦截）")

        def close_sync(self):
            pass

    monkeypatch.setattr(
        "app.scrapers.xiaohongshu.XiaohongshuScraper", lambda **kw: _FakeScraper()
    )
    async with async_session() as db:
        task, _ = await create_enrich_blogger_profile_task(db, None)
        with pytest.raises(RuntimeError, match="风控拦截"):
            await execute_enrich_blogger_profile(db, task)


# ═══════════════════════════════════════════════════════════════
#  抖音 IP 属地离线回填（读 f2 用户库；不联网、不需要 Cookie）
# ═══════════════════════════════════════════════════════════════


def _create_douyin_blogger(client, name, platform_user_id=None, ip_location=None):
    body = {"name": name, "platform": "douyin"}
    if platform_user_id:
        body["platform_user_id"] = platform_user_id
    if ip_location:
        body["ip_location"] = ip_location
    r = client.post("/api/bloggers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _write_f2_db(tmp_path, rows) -> Path:
    """造一个 f2 用户库（ip_location 用真实的「IP属地：xx」写法）。"""
    f2_dir = tmp_path / "f2proj"
    f2_dir.mkdir()
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, ip_location TEXT)"
    )
    conn.executemany("INSERT INTO user_info_web VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return f2_dir


async def test_douyin_backfill_fills_gaps_and_skips_others(
    client, tmp_path, monkeypatch
):
    """一键补全的抖音分支：只补空缺、已有值不动、查不到的写跳过并说明原因。"""
    patch_f2(
        monkeypatch, "DEFAULT_F2_DIR",
        _write_f2_db(
            tmp_path,
            [
                ("MS4x_a", "缺属地博", "IP属地：浙江"),
                ("MS4x_b", "已有属地博", "IP属地：广东"),
                # MS4x_missing 故意不在库里（f2 没见过这个账号）
            ],
        ),
    )
    filled = _create_douyin_blogger(client, "缺属地博", platform_user_id="MS4x_a")
    kept = _create_douyin_blogger(
        client, "已有属地博", platform_user_id="MS4x_b", ip_location="四川"
    )
    absent = _create_douyin_blogger(client, "f2没有博", platform_user_id="MS4x_missing")
    no_uid = _create_douyin_blogger(client, "缺ID博")

    # 缺口列表包含抖音（并带上 platform / platform_user_id 供前端显示）
    resp = client.get("/api/bloggers/missing-profile").json()
    douyin_items = [i for i in resp["items"] if i["platform"] == "douyin"]
    assert {i["name"] for i in douyin_items} == {"缺属地博", "f2没有博", "缺ID博"}
    assert all("platform_user_id" in i for i in douyin_items)

    # 执行任务（只有抖音博主，不需要小红书 Cookie / 浏览器）
    async with async_session() as db:
        task, total = await create_enrich_blogger_profile_task(db, None)
        assert total == 3
        await execute_enrich_blogger_profile(db, task)
        await db.refresh(task)
        assert task.progress == 100
        assert task.result["updated"] == 1
        assert task.result["douyin_updated"] == 1
        assert task.result["skipped"] == 2
        assert task.result["failed"] == 0
        by_name = {r["name"]: r for r in task.result["results"]}
        assert by_name["缺属地博"]["status"] == "updated"
        assert "f2 用户库" in by_name["f2没有博"]["reason"]
        assert "sec_user_id" in by_name["缺ID博"]["reason"]

    # 落库结果：空缺补上、已有值不被覆盖
    async with async_session() as db:
        rows = (
            await db.execute(
                select(Blogger).where(
                    Blogger.id.in_([filled["id"], kept["id"], absent["id"], no_uid["id"]])
                )
            )
        ).scalars().all()
        by_id = {b.id: b for b in rows}
        assert by_id[filled["id"]].ip_location == "浙江"
        assert by_id[kept["id"]].ip_location == "四川"  # 只补空缺

    # 查不到的两位进了跳过表（可在界面解除后重试），已补全的不再出现在缺口列表
    skips = client.get("/api/bloggers/enrich-skips").json()
    assert {s["name"] for s in skips["items"]} == {"f2没有博", "缺ID博"}
    after = client.get("/api/bloggers/missing-profile").json()
    assert after["total"] == 0

    # 幂等：再跑一次不会改动任何值
    async with async_session() as db:
        task2, total2 = await create_enrich_blogger_profile_task(db, None)
        assert (task2, total2) == (None, 0)


async def test_douyin_backfill_does_not_overwrite_existing_value(client):
    """服务层兜底：即使被显式点名，已有 IP 属地也不覆盖（「只补空缺」策略）。"""
    from app.services.blogger_enrichment_service import backfill_douyin_from_f2

    blogger = _create_douyin_blogger(
        client, "手填属地博", platform_user_id="MS4x_a", ip_location="上海"
    )
    async with async_session() as db:
        row = await db.get(Blogger, blogger["id"])
        result = await backfill_douyin_from_f2(db, row, {"MS4x_a": {"ip_location": "浙江"}})
        await db.refresh(row)
    assert result["status"] == "skipped"
    assert "只补空缺" in result["reason"]
    assert row.ip_location == "上海"


async def test_douyin_backfill_empty_in_f2_is_skipped(client, tmp_path, monkeypatch):
    """f2 库里有这个账号但没有属地：跳过并提示「让 f2 采一次主页」。"""
    patch_f2(
        monkeypatch, "DEFAULT_F2_DIR",
        _write_f2_db(tmp_path, [("MS4x_a", "空属地博", "")]),
    )
    _create_douyin_blogger(client, "空属地博", platform_user_id="MS4x_a")

    async with async_session() as db:
        task, _ = await create_enrich_blogger_profile_task(db, None)
        await execute_enrich_blogger_profile(db, task)
        await db.refresh(task)
        assert task.result["updated"] == 0
        assert task.result["skipped"] == 1
        assert "没有 IP 属地" in task.result["results"][0]["reason"]


async def test_enrich_task_includes_douyin_and_xhs_together(client, tmp_path, monkeypatch):
    """抖音 + 小红书混合：全部纳入（无上限），抖音排在最前（离线零成本先做）。"""
    patch_f2(
        monkeypatch, "DEFAULT_F2_DIR",
        _write_f2_db(tmp_path, [(f"MS4x_{i}", f"抖音{i}", "IP属地：浙江") for i in range(5)]),
    )
    for i in range(5):
        _create_douyin_blogger(client, f"抖音{i}", platform_user_id=f"MS4x_{i}")
    for i in range(23):
        _create_blogger(client, f"缺博{i}", xhs_id=f"xhs{i:03d}")

    async with async_session() as db:
        task, total = await create_enrich_blogger_profile_task(db, None)

    assert total == 5 + 23
    assert len(task.result["blogger_ids"]) == 28

    # 抖音排在最前
    async with async_session() as db:
        rows = (
            await db.execute(
                select(Blogger.id, Blogger.platform).where(
                    Blogger.id.in_(task.result["blogger_ids"])
                )
            )
        ).all()
        by_id = {bid: p for bid, p in rows}
        platforms = [by_id.get(i) for i in task.result["blogger_ids"][:5]]
    assert platforms == ["douyin"] * 5
