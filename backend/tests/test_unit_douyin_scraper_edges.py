"""抖音网页搜索爬虫补充测试：浏览器初始化、异常降级、Cookie/关闭。

对应 ``app/scrapers/douyin.py`` 既有 test_unit_platform_scrapers.py 未覆盖的分支：
``_ensure_browser_sync`` / ``_ensure_browser``、``login``、``get_feed``、
单卡解析异常跳过、页面级异常返回空、``cookies`` 异常降级、``close`` 清理。

全部打桩，不启动 Playwright、不触网。
"""

import asyncio
from types import SimpleNamespace

from app.scrapers import douyin
from app.scrapers.douyin import DouyinScraper


class _FakeCard:
    """假搜索卡片：可指定 img/a 查询结果或让 query_selector 抛错。"""

    def __init__(self, img_src=None, href=None, raise_on_query=False):
        self._img = img_src
        self._href = href
        self._raise = raise_on_query

    def query_selector(self, sel: str):
        if self._raise:
            raise RuntimeError("card detached")
        if sel == "img" and self._img is not None:
            return SimpleNamespace(get_attribute=lambda _n: self._img)
        if sel == "a" and self._href is not None:
            return SimpleNamespace(get_attribute=lambda _n: self._href)
        return None


class _FakePage:
    def __init__(self, cards=(), goto_error: Exception | None = None):
        self._cards = list(cards)
        self._goto_error = goto_error
        self.urls: list[str] = []

    def goto(self, url, **_kw):
        self.urls.append(url)
        if self._goto_error is not None:
            raise self._goto_error

    def query_selector_all(self, _sel: str):
        return self._cards


def _search_with(page, keyword="穿搭", count=20):
    """以打桩页面直接跑 search（跳过浏览器初始化与真实 sleep）。"""
    s = DouyinScraper()
    s._page = page

    async def _noop() -> None:
        return None

    s._ensure_browser = _noop  # type: ignore[method-assign]
    return asyncio.run(s.search(keyword, count))


def test_ensure_browser_skips_when_browser_ready(monkeypatch):
    """已有浏览器实例：_ensure_browser 直接返回，不再进线程池初始化。"""
    calls: list[str] = []
    s = DouyinScraper()
    s._browser = object()
    monkeypatch.setattr(douyin.asyncio, "to_thread", lambda fn, *a, **k: calls.append("x"))

    asyncio.run(s._ensure_browser())

    assert calls == []


def test_ensure_browser_initializes_in_thread(monkeypatch):
    """未初始化时通过 to_thread 调用同步初始化。"""
    s = DouyinScraper()
    seen: list[str] = []

    async def _fake_to_thread(fn, *args, **kwargs):
        seen.append("called")
        fn(*args, **kwargs)

    monkeypatch.setattr(douyin.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(s, "_ensure_browser_sync", lambda: seen.append("sync"))

    asyncio.run(s._ensure_browser())

    assert seen == ["called", "sync"]


def test_ensure_browser_sync_builds_page(monkeypatch):
    """同步初始化：启动 playwright 并交给统一 launch_sync_page 拉起页面。"""
    fake_pw = SimpleNamespace(start=lambda: fake_pw)
    ctx = object()
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: fake_pw)
    monkeypatch.setattr(
        douyin, "launch_sync_page", lambda pw, headless, args: ("browser", ctx, "page")
    )

    s = DouyinScraper(headless=False)
    s._ensure_browser_sync()

    assert s._pw is fake_pw
    assert (s._browser, s._context, s._page) == ("browser", ctx, "page")


def test_login_and_get_feed_stubs():
    """登录/推荐流：这两个能力当前刻意未实现，返回固定空值。"""
    s = DouyinScraper()
    assert asyncio.run(s.login()) is False
    assert asyncio.run(s.get_feed(count=5)) == []


def test_search_skips_broken_card(monkeypatch):
    """单张卡片解析抛异常：跳过该卡，其余结果照常返回。"""
    monkeypatch.setattr(douyin.time, "sleep", lambda *_a: None)
    cards = [
        _FakeCard(raise_on_query=True),
        _FakeCard(img_src="//cdn.x/a.jpg", href="//www.douyin.com/video/123?from=1"),
    ]

    out = _search_with(_FakePage(cards))

    assert [(r.platform_id, r.image_urls) for r in out] == [
        ("123", ["https://cdn.x/a.jpg"])
    ]


def test_search_page_exception_returns_empty(monkeypatch):
    """页面导航失败：整体降级为空列表，不把异常抛给调用方。"""
    monkeypatch.setattr(douyin.time, "sleep", lambda *_a: None)

    out = _search_with(_FakePage(goto_error=RuntimeError("net down")))

    assert out == []


def test_search_card_without_img_or_link(monkeypatch):
    """卡片缺图片/链接：仍生成一条结果（platform_id 与图片为空）。"""
    monkeypatch.setattr(douyin.time, "sleep", lambda *_a: None)

    out = _search_with(_FakePage([_FakeCard()]))

    assert len(out) == 1
    assert out[0].platform_id == ""
    assert out[0].url == ""
    assert out[0].image_urls == []


def test_search_plain_href_and_trailing_slash(monkeypatch):
    """非协议相对链接保持原样；platform_id 去掉末尾斜杠与查询串。"""
    monkeypatch.setattr(douyin.time, "sleep", lambda *_a: None)
    cards = [
        _FakeCard(href="https://www.douyin.com/note/456?x=1"),
        _FakeCard(img_src="https://cdn.x/b.jpg", href="https://www.douyin.com/video/789/"),
    ]

    out = _search_with(_FakePage(cards))

    assert [r.platform_id for r in out] == ["456", "789"]
    assert out[1].url == "https://www.douyin.com/video/789/"


class _BrokenCookiesCtx:
    def cookies(self):
        raise RuntimeError("browser closed")


class _GoodCookiesCtx:
    def cookies(self):
        return [{"name": "ttwid", "value": "1"}]


def test_cookies_degrades_on_error():
    """上下文读取 Cookie 抛错：降级为空字典（下载器仍可无鉴权尝试）。"""
    s = DouyinScraper()
    s._context = _BrokenCookiesCtx()
    assert s.cookies() == {}

    s._context = _GoodCookiesCtx()
    assert s.cookies() == {"ttwid": {"name": "ttwid", "value": "1"}}


class _Closable:
    def __init__(self, raise_on_close=False):
        self.closed = 0
        self._raise = raise_on_close

    def close(self):
        self.closed += 1
        if self._raise:
            raise RuntimeError("already closed")


class _Stoppable:
    def __init__(self, raise_on_stop=False):
        self.stopped = 0
        self._raise = raise_on_stop

    def stop(self):
        self.stopped += 1
        if self._raise:
            raise RuntimeError("pw gone")


def test_close_closes_browser_and_playwright():
    s = DouyinScraper()
    browser = _Closable()
    pw = _Stoppable()
    s._browser = browser
    s._pw = pw

    asyncio.run(s.close())

    assert browser.closed == 1 and pw.stopped == 1


def test_close_swallows_errors():
    """关闭阶段的异常只记 debug，不向上抛（避免退出流程被卡住）。"""
    s = DouyinScraper()
    s._browser = _Closable(raise_on_close=True)
    s._pw = _Stoppable(raise_on_stop=True)

    asyncio.run(s.close())  # 不应抛异常
