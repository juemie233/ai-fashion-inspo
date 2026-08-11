"""小红书爬虫：基于 Playwright + stealth 插件绕过反爬检测。

使用方式:
  scraper = XiaohongshuScraper(headless=True)
  results = await scraper.search("JK制服", count=10)
  await scraper.close()
"""

import asyncio
import logging
import os
from pathlib import Path

from app.config import settings
from app.scrapers.base import BaseScraper, RawContent

logger = logging.getLogger(__name__)


class XiaohongshuScraper(BaseScraper):
    """小红书平台爬虫 — 关键词搜索 + 笔记图片提取。"""

    platform = "xiaohongshu"

    def __init__(self, headless: bool = True, cookie_file: str | None = None):
        self.headless = headless
        self.cookie_file = cookie_file
        self._browser = None
        self._context = None
        self._page = None

    async def _ensure_browser(self):
        """延迟初始化浏览器（避免导入时加载 Playwright）。"""
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
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        # 注入反检测脚本
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
        """)
        self._page = await self._context.new_page()

    async def login(self) -> bool:
        """尝试加载 Cookie 文件恢复登录态。"""
        await self._ensure_browser()
        if self.cookie_file and os.path.exists(self.cookie_file):
            try:
                import json
                with open(self.cookie_file, encoding="utf-8") as f:
                    cookies = json.load(f)
                await self._context.add_cookies(cookies)
                logger.info(f"已加载 {len(cookies)} 个 Cookie")
                return True
            except Exception as e:
                logger.warning(f"Cookie 加载失败: {e}")
        return False

    async def search(
        self, keyword: str, count: int = 20
    ) -> list[RawContent]:
        """搜索穿搭关键词并提取笔记图片链接。"""
        await self._ensure_browser()
        await self.login()

        results: list[RawContent] = []
        search_url = (
            f"https://www.xiaohongshu.com/search_result/"
            f"?keyword={keyword}&source=web_search_result_notes"
        )

        try:
            logger.info(f"小红书搜索: {keyword}")
            await self._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

            # 等待搜索结果加载
            await asyncio.sleep(3)
            await self._page.wait_for_selector("section.note-item", timeout=15000)

            # 提取笔记卡片
            cards = await self._page.query_selector_all("section.note-item")
            logger.info(f"找到 {len(cards)} 条笔记")

            for card in cards[:count]:
                try:
                    # 获取笔记链接
                    link_el = await card.query_selector("a.cover")
                    if not link_el:
                        continue
                    href = await link_el.get_attribute("href")
                    if not href:
                        continue

                    # 构造完整 URL
                    if href.startswith("/"):
                        url = f"https://www.xiaohongshu.com{href}"
                    else:
                        url = href

                    # 获取封面图
                    img_el = await card.query_selector("img")
                    image_urls = []
                    if img_el:
                        src = await img_el.get_attribute("src")
                        if src:
                            image_urls.append(src)

                    # 获取标题
                    title_el = await card.query_selector(".title")
                    caption = None
                    if title_el:
                        caption = await title_el.inner_text()

                    # 获取作者
                    author_el = await card.query_selector(".author .name")
                    author = None
                    if author_el:
                        author = await author_el.inner_text()

                    # 从 URL 提取平台 ID
                    platform_id = url.rstrip("/").split("/")[-1]

                    results.append(RawContent(
                        platform=self.platform,
                        platform_id=platform_id,
                        url=url,
                        author=author,
                        image_urls=image_urls,
                        caption=caption,
                    ))
                except Exception as e:
                    logger.warning(f"解析笔记卡片失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"小红书搜索失败: {e}")

        return results

    async def get_feed(self, count: int = 20) -> list[RawContent]:
        """浏览发现页推荐流，提取穿搭相关笔记。"""
        await self._ensure_browser()
        await self.login()

        results: list[RawContent] = []
        try:
            logger.info(f"小红书发现页 (目标 {count} 条)")
            await self._page.goto(
                "https://www.xiaohongshu.com/explore",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await asyncio.sleep(3)

            # 滚动加载更多
            for _ in range(min(count // 10, 5)):
                await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)

            # 提取笔记
            cards = await self._page.query_selector_all("section.note-item")
            for card in cards[:count]:
                try:
                    link_el = await card.query_selector("a.cover")
                    if not link_el:
                        continue
                    href = await link_el.get_attribute("href")
                    img_el = await card.query_selector("img")
                    image_urls = [await img_el.get_attribute("src")] if img_el else []

                    platform_id = href.rstrip("/").split("/")[-1] if href else ""
                    url = f"https://www.xiaohongshu.com{href}" if href and href.startswith("/") else href

                    results.append(RawContent(
                        platform=self.platform,
                        platform_id=platform_id,
                        url=url or "",
                        image_urls=image_urls,
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.error(f"小红书发现页失败: {e}")

        return results

    async def close(self):
        """关闭浏览器实例。"""
        try:
            if self._browser:
                await self._browser.close()
            if hasattr(self, "_pw"):
                await self._pw.stop()
        except Exception:
            pass
