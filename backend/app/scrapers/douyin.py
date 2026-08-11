"""抖音爬虫：基于 Playwright sync API 采集网页版搜索结果。"""

import asyncio
import logging
import time

from app.scrapers.base import BaseScraper, RawContent

logger = logging.getLogger(__name__)


class DouyinScraper(BaseScraper):
    """抖音平台爬虫 — 使用 sync Playwright 在 threadpool 中运行。"""

    platform = "douyin"

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser = None
        self._context = None
        self._page = None
        self._pw = None

    def _ensure_browser_sync(self):
        if self._browser is not None:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        self._page = self._context.new_page()

    async def _ensure_browser(self):
        if self._browser is not None:
            return
        await asyncio.to_thread(self._ensure_browser_sync)

    async def login(self) -> bool:
        return False

    async def search(self, keyword: str, count: int = 20) -> list[RawContent]:
        await self._ensure_browser()

        def _search():
            results: list[RawContent] = []
            try:
                logger.info(f"抖音搜索: {keyword}")
                self._page.goto(
                    f"https://www.douyin.com/search/{keyword}?type=general",
                    wait_until="domcontentloaded", timeout=30000,
                )
                time.sleep(3)

                cards = self._page.query_selector_all('li[data-e2e="search-card"]')
                if not cards:
                    cards = self._page.query_selector_all("li.search-result-card")

                for card in cards[:count]:
                    try:
                        img_el = card.query_selector("img")
                        img_src = img_el.get_attribute("src") if img_el else None
                        if img_src and img_src.startswith("//"):
                            img_src = "https:" + img_src

                        link_el = card.query_selector("a")
                        href = link_el.get_attribute("href") if link_el else ""
                        if href.startswith("//"):
                            href = "https:" + href

                        platform_id = href.rstrip("/").split("/")[-1].split("?")[0] if href else ""
                        results.append(RawContent(
                            platform=self.platform, platform_id=platform_id,
                            url=href, image_urls=[img_src] if img_src else [],
                        ))
                    except Exception:
                        continue

                logger.info(f"抖音找到 {len(results)} 条结果")
            except Exception as e:
                logger.error(f"抖音搜索失败: {e}")
            return results

        return await asyncio.to_thread(_search)

    async def get_feed(self, count: int = 20) -> list[RawContent]:
        return []

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
