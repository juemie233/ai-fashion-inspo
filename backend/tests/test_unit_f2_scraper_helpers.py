"""F2 小模块与爬虫/事件基础件的直连单测。

覆盖：
- ``app/scrapers/base.py``：``launch_sync_page`` 组装的 launcher 参数与统一 UA/视口、
  ``RawContent`` 默认值、抽象基类四个抽象方法的占位实现；
- ``app/services/task_events.py``：广播转发 + 广播异常静默降级；
- ``app/services/scraper/f2_bloggers.py``：未绑定作者的补登记（新建/复用/同名歧义/
  素材不存在/单条异常）、跳过规则；
- ``app/services/scraper/f2_preview.py``：扫描根三种模式、作者白名单口径（含 f2 用户库
  读取失败降级）、预览结果字段与空目录、异步入口线程池转发。
"""

from types import SimpleNamespace

import pytest

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.person import Blogger, InspirationBlogger
from app.scrapers import base as scraper_base
from app.services import f2
from app.services import task_events
from app.services.scraper import f2_bloggers, f2_preview


# ═══════════════════════════════════════════════════════════════
#  scrapers/base.py
# ═══════════════════════════════════════════════════════════════


class _FakePage:
    pass


class _FakeContext:
    def __init__(self) -> None:
        self.page = _FakePage()

    def new_page(self):
        return self.page


class _FakeBrowser:
    def __init__(self) -> None:
        self.context = _FakeContext()
        self.context_kwargs: dict = {}

    def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return self.context


class _FakeChromium:
    def __init__(self) -> None:
        self.launch_kwargs: dict = {}

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return _FakeBrowser()


def test_launch_sync_page_passes_args_and_shared_ua():
    chromium = _FakeChromium()
    playwright = SimpleNamespace(chromium=chromium)

    browser, context, page = scraper_base.launch_sync_page(
        playwright, headless=False, launch_args=["--no-sandbox"]
    )

    assert chromium.launch_kwargs == {"headless": False, "args": ["--no-sandbox"]}
    assert browser.context_kwargs == {
        "user_agent": scraper_base.DEFAULT_USER_AGENT,
        "viewport": scraper_base.DEFAULT_VIEWPORT,
    }
    assert context is browser.context and page is browser.context.page


def test_raw_content_defaults_and_fields():
    content = scraper_base.RawContent(
        platform="douyin", platform_id="123", url="https://x/1"
    )
    assert content.author is None
    assert content.image_urls == [] and content.metadata == {}
    assert content.video_url is None and content.caption is None

    filled = scraper_base.RawContent(
        platform="douyin",
        platform_id="1",
        url="u",
        author="作者",
        image_urls=["a"],
        video_url="v",
        caption="文案",
        metadata={"k": 1},
    )
    assert filled.image_urls == ["a"] and filled.metadata == {"k": 1}


class _ConcreteScraper(scraper_base.BaseScraper):
    """只为执行四个 abstractmethod 的占位体（返回 None）。"""

    platform = "demo"

    async def login(self):
        return await super().login()

    async def search(self, keyword: str, count: int = 20):
        return await super().search(keyword, count)

    async def get_feed(self, count: int = 20):
        return await super().get_feed(count)

    async def close(self):
        return await super().close()


async def test_base_scraper_abstract_shells_return_none():
    scraper = _ConcreteScraper()
    assert scraper.platform == "demo"
    assert await scraper.login() is None
    assert await scraper.search("穿搭") is None
    assert await scraper.get_feed(5) is None
    assert await scraper.close() is None


# ═══════════════════════════════════════════════════════════════
#  task_events
# ═══════════════════════════════════════════════════════════════


async def test_broadcast_task_event_forwards_payload(monkeypatch):
    import app.routers.ws as ws

    seen: list[dict] = []

    async def _broadcast(payload: dict) -> None:
        seen.append(payload)

    monkeypatch.setattr(ws.manager, "broadcast", _broadcast)
    payload = {"type": "task_event", "event": "running", "task_id": 3, "task_type": "x"}

    assert await task_events.broadcast_task_event(payload) is None
    assert seen == [payload]


async def test_broadcast_task_event_swallows_failures(monkeypatch):
    import app.routers.ws as ws

    async def _boom(_payload: dict) -> None:
        raise RuntimeError("WS 已断开")

    monkeypatch.setattr(ws.manager, "broadcast", _boom)

    # 广播失败不能影响任务主流程
    assert await task_events.broadcast_task_event({"task_id": 1}) is None


# ═══════════════════════════════════════════════════════════════
#  f2_bloggers.register_batch_bloggers
# ═══════════════════════════════════════════════════════════════


async def _insp() -> Inspiration:
    async with async_session() as db:
        insp = Inspiration(file_path="images/f2b.jpg", media_type="image")
        db.add(insp)
        await db.commit()
        return insp


async def _count_links() -> list[InspirationBlogger]:
    from sqlalchemy import select

    async with async_session() as db:
        return (await db.execute(select(InspirationBlogger))).scalars().all()


async def test_register_bloggers_skips_ineligible_entries(client):
    insp = await _insp()
    entries = [
        {"inspiration_id": "", "author_dir": "作者A"},  # 无素材 ID
        {"inspiration_id": insp.id, "author_dir": "  "},  # 无作者
        {"inspiration_id": insp.id, "author_dir": "作者B", "blogger_id": 9},  # 已绑定
        {"inspiration_id": insp.id, "author_dir": "√"},  # 归一化后为空
    ]
    async with async_session() as db:
        stats = await f2_bloggers.register_batch_bloggers(db, entries)

    assert stats["authors"] == 0
    assert await _count_links() == []


async def test_register_bloggers_creates_auto_blogger_and_links(client):
    """未绑定作者：新建 auto_collect 博主并按置信度绑定本批素材（幂等去重）。"""
    insp1 = await _insp()
    insp2 = await _insp()
    entries = [
        {"inspiration_id": insp1.id, "author_dir": "里香1√", "blogger_id": None},
        {"inspiration_id": insp2.id, "author_dir": "里香1√"},
    ]
    async with async_session() as db:
        stats = await f2_bloggers.register_batch_bloggers(db, entries)

    assert stats == {
        "authors": 1,
        "created": 1,
        "reused": 0,
        "ambiguous": 0,
        "linked": 2,
        "existing": 0,
        "failed": 0,
    }
    async with async_session() as db:
        from sqlalchemy import select

        blogger = (await db.execute(select(Blogger))).scalars().one()
        assert blogger.name == "里香1√"
        assert blogger.platform == "douyin"
        assert blogger.source == f2.AUTO_BLOGGER_SOURCE
    links = await _count_links()
    assert {link.inspiration_id for link in links} == {insp1.id, insp2.id}
    assert all(link.confidence == f2.AUTHOR_MATCH_CONFIDENCE for link in links)

    # 幂等：再跑一次复用博主、关联全部已存在
    async with async_session() as db:
        again = await f2_bloggers.register_batch_bloggers(db, entries)
    assert again["created"] == 0
    assert again["reused"] == 1
    assert again["existing"] == 2 and again["linked"] == 0


async def test_register_bloggers_reuses_existing_by_normalized_name(client):
    insp = await _insp()
    async with async_session() as db:
        db.add(Blogger(name="里香", platform="douyin", source="manual"))
        await db.commit()

    async with async_session() as db:
        stats = await f2_bloggers.register_batch_bloggers(
            db, [{"inspiration_id": insp.id, "author_dir": "里香2√"}]
        )

    assert stats["reused"] == 1 and stats["created"] == 0 and stats["linked"] == 1


async def test_register_bloggers_ambiguous_same_name_is_skipped(client):
    """库里已有两条同名（归一化后）博主：不猜，计入 ambiguous 且不建关联。"""
    insp = await _insp()
    async with async_session() as db:
        db.add_all(
            [
                Blogger(name="里香", platform="douyin", source="manual"),
                Blogger(name="里香1√", platform="douyin", source="ai_generated"),
            ]
        )
        await db.commit()

    async with async_session() as db:
        stats = await f2_bloggers.register_batch_bloggers(
            db, [{"inspiration_id": insp.id, "author_dir": "里香"}]
        )

    assert stats["ambiguous"] == 1
    assert stats["created"] == 0 and stats["linked"] == 0
    assert await _count_links() == []


async def test_register_bloggers_counts_missing_inspiration_as_failed(client):
    async with async_session() as db:
        stats = await f2_bloggers.register_batch_bloggers(
            db, [{"inspiration_id": "no-such-id", "author_dir": "新作者"}]
        )

    assert stats["failed"] == 1
    assert stats["created"] == 1 and stats["linked"] == 0
    assert await _count_links() == []


async def test_register_bloggers_link_exception_counted_as_failed(client, monkeypatch):
    insp = await _insp()
    original = f2_bloggers.blogger_service

    class _Boom:
        async def link_batch(self, *_args, **_kwargs):
            raise RuntimeError("关联炸了")

    monkeypatch.setattr(f2_bloggers, "blogger_service", _Boom())
    async with async_session() as db:
        stats = await f2_bloggers.register_batch_bloggers(
            db, [{"inspiration_id": insp.id, "author_dir": "新作者"}]
        )
    monkeypatch.setattr(f2_bloggers, "blogger_service", original)

    assert stats["failed"] == 1
    assert stats["linked"] == 0 and stats["created"] == 1


# ═══════════════════════════════════════════════════════════════
#  f2_preview
# ═══════════════════════════════════════════════════════════════


def test_scan_root_three_modes():
    fake_f2 = SimpleNamespace(
        DEFAULT_F2_COLLECT_ROOT="collect-root",
        DEFAULT_F2_LIKE_ROOT="like-root",
        DEFAULT_F2_ROOT="post-root",
    )
    assert f2_preview._scan_root(fake_f2, "collection") == "collect-root"
    assert f2_preview._scan_root(fake_f2, "like") == "like-root"
    assert f2_preview._scan_root(fake_f2, "post") == "post-root"
    assert f2_preview._scan_root(fake_f2, "unknown") == "post-root"


def test_resolve_authors_include_unknown_returns_scope(monkeypatch):
    monkeypatch.setattr(f2, "load_f2_authors", lambda _d: [{"nickname": "不应被调用"}])
    scope = f2_preview._resolve_authors(f2, {"已登记": []}, [" 里香 ", ""], include_unknown=True)
    assert scope == {"里香"}

    assert f2_preview._resolve_authors(f2, {}, [], include_unknown=True) is None


def test_resolve_authors_without_douyin_bloggers_no_filter(monkeypatch):
    """库里没有抖音博主：白名单不生效，仅按点名单过滤。"""
    assert f2_preview._resolve_authors(f2, {}, ["作者A"], include_unknown=False) == {"作者A"}
    assert f2_preview._resolve_authors(f2, {}, [], include_unknown=False) is None


def test_resolve_authors_union_and_intersection(monkeypatch):
    bloggers = {"里香": [{"id": 1, "name": "里香", "platform_user_id": "sec-1"}]}
    monkeypatch.setattr(
        f2,
        "load_f2_authors",
        lambda _d: [
            {"nickname": "里香1√", "sec_user_id": "sec-1"},
            {"nickname": "陌生人", "sec_user_id": "sec-2"},
        ],
    )

    # 不给点名单：允许范围 = 库内博主 ∪ 按 sec_user_id 命中的 f2 昵称
    allowed = f2_preview._resolve_authors(f2, bloggers, [], include_unknown=False)
    assert allowed == {"里香"}

    # 点名已登记作者：交集保留
    assert f2_preview._resolve_authors(f2, bloggers, ["里香"], include_unknown=False) == {"里香"}
    # 点名未登记作者：交集为空（与执行侧口径一致，一个都不导入）
    assert f2_preview._resolve_authors(f2, bloggers, ["陌生人"], include_unknown=False) == set()


def test_resolve_authors_degrades_when_user_db_unreadable(monkeypatch):
    """读取 f2 用户库失败：预览退化为「按点名单/不限作者」，不 500。"""

    def _boom(_dir):
        raise OSError("douyin_users.db 损坏")

    monkeypatch.setattr(f2, "load_f2_authors", _boom)
    bloggers = {"里香": [{"id": 1, "name": "里香"}]}

    assert f2_preview._resolve_authors(f2, bloggers, ["作者A"], include_unknown=False) == {"作者A"}
    assert f2_preview._resolve_authors(f2, bloggers, [], include_unknown=False) is None


def test_preview_import_sync_empty_directory(client, monkeypatch, tmp_path):
    monkeypatch.setattr(f2, "scan_directory", lambda _root: [])

    result = f2_preview.preview_import_sync(root=tmp_path / "empty", fetch_mode="post")

    assert result["root"] == str(tmp_path / "empty")
    assert result["files"] == 0 and result["files_to_import"] == 0
    assert result["works"] == 0
    assert result["skipped"] == {}
    assert result["authors_scope"] is None
    assert result["has_any"] is False


def _fake_decisions():
    return [
        SimpleNamespace(action="import", item=SimpleNamespace(work_key="w1")),
        SimpleNamespace(action="import", item=SimpleNamespace(work_key="w1")),
        SimpleNamespace(action="import", item=SimpleNamespace(work_key="w2")),
        SimpleNamespace(action="skip", item=SimpleNamespace(work_key="w3")),
    ]


def test_preview_import_sync_counts_and_scope(client, monkeypatch, tmp_path):
    """非空目录：作品按 work_key 去重、跳过明细/延迟作品/哈希缓存原样返回。"""
    seen: dict = {}
    monkeypatch.setattr(f2, "scan_directory", lambda _root: [object(), object()])
    monkeypatch.setattr(f2, "library_db_path", lambda: tmp_path / "lib.db")
    monkeypatch.setattr(f2, "load_douyin_bloggers", lambda _db: {"里香": [{"id": 1}]})
    monkeypatch.setattr(f2, "load_dedup_index", lambda _db: "dedup")
    monkeypatch.setattr(
        f2,
        "build_plan_with_cache",
        lambda files, dedup, **kwargs: (
            seen.update(kwargs) or _fake_decisions(),
            {"已在库（内容相同）": 2},
            3,
            {"computed": 1},
        ),
    )

    result = f2_preview.preview_import_sync(
        root=tmp_path / "root",
        fetch_mode="like",  # 我的列表不做作者白名单
        limit=100,
        skip_live=True,
        include_unknown_authors=True,
        db_path=tmp_path / "override.db",
    )

    assert result["root"] == str(tmp_path / "root")
    assert result["files"] == 2
    assert result["files_to_import"] == 3
    assert result["works"] == 2  # w1 两次去重成 1 个作品 + w2
    assert result["skipped"] == {"已在库（内容相同）": 2}
    assert result["deferred_works"] == 3
    assert result["hash_cache"] == {"computed": 1}
    assert result["authors_scope"] is None  # like 模式不过滤作者
    assert result["has_any"] is True
    # 执行参数透传：作者过滤 None + limit/skip_live
    assert seen["authors"] is None
    assert seen["limit"] == 100 and seen["skip_live"] is True


def test_preview_import_sync_post_mode_authors_scope(client, monkeypatch, tmp_path):
    """发布模式：作者白名单解析结果以排序列表返回（空集合 ≠ 不过滤）。"""
    monkeypatch.setattr(f2, "scan_directory", lambda _root: [object()])
    monkeypatch.setattr(f2, "library_db_path", lambda: tmp_path / "lib.db")
    monkeypatch.setattr(f2, "load_douyin_bloggers", lambda _db: {})
    monkeypatch.setattr(f2, "load_dedup_index", lambda _db: "dedup")
    monkeypatch.setattr(
        f2,
        "build_plan_with_cache",
        lambda files, dedup, **kwargs: (_fake_decisions(), {}, 0, {}),
    )

    result = f2_preview.preview_import_sync(
        root=tmp_path,
        authors=["乙", "甲"],
        include_unknown_authors=False,
    )

    # 库里没有抖音博主 → 只按点名单过滤；排序后返回
    assert result["authors_scope"] == ["乙", "甲"]


async def test_preview_f2_import_delegates_to_thread(client, monkeypatch):
    captured: dict = {}

    def _fake_sync(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(f2_preview, "preview_import_sync", _fake_sync)

    result = await f2_preview.preview_f2_import(
        authors=["a"], limit=5, skip_live=True, include_unknown_authors=True, fetch_mode="collection"
    )

    assert result == {"ok": True}
    assert captured == {
        "authors": ["a"],
        "limit": 5,
        "skip_live": True,
        "include_unknown_authors": True,
        "fetch_mode": "collection",
    }


@pytest.mark.parametrize("explicit,include_unknown,expected", [([], True, None), (["a"], True, {"a"})])
def test_resolve_authors_scope_variants(explicit, include_unknown, expected):
    assert f2_preview._resolve_authors(f2, {"x": []}, explicit, include_unknown) == expected
