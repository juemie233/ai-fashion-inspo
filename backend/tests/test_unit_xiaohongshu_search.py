"""小红书搜索/发现页解析（`scrapers/xiaohongshu.py` 的 `search` / `get_feed` / 关注列表）。

采集链路里真正「根据页面结构解析结果」的部分，原覆盖 45%。它没有单测时，页面一改版
就静默返回空列表（用户看到「采集完成 0 条」），且异常被吞掉难以定位。这里用假
Playwright page 覆盖：

- 选择器容错链：第一个选择器没结果就换下一个；全部失败再走「图片链接」兜底；
- 卡片解析：A 标签直接取 href / 非 A 标签找内部 a；相对路径补全域名；图片 src 或 data-src；
  caption 截 200 字；平台 ID 从 URL 取（去掉 query 与尾斜杠）；
- 单条卡片解析异常只跳过该条，不影响其余；整页 goto 失败只记日志返回空；
- 关注列表：Cookie 加载失败要抛可读 RuntimeError（不能静默空列表）。
"""

import time
from types import SimpleNamespace

import pytest

from app.scrapers import xiaohongshu as xhs
from app.scrapers.base import RawContent


class _El:
    """通用假元素：按属性表返回 get_attribute。"""

    def __init__(self, attrs: dict | None = None, *, inner_text: str | None = None):
        self._attrs = attrs or {}
        self._text = inner_text

    def get_attribute(self, name: str):
        return self._attrs.get(name)

    def inner_text(self):
        if self._text is None:
            raise AttributeError("no text")
        return self._text


class _Card:
    """假卡片：tagName / href / 内部 a / img / 文案 / 是否抛异常。"""

    def __init__(
        self,
        *,
        tag: str = "A",
        href: str | None = "/explore/abc123?xsec_token=1",
        link_href: str | None = None,
        img_src: str | None = "https://img.example/a.jpg",
        img_data_src: str | None = None,
        caption: str | None = "法式穿搭分享",
        has_inner_text: bool = True,
        raise_evaluate: bool = False,
        raise_query: bool = False,
    ):
        self.tag = tag
        self.href = href
        self.link_href = link_href
        self.img_src = img_src
        self.img_data_src = img_data_src
        self.caption = caption
        self.has_inner_text = has_inner_text
        self.raise_evaluate = raise_evaluate
        self.raise_query = raise_query

    def evaluate(self, _js: str):
        if self.raise_evaluate:
            raise RuntimeError("页面元素已失效")
        return self.tag

    def get_attribute(self, name: str):
        if name == "href":
            return self.href
        return None

    def query_selector(self, selector: str):
        if self.raise_query:
            raise RuntimeError("元素已 detach")
        if selector == "a":
            return _El({"href": self.link_href}) if self.link_href is not None else None
        if selector == "a.cover":
            if self.link_href is None:
                return None
            return _El({"href": self.link_href})
        if selector == "img":
            if self.img_src is None and self.img_data_src is None:
                return None
            return _El({"src": self.img_src, "data-src": self.img_data_src})
        return None

    def inner_text(self):
        if not self.has_inner_text:
            raise AttributeError("no text")
        return self.caption


class _NoTextCard:
    """没有 inner_text 属性的卡片：验证 `hasattr` 兜底分支（caption=None）。"""

    def __init__(self, *, href: str):
        self._href = href

    def evaluate(self, _js: str):
        return "A"

    def get_attribute(self, name: str):
        return self._href if name == "href" else None

    def query_selector(self, _selector: str):
        return None


class _Page:
    def __init__(
        self,
        *,
        cards: dict[str, list] | None = None,
        raise_wait_for: set[str] | None = None,
        raise_goto: bool = False,
    ):
        self.cards = cards or {}
        self.raise_wait_for = raise_wait_for or set()
        self.raise_goto = raise_goto
        self.gotos: list[str] = []
        self.queries: list[str] = []
        self.evaluated: list[str] = []

    def goto(self, url: str, **_kwargs):
        if self.raise_goto:
            raise RuntimeError("net::ERR_CONNECTION_RESET")
        self.gotos.append(url)

    def wait_for_selector(self, selector: str, **_kwargs):
        if selector in self.raise_wait_for:
            raise RuntimeError(f"选择器不存在: {selector}")
        return None

    def query_selector_all(self, selector: str):
        self.queries.append(selector)
        return list(self.cards.get(selector, []))

    def evaluate(self, js: str):
        self.evaluated.append(js)
        return None


async def _noop(*_args, **_kwargs):
    return None


def _scraper(page: _Page) -> xhs.XiaohongshuScraper:
    scraper = xhs.XiaohongshuScraper()
    scraper._page = page
    scraper._browser = object()  # 让 _ensure_browser 直接返回；login 会被 patch
    return scraper


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)


class TestSearch:
    async def test_selector_fallback_chain_and_parsing(self, monkeypatch):
        cards = [
            _Card(),
            _Card(
                tag="DIV",
                href=None,
                link_href="/explore/def456",
                img_src=None,
                img_data_src="https://img.example/b.jpg",
            ),
        ]
        page = _Page(
            cards={
                "section.note-item": [],  # 第一个选择器命中但没结果
                "div.note-item": cards,  # 第二个有结果
            },
            raise_wait_for={"section.note-item"},
        )
        scraper = _scraper(page)
        monkeypatch.setattr(scraper, "login", _noop)

        results = await scraper.search("法式 通勤", count=5)

        assert len(results) == 2
        first, second = results
        assert isinstance(first, RawContent)
        assert first.platform == "xiaohongshu"
        assert first.platform_id == "abc123"  # 去掉 query
        assert first.url == "https://www.xiaohongshu.com/explore/abc123?xsec_token=1"
        assert first.image_urls == ["https://img.example/a.jpg"]
        assert first.caption == "法式穿搭分享"
        # 非 A 标签：从内部 a 取 href
        assert second.platform_id == "def456"
        # 中文/空格必须 URL 编码
        assert "keyword=%E6%B3%95%E5%BC%8F%20%E9%80%9A%E5%8B%A4" in page.gotos[0]

    async def test_image_link_fallback(self, monkeypatch):
        page = _Page(
            cards={
                "a[href*='/explore/']": [],
                "a[href*='/search_result/']": [_Card(href="/search_result/xyz")],
            }
        )
        scraper = _scraper(page)
        monkeypatch.setattr(scraper, "login", _noop)

        results = await scraper.search("穿搭", count=10)

        assert [r.platform_id for r in results] == ["xyz"]

    async def test_no_cards_returns_empty(self, monkeypatch):
        page = _Page()
        scraper = _scraper(page)
        monkeypatch.setattr(scraper, "login", _noop)

        assert await scraper.search("穿搭") == []

    async def test_respects_count_limit(self, monkeypatch):
        page = _Page(cards={"section.note-item": [_Card() for _ in range(5)]})
        scraper = _scraper(page)
        monkeypatch.setattr(scraper, "login", _noop)

        assert len(await scraper.search("穿搭", count=2)) == 2

    async def test_skips_card_without_href_and_broken_card(self, monkeypatch):
        page = _Page(
            cards={
                "section.note-item": [
                    _Card(href=None, tag="A"),  # 没有 href
                    _Card(raise_evaluate=True),  # 解析异常
                    _Card(href="https://www.xiaohongshu.com/explore/ok"),
                ]
            }
        )
        scraper = _scraper(page)
        monkeypatch.setattr(scraper, "login", _noop)

        results = await scraper.search("穿搭")

        assert len(results) == 1
        assert results[0].url == "https://www.xiaohongshu.com/explore/ok"  # 绝对地址保持
        assert results[0].platform_id == "ok"

    async def test_absolute_href_untouched_and_caption_none(self, monkeypatch):
        page = _Page(
            cards={
                "section.note-item": [
                    _NoTextCard(href="https://www.xiaohongshu.com/explore/abs")
                ]
            }
        )
        scraper = _scraper(page)
        monkeypatch.setattr(scraper, "login", _noop)

        result = (await scraper.search("穿搭"))[0]
        assert result.url == "https://www.xiaohongshu.com/explore/abs"
        assert result.caption is None
        assert result.image_urls == []

    async def test_goto_failure_is_swallowed(self, monkeypatch):
        scraper = _scraper(_Page(raise_goto=True))
        monkeypatch.setattr(scraper, "login", _noop)

        assert await scraper.search("穿搭") == []  # 不抛


class TestGetFeed:
    async def test_parses_cards_and_skips_broken(self):
        page = _Page(
            cards={
                "section.note-item": [
                    _Card(link_href="/explore/feed1", img_src="https://img.example/f.jpg"),
                    _Card(link_href=None),  # 没有封面链接 → 跳过
                    _Card(link_href="/explore/boom", raise_query=True),  # 元素已 detach
                    _Card(link_href="https://www.xiaohongshu.com/explore/abs"),
                ]
            }
        )
        scraper = _scraper(page)

        results = await scraper.get_feed(count=10)

        assert [r.platform_id for r in results] == ["feed1", "abs"]
        assert results[0].url == "https://www.xiaohongshu.com/explore/feed1"
        assert results[0].image_urls == ["https://img.example/f.jpg"]
        assert page.evaluated  # 有滚动加载

    async def test_scroll_count_capped_and_goto_failure(self):
        cards = {"section.note-item": [_Card(link_href="/explore/x")]}
        page = _Page(cards=cards)
        scraper = _scraper(page)

        await scraper.get_feed(count=100)
        assert len(page.evaluated) <= 5  # 最多滚 5 次

        failing = _Page(raise_goto=True)
        scraper._page = failing
        assert await scraper.get_feed() == []


class TestFollowingAndClose:
    def test_list_following_requires_cookies(self, monkeypatch):
        scraper = xhs.XiaohongshuScraper()
        monkeypatch.setattr(scraper, "_ensure_browser_sync", lambda: None)
        monkeypatch.setattr(scraper, "_load_cookies_sync", lambda: False)
        scraper.last_login_error = "Cookie 文件缺失或为空"

        with pytest.raises(RuntimeError) as exc_info:
            scraper.list_following_sync()

        assert "小红书 Cookie 加载失败" in str(exc_info.value)

    def test_list_following_parses_via_script(self, monkeypatch):
        import scripts.fetch_xhs_following as fx

        scraper = xhs.XiaohongshuScraper()
        page = _Page()
        scraper._page = page
        monkeypatch.setattr(scraper, "_ensure_browser_sync", lambda: None)
        monkeypatch.setattr(scraper, "_load_cookies_sync", lambda: True)
        monkeypatch.setattr(
            fx, "fetch_following_list", lambda p, max_pages=3: [{"nickname": "甲", "uid": "u1"}]
        )

        result = scraper.list_following_sync(max_pages=2)

        assert result == [{"nickname": "甲", "uid": "u1"}]
        assert page.gotos and "explore" in page.gotos[0]  # 先落到站内页复用登录态

    def test_close_sync_calls_browser_and_pw(self):
        scraper = xhs.XiaohongshuScraper()
        closed: list[str] = []
        scraper._browser = SimpleNamespace(close=lambda: closed.append("browser"))
        scraper._pw = SimpleNamespace(stop=lambda: closed.append("pw"))

        scraper.close_sync()

        assert closed == ["browser", "pw"]

    def test_close_sync_swallows_errors(self):
        def _boom():
            raise RuntimeError("already closed")

        scraper = xhs.XiaohongshuScraper()
        scraper._browser = SimpleNamespace(close=_boom)
        scraper._pw = SimpleNamespace(stop=_boom)

        scraper.close_sync()  # 不抛

    async def test_close_delegates_to_thread(self):
        scraper = xhs.XiaohongshuScraper()
        called: list[bool] = []
        scraper.close_sync = lambda: called.append(True)

        await scraper.close()

        assert called == [True]
