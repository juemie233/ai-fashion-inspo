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


# ── 用户搜索解析 ──


class _FakeUserLink:
    def __init__(self, href: str, text: str):
        self._href = href
        self._text = text

    def get_attribute(self, _name: str):
        return self._href

    def inner_text(self) -> str:
        return self._text


class _FakeUserSearchPage:
    def __init__(self, links, body_text="正常页面"):
        self._links = list(links)
        self._body = body_text

    def goto(self, _url, **_kw):
        pass

    def inner_text(self, _sel: str) -> str:
        return self._body

    def query_selector(self, _sel: str):
        return object()  # 触碰即视为结果已渲染，立即结束等待循环

    def wait_for_selector(self, *_a, **_k):
        pass

    def query_selector_all(self, sel: str):
        if "/user/profile/" in sel:
            return list(self._links)
        return []


def _make_logged_in_scraper(page) -> XiaohongshuScraper:
    s = _make_scraper()
    s._page = page
    # 浏览器初始化与 Cookie 加载打桩（解析逻辑是本测试对象）
    s._ensure_browser_sync = lambda: None
    s._load_cookies_sync = lambda: True
    return s


def test_xhs_search_users_parses_name_id_and_url():
    """用户卡片解析：昵称截断、小红书号提取、主页 URL 补全、user_id 去查询串。"""
    links = [
        _FakeUserLink(
            "/user/profile/abc123?x=1",
            "穿搭日记\n小红书号：98765432\n1 关注 2 粉丝",
        ),
    ]
    out = asyncio.run(_make_logged_in_scraper(_FakeUserSearchPage(links))
                      .search_users("穿搭", limit=10))
    assert out == [{
        "name": "穿搭日记",
        # profile_url 保留原始查询串；仅 platform_user_id 去除查询串
        "profile_url": "https://www.xiaohongshu.com/user/profile/abc123?x=1",
        "platform_user_id": "abc123",
        "xhs_id": "98765432",
    }]


def test_xhs_search_users_name_truncates_at_rednote_id():
    """昵称与「小红书号」同行时截断噪声，不把编号混进昵称。"""
    links = [_FakeUserLink("/user/profile/u1", "穿搭日记 小红书号：abc123")]
    out = asyncio.run(_make_logged_in_scraper(_FakeUserSearchPage(links))
                      .search_users("穿搭"))
    assert out[0]["name"] == "穿搭日记"
    assert out[0]["xhs_id"] == "abc123"


def test_xhs_search_users_dedup_by_user_id():
    """同一用户多个卡片（卡片区 + 全局兜底重复命中）只保留一个。"""
    links = [
        _FakeUserLink("/user/profile/dup", "用户甲"),
        _FakeUserLink("/user/profile/dup?from=note", "用户甲"),
        _FakeUserLink("/user/profile/other", "用户乙"),
    ]
    out = asyncio.run(_make_logged_in_scraper(_FakeUserSearchPage(links))
                      .search_users("穿搭", limit=10))
    assert [u["platform_user_id"] for u in out] == ["dup", "other"]


def test_xhs_search_users_respects_limit():
    links = [_FakeUserLink(f"/user/profile/u{i}", f"用户{i}") for i in range(5)]
    out = asyncio.run(_make_logged_in_scraper(_FakeUserSearchPage(links))
                      .search_users("穿搭", limit=2))
    assert len(out) == 2


def test_xhs_search_users_non_profile_href_skipped():
    """兜底选择器混入的非用户主页链接跳过。"""
    links = [_FakeUserLink("/explore/n1", "一篇笔记")]
    out = asyncio.run(_make_logged_in_scraper(_FakeUserSearchPage(links))
                      .search_users("穿搭"))
    assert out == []


def test_xhs_search_users_login_wall_raises():
    """登录墙：明确报错而非静默返回空列表（调用方据此提示导 Cookie）。"""
    page = _FakeUserSearchPage([], body_text="登录后查看搜索结果 手机号登录")
    with pytest.raises(RuntimeError, match="登录"):
        asyncio.run(_make_logged_in_scraper(page).search_users("穿搭"))


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
