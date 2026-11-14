"""小红书爬虫（`scrapers/xiaohongshu.py`）的 Cookie 归一化与浏览器初始化测试。

Cookie 导出格式转换是最容易出错又最难察觉的一环：Chrome 扩展导出的 `sameSite`
为 `no_restriction/unspecified`、过期时间叫 `expirationDate`、还带一堆 Playwright
不认识的字段——直接 `add_cookies` 会报错，而报错被吞掉后表现是**「搜索永远走到
登录墙」**。本文件覆盖归一化规则、Cookie 文件加载成功/失败分支与浏览器懒加载。
"""

import asyncio
import json

import pytest

from app.scrapers import xiaohongshu as xhs


class TestNormalizeCookies:
    def test_skips_invalid_entries(self):
        assert xhs.normalize_cookies([]) == []
        assert xhs.normalize_cookies([None, "str", {}, {"value": "v"}]) == []  # 没有 name 一律丢

    def test_maps_fields_with_defaults(self):
        cookies = xhs.normalize_cookies(
            [{"name": "a", "value": None, "domain": None, "path": None}]
        )
        assert cookies == [{"name": "a", "value": "", "domain": "", "path": "/", "sameSite": "Lax"}]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("no_restriction", "None"),
            ("unspecified", "Lax"),
            ("lax", "Lax"),
            ("strict", "Strict"),
            ("LAX", "Lax"),  # 大小写归一
            ("unknown-value", "Lax"),  # 未知值按宽容默认
            (None, "Lax"),
            ("", "Lax"),
        ],
    )
    def test_same_site_mapping(self, raw, expected):
        cookie = {"name": "a", "value": "v", "sameSite": raw}
        assert xhs.normalize_cookies([cookie])[0]["sameSite"] == expected

    def test_expiration_date_becomes_expires_float(self):
        cookie = {"name": "a", "value": "v", "expirationDate": "1700000000.5"}
        assert xhs.normalize_cookies([cookie])[0]["expires"] == 1700000000.5

    def test_no_expiration_date_means_no_expires_key(self):
        cookie = {"name": "a", "value": "v"}
        assert "expires" not in xhs.normalize_cookies([cookie])[0]

    def test_only_truthy_http_only_and_secure_are_kept(self):
        truthy = xhs.normalize_cookies(
            [{"name": "a", "value": "v", "httpOnly": True, "secure": True}]
        )[0]
        assert truthy["httpOnly"] is True
        assert truthy["secure"] is True

        falsy = xhs.normalize_cookies(
            [{"name": "a", "value": "v", "httpOnly": False, "secure": 0}]
        )[0]
        assert "httpOnly" not in falsy
        assert "secure" not in falsy

    def test_extra_fields_are_dropped(self):
        cookie = {
            "name": "a",
            "value": "v",
            "hostOnly": True,
            "session": True,
            "storeId": "0",
            "sameSite": "lax",
        }
        assert set(xhs.normalize_cookies([cookie])[0]) == {
            "name",
            "value",
            "domain",
            "path",
            "sameSite",
        }


class _FakeContext:
    def __init__(self, *, raise_on_add=None):
        self.added: list[list[dict]] = []
        self.init_scripts: list[str] = []
        self._raise = raise_on_add

    def add_cookies(self, cookies):
        if self._raise is not None:
            raise self._raise
        self.added.append(cookies)

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def new_page(self):
        return "fake-page"


def _scraper_with_context(tmp_path, cookie_payload, *, raise_on_add=None):
    scraper = xhs.XiaohongshuScraper()
    scraper._context = _FakeContext(raise_on_add=raise_on_add)
    if cookie_payload is not None:
        path = tmp_path / "cookies.json"
        path.write_text(json.dumps(cookie_payload), encoding="utf-8")
        scraper.cookie_file = str(path)
    return scraper


class TestLoadCookies:
    def test_without_cookie_file_returns_false(self):
        scraper = xhs.XiaohongshuScraper()
        assert scraper._load_cookies_sync() is False

    def test_missing_file_returns_false(self, tmp_path):
        scraper = xhs.XiaohongshuScraper()
        scraper._context = _FakeContext()
        scraper.cookie_file = str(tmp_path / "不存在.json")
        assert scraper._load_cookies_sync() is False

    def test_valid_file_loads_normalized_cookies(self, tmp_path):
        scraper = _scraper_with_context(
            tmp_path, [{"name": "a", "value": "v", "sameSite": "no_restriction"}]
        )

        assert scraper._load_cookies_sync() is True
        added = scraper._context.added[0]
        assert added[0]["name"] == "a"
        assert added[0]["sameSite"] == "None"  # 归一化后才交给 Playwright
        assert scraper.last_login_error == ""

    def test_invalid_json_records_reason_and_returns_false(self, tmp_path):
        scraper = xhs.XiaohongshuScraper()
        scraper._context = _FakeContext()
        path = tmp_path / "cookies.json"
        path.write_text("{坏", encoding="utf-8")
        scraper.cookie_file = str(path)

        assert scraper._load_cookies_sync() is False
        assert scraper.last_login_error  # 供 search 明确报错，避免静默走到登录墙

    def test_add_cookies_failure_records_reason(self, tmp_path):
        scraper = _scraper_with_context(
            tmp_path, [{"name": "a", "value": "v"}], raise_on_add=RuntimeError("字段不合法")
        )

        assert scraper._load_cookies_sync() is False
        assert "字段不合法" in scraper.last_login_error


class TestBrowserLifecycle:
    async def test_login_delegates_to_sync_loader(self, monkeypatch):
        scraper = xhs.XiaohongshuScraper()

        async def _ensure():
            scraper._browser = object()

        calls: list[bool] = []

        def _load():
            calls.append(True)
            return True

        monkeypatch.setattr(scraper, "_ensure_browser", _ensure)
        monkeypatch.setattr(scraper, "_load_cookies_sync", _load)

        assert await scraper.login() is True
        assert calls == [True]

    async def test_ensure_browser_is_noop_when_already_started(self, monkeypatch):
        scraper = xhs.XiaohongshuScraper()
        scraper._browser = object()  # 已初始化

        def _should_not_run():
            raise AssertionError("不应重复初始化浏览器")

        monkeypatch.setattr(scraper, "_ensure_browser_sync", _should_not_run)
        await scraper._ensure_browser()  # 直接返回，不抛

    async def test_ensure_browser_runs_sync_init_in_thread(self, monkeypatch):
        scraper = xhs.XiaohongshuScraper()
        ran: list[str] = []

        def _init():
            ran.append("sync")

        monkeypatch.setattr(scraper, "_ensure_browser_sync", _init)

        await scraper._ensure_browser()
        await asyncio.sleep(0)  # to_thread 完成后返回

        assert ran == ["sync"]

    def test_missing_playwright_gives_install_hint(self, monkeypatch):
        import sys

        scraper = xhs.XiaohongshuScraper()
        # 让 `from playwright.sync_api import sync_playwright` 抛 ImportError
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
        monkeypatch.setitem(sys.modules, "playwright", None)

        with pytest.raises(RuntimeError) as exc_info:
            scraper._ensure_browser_sync()

        assert "未安装 Playwright" in str(exc_info.value)

    def test_ensure_browser_sync_launches_and_injects_stealth(self, monkeypatch):
        import sys
        import types

        scraper = xhs.XiaohongshuScraper()
        context = _FakeContext()
        launched: dict = {}

        class _FakePw:
            def start(self):
                return "fake-pw-instance"

        fake_module = types.ModuleType("playwright.sync_api")
        fake_module.sync_playwright = lambda: _FakePw()
        monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
        monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))

        def _launch(pw, headless, args):
            launched.update({"pw": pw, "headless": headless, "args": args})
            return ("fake-browser", context, "first-page")

        monkeypatch.setattr(xhs, "launch_sync_page", _launch)

        scraper._ensure_browser_sync()

        assert scraper._browser == "fake-browser"
        assert launched["args"] == [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ]
        # 反检测脚本 + 用 context.new_page() 重新开页（首屏页带 UA/视口初始化）
        assert any("webdriver" in s for s in context.init_scripts)
        assert scraper._page == "fake-page"
