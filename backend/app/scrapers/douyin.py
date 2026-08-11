"""抖音爬虫：基于 Playwright 采集网页版搜索结果。

限制：网页版仅支持搜索，无发现页，仅能获取封面图。
完整功能需要 Android 模拟器 + ADB 自动化（远期增强）。
"""

import asyncio
import logging

from app.scrapers.base import BaseScraper, RawContent

logger = logging.getLogger(__name__)


class DouyinScraper(BaseScraper):
    """抖音平台爬虫 — 网页版搜索 + 封面图提取。"""

    platform = "douyin"

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser = None
        self._context = None
        self._page = None

    async def _ensure_browser(self):
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "未安装 Playwright，请运行: pip install playwright && playwright install chromium"
            )

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        self._page = await self._context.new_page()

    async def login(self) -> bool:
        logger.info("抖音网页版需扫码登录，跳过")
        return False

    async def search(
        self, keyword: str, count: int = 20
    ) -> list[RawContent]:
        """在抖音网页版搜索并提取视频封面图。"""
        await self._ensure_browser()

        results: list[RawContent] = []
        search_url = (
            f"https://www.douyin.com/search/{keyword}"
            f"?type=general"
        )

        try:
            logger.info(f"抖音搜索: {keyword}")
            await self._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # 提取搜索结果中的视频卡片
            cards = await self._page.query_selector_all('li[data-e2e="search-card"]')
            if not cards:
                cards = await self._page.query_selector_all("li.search-result-card")

            for card in cards[:count]:
                try:
                    # 封面图
                    img_el = await card.query_selector("img")
                    img_src = await img_el.get_attribute("src") if img_el else None
                    if img_src and img_src.startswith("//"):
                        img_src = "https:" + img_src

                    # 链接
                    link_el = await card.query_selector("a")
                    href = await link_el.get_attribute("href") if link_el else ""
                    if href.startswith("//"):
                        href = "https:" + href

                    # 标题
                    title_el = await card.query_selector("[data-e2e='search-card-title']")
                    caption = await title_el.inner_text() if title_el else None

                    platform_id = href.rstrip("/").split("/")[-1].split("?")[0] if href else ""

                    results.append(RawContent(
                        platform=self.platform,
                        platform_id=platform_id,
                        url=href,
                        caption=caption,
                        image_urls=[img_src] if img_src else [],
                    ))
                except Exception:
                    continue

            logger.info(f"抖音找到 {len(results)} 条结果")

        except Exception as e:
            logger.error(f"抖音搜索失败: {e}")

        return results

    async def get_feed(self, count: int = 20) -> list[RawContent]:
        """抖音网页版不提供发现页。"""
        return []

    async def close(self):
        try:
            if self._browser:
                await self._browser.close()
            if hasattr(self, "_pw"):
                await self._pw.stop()
        except Exception:
            pass
