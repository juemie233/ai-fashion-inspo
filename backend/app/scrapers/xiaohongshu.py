"""小红书爬虫：基于 Playwright sync API + asyncio.to_thread 绕过反爬检测。"""

import asyncio
import json
import logging
import os

from app.scrapers.base import BaseScraper, RawContent

logger = logging.getLogger(__name__)


class XiaohongshuScraper(BaseScraper):
    """小红书平台爬虫 — 使用 sync Playwright 在 threadpool 中运行。"""

    platform = "xiaohongshu"

    def __init__(self, headless: bool = True, cookie_file: str | None = None):
        self.headless = headless
        self.cookie_file = cookie_file
        self._browser = None
        self._context = None
        self._page = None
        self._pw = None

    def _ensure_browser_sync(self):
        """同步初始化浏览器。"""
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "未安装 Playwright: pip install playwright && playwright install chromium"
            )
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
        self._page = self._context.new_page()

    async def _ensure_browser(self):
        if self._browser is not None:
            return
        await asyncio.to_thread(self._ensure_browser_sync)

    async def login(self) -> bool:
        await self._ensure_browser()

        def _login():
            if self.cookie_file and os.path.exists(self.cookie_file):
                try:
                    with open(self.cookie_file, encoding="utf-8") as f:
                        cookies = json.load(f)
                    self._context.add_cookies(cookies)
                    logger.info(f"已加载 {len(cookies)} 个 Cookie")
                    return True
                except Exception as e:
                    logger.warning(f"Cookie 加载失败: {e}")
            return False

        return await asyncio.to_thread(_login)

    async def search(
        self, keyword: str, count: int = 20
    ) -> list[RawContent]:
        await self._ensure_browser()
        await self.login()

        def _search():
            results: list[RawContent] = []
            # 关键词 URL 编码：中文/空格/特殊字符直接拼进 URL 会导致请求异常
            from urllib.parse import quote

            search_url = (
                f"https://www.xiaohongshu.com/search_result/"
                f"?keyword={quote(keyword)}&source=web_search_result_notes"
            )
            try:
                logger.info(f"小红书搜索: {keyword}")
                self._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                import time
                time.sleep(3)
                # 小红书页面结构：使用多种选择器容错
                selectors = [
                    "section.note-item", "div.note-item", ".note-item",
                    "a[href*='explore']", "a[href*='discovery']",
                    "div[class*='note']", "div[class*='card']",
                ]
                cards = []
                for sel in selectors:
                    try:
                        self._page.wait_for_selector(sel, timeout=3000)
                        cards = self._page.query_selector_all(sel)
                        if cards:
                            logger.info(f"使用选择器 '{sel}' 找到 {len(cards)} 条笔记")
                            break
                    except Exception:
                        continue

                if not cards:
                    # 兜底：查找所有包含图片链接的元素
                    cards = self._page.query_selector_all("a[href*='/explore/']")
                    if not cards:
                        cards = self._page.query_selector_all("a[href*='/search_result/']")
                    logger.info(f"兜底选择器找到 {len(cards)} 条")

                for card in cards[:count]:
                    try:
                        # 获取链接
                        href = ""
                        if card.evaluate("el => el.tagName") == "A":
                            href = card.get_attribute("href") or ""
                        else:
                            link_el = card.query_selector("a")
                            if link_el:
                                href = link_el.get_attribute("href") or ""

                        if not href:
                            continue
                        url = f"https://www.xiaohongshu.com{href}" if href.startswith("/") else href

                        # 获取图片
                        img_el = card.query_selector("img")
                        image_urls = []
                        if img_el:
                            src = img_el.get_attribute("src") or img_el.get_attribute("data-src") or ""
                            if src:
                                image_urls.append(src)

                        # 获取文本
                        caption = card.inner_text()[:200] if hasattr(card, 'inner_text') else None

                        # 提取平台 ID
                        platform_id = url.rstrip("/").split("/")[-1].split("?")[0]
                        results.append(RawContent(
                            platform=self.platform,
                            platform_id=platform_id,
                            url=url,
                            image_urls=image_urls,
                            caption=caption,
                        ))
                    except Exception as e:
                        continue
            except Exception as e:
                logger.error(f"小红书搜索失败: {e}")
            return results

        return await asyncio.to_thread(_search)

    async def get_feed(self, count: int = 20) -> list[RawContent]:
        await self._ensure_browser()

        def _get_feed():
            results: list[RawContent] = []
            import time
            try:
                self._page.goto("https://www.xiaohongshu.com/explore",
                                wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
                for _ in range(min(count // 10, 5)):
                    self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(2)

                cards = self._page.query_selector_all("section.note-item")
                for card in cards[:count]:
                    try:
                        link_el = card.query_selector("a.cover")
                        if not link_el:
                            continue
                        href = link_el.get_attribute("href")
                        img_el = card.query_selector("img")
                        image_urls = [img_el.get_attribute("src")] if img_el else []
                        platform_id = href.rstrip("/").split("/")[-1] if href else ""
                        url = f"https://www.xiaohongshu.com{href}" if href and href.startswith("/") else href
                        results.append(RawContent(
                            platform=self.platform, platform_id=platform_id,
                            url=url or "", image_urls=image_urls,
                        ))
                    except Exception:
                        continue
            except Exception as e:
                logger.error(f"小红书发现页失败: {e}")
            return results

        return await asyncio.to_thread(_get_feed)

    async def close(self):
        def _close():
            try:
                if self._browser:
                    self._browser.close()
                if self._pw:
                    self._pw.stop()
            except Exception:
                pass
        await asyncio.to_thread(_close)
