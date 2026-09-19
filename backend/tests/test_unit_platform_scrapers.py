"""平台爬虫类单元测试：Cookie 归一化 / 小红书用户搜索解析 / 抖音搜索提取。

覆盖 app/scrapers/xiaohongshu.py 与 app/scrapers/douyin.py（此前无测试覆盖）。
不启动浏览器：直接实例化爬虫类并注入假 page/context 对象，验证解析逻辑。
"""

import asyncio
import json

import pytest

from app.scrapers.douyin import DouyinScraper
from app.scrapers.xiaohongshu import XiaohongshuScraper, normalize_cookies


# ═══════════════════════════════════════════════════════════════
#  小红书 Cookie 归一化（Chrome 扩展导出 → Playwright 兼容）
# ═══════════════════════════════════════════════════════════════


def test_normalize_cookies_full_conversion():
    """扩展字段完整转换：sameSite / expires / 字段白名单。"""
    raw = [{
        "name": "web_session",
        "value": "abc",
        "domain": ".xiaohongshu.com",
        "path": "/",
        "sameSite": "no_restriction",
        "expirationDate": 1735689600.5,
        "httpOnly": True,
        "secure": True,
        "hostOnly": True,   # Playwright 不认识，应丢弃
        "session": True,    # 同上
        "storeId": "1",     # 同上
    }]
    out = normalize_cookies(raw)
    assert out == [{
        "name": "web_session",
        "value": "abc",
        "domain": ".xiaohongshu.com",
        "path": "/",
        "sameSite": "None",
        "expires": 1735689600.5,
        "httpOnly": True,
        "secure": True,
    }]


@pytest.mark.parametrize("raw,expected", [
    ("no_restriction", "None"),
    ("none", "None"),
    ("unspecified", "Lax"),
    ("lax", "Lax"),
    ("strict", "Strict"),
    ("", "Lax"),
    (None, "Lax"),
    ("weird_value", "Lax"),  # 未知取值宽容回退
])
def test_normalize_cookies_samesite_mapping(raw, expected):
    out = normalize_cookies([{"name": "a", "sameSite": raw}])
    assert out[0]["sameSite"] == expected


def test_normalize_cookies_defaults_and_expiration():
    """缺省字段填充默认值；无过期时间的会话 Cookie 不带 expires。"""
    out = normalize_cookies([{"name": "a"}])
    assert out == [{
        "name": "a", "value": "", "domain": "", "path": "/", "sameSite": "Lax",
    }]


def test_normalize_cookies_skips_invalid_entries():
    """非 dict 项与无 name 项跳过，不中断整批转换。"""
    out = normalize_cookies([
        "not-a-dict",
        {"value": "no-name"},
        {"name": "ok", "value": "v"},
        None,
    ])
    assert len(out) == 1 and out[0]["name"] == "ok"


# ═══════════════════════════════════════════════════════════════
#  小红书爬虫：Cookie 加载 / 用户搜索解析
# ═══════════════════════════════════════════════════════════════


class _FakeContext:
    def __init__(self):
        self.added_cookies = None

    def add_cookies(self, cookies):
        self.added_cookies = cookies


def _make_scraper(cookie_file=None) -> XiaohongshuScraper:
    s = XiaohongshuScraper(cookie_file=cookie_file)
    s._context = _FakeContext()
    return s


def test_xhs_load_cookies_valid_file(tmp_path):
    """合法 Cookie 文件：归一化后注入 context，返回 True。"""
    f = tmp_path / "cookies.json"
    f.write_text(json.dumps([{
        "name": "web_session", "value": "abc", "domain": ".xiaohongshu.com",
        "sameSite": "strict", "expirationDate": 1735689600,
    }]), encoding="utf-8")
    s = _make_scraper(str(f))
    assert s._load_cookies_sync() is True
    assert s.last_login_error == ""
    assert s._context.added_cookies == [{
        "name": "web_session", "value": "abc", "domain": ".xiaohongshu.com",
        "path": "/", "sameSite": "Strict", "expires": 1735689600.0,
    }]


def test_xhs_load_cookies_broken_file_records_error(tmp_path):
    """损坏的 Cookie 文件：返回 False 且记录失败原因（供上层明确报错）。"""
    f = tmp_path / "cookies.json"
    f.write_text("%%%not-json%%%", encoding="utf-8")
    s = _make_scraper(str(f))
    assert s._load_cookies_sync() is False
    assert s.last_login_error != ""


def test_xhs_load_cookies_missing_file(tmp_path):
    s = _make_scraper(str(tmp_path / "nope.json"))
    assert s._load_cookies_sync() is False


# ── 关注列表拉取（博主 uid 解析的唯一来源）──


class _FakeExplorePage:
    def __init__(self):
        self.urls: list[str] = []

    def goto(self, url, **_kw):
        self.urls.append(url)


def _make_following_scraper(page, monkeypatch):
    """打桩浏览器/Cookie/延时的关注列表 scraper，返回 (scraper, 调用记录)。"""
    from scripts import fetch_xhs_following as fx

    monkeypatch.setattr("app.scrapers.xiaohongshu.time.sleep", lambda *_a: None)
    s = _make_scraper()
    s._page = page
    s._ensure_browser_sync = lambda: None
    s._load_cookies_sync = lambda: True
    calls: dict = {}

    def _fake_fetch(page_arg, max_pages=3):
        calls["page"] = page_arg
        calls["max_pages"] = max_pages
        return [{"nickname": "穿搭日记", "uid": "abc123"}]

    monkeypatch.setattr(fx, "fetch_following_list", _fake_fetch)
    return s, calls


def test_xhs_list_following_delegates_after_cookie_load(monkeypatch):
    """关注列表：先落站内页面（带登录态）再调解析，max_pages 透传。"""
    page = _FakeExplorePage()
    s, calls = _make_following_scraper(page, monkeypatch)
    out = s.list_following_sync(max_pages=2)
    assert out == [{"nickname": "穿搭日记", "uid": "abc123"}]
    assert page.urls == ["https://www.xiaohongshu.com/explore"]
    assert calls["page"] is page
    assert calls["max_pages"] == 2


def test_xhs_list_following_without_cookies_raises(monkeypatch):
    """未加载 Cookie → 明确报错（关注列表接口需要登录态，不能静默返回空）。"""
    s, _ = _make_following_scraper(_FakeExplorePage(), monkeypatch)
    s._load_cookies_sync = lambda: False
    s.last_login_error = "Cookie 文件缺失或为空"
    with pytest.raises(RuntimeError, match="Cookie"):
        s.list_following_sync()


# ═══════════════════════════════════════════════════════════════
#  抖音爬虫：搜索结果解析 / Cookie 导出
# ═══════════════════════════════════════════════════════════════


class _FakeDyCard:
    def __init__(self, img_src=None, href=None):
        self._img = img_src
        self._href = href

    def query_selector(self, sel: str):
        if sel == "img" and self._img is not None:
            return _Img(self._img)
        if sel == "a" and self._href is not None:
            return _Img(self._href)
        return None


class _Img:
    def __init__(self, src: str):
        self._src = src

    def get_attribute(self, _name: str):
        return self._src


class _FakeDyPage:
    def __init__(self, primary_cards, fallback_cards=()):
        self._primary = list(primary_cards)
        self._fallback = list(fallback_cards)
        self.urls: list[str] = []

    def goto(self, url, **_kw):
        self.urls.append(url)

    def query_selector_all(self, sel: str):
        if sel == 'li[data-e2e="search-card"]':
            return self._primary
        if sel == "li.search-result-card":
            return self._fallback
        return []


async def _search_with(page, keyword="穿搭", count=20):
    s = DouyinScraper()
    s._page = page

    async def _noop():
        return None  # 打桩浏览器初始化

    s._ensure_browser = _noop
    return await s.search(keyword, count)


def test_douyin_search_extracts_results(monkeypatch):
    """搜索卡片提取：协议相对图片/链接补全 https、platform_id 去查询串。"""
    monkeypatch.setattr("app.scrapers.douyin.time.sleep", lambda *_a: None)
    page = _FakeDyPage([
        _FakeDyCard(img_src="//p3.douyinpic.com/x.jpg", href="//www.douyin.com/video/123?from=1"),
        _FakeDyCard(img_src="https://p9.douyinpic.com/y.jpg", href="https://www.douyin.com/note/456"),
    ])
    out = asyncio.run(_search_with(page, "夏日穿搭"))
    assert page.urls[0].startswith("https://www.douyin.com/search/")
    assert "%E5%A4%8F%E6%97%A5%E7%A9%BF%E6%90%AD" in page.urls[0]  # 关键词已编码
    assert [(r.platform_id, r.image_urls) for r in out] == [
        ("123", ["https://p3.douyinpic.com/x.jpg"]),
        ("456", ["https://p9.douyinpic.com/y.jpg"]),
    ]
    assert all(r.platform == "douyin" for r in out)


def test_douyin_search_fallback_selector(monkeypatch):
    """主选择器无卡片时回退 li.search-result-card。"""
    monkeypatch.setattr("app.scrapers.douyin.time.sleep", lambda *_a: None)
    page = _FakeDyPage([], fallback_cards=[
        _FakeDyCard(img_src="https://p.douyinpic.com/z.jpg", href="https://www.douyin.com/video/789"),
    ])
    out = asyncio.run(_search_with(page))
    assert len(out) == 1 and out[0].platform_id == "789"


def test_douyin_search_respects_count(monkeypatch):
    monkeypatch.setattr("app.scrapers.douyin.time.sleep", lambda *_a: None)
    cards = [_FakeDyCard(href=f"https://www.douyin.com/video/{i}") for i in range(10)]
    out = asyncio.run(_search_with(_FakeDyPage(cards), count=3))
    assert len(out) == 3


def test_douyin_search_empty_page_returns_empty(monkeypatch):
    monkeypatch.setattr("app.scrapers.douyin.time.sleep", lambda *_a: None)
    out = asyncio.run(_search_with(_FakeDyPage([])))
    assert out == []


def test_douyin_cookies_keyed_by_name():
    """Cookie 导出按 name 建索引，供下载器构造请求头。"""
    s = DouyinScraper()
    assert s.cookies() == {}  # 未初始化浏览器

    class _Ctx:
        def cookies(self):
            return [{"name": "ttwid", "value": "1"}, {"name": "sessionid", "value": "2"}]

    s._context = _Ctx()
    assert set(s.cookies().keys()) == {"ttwid", "sessionid"}
