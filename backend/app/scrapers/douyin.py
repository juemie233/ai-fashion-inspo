"""抖音爬虫：基于 Playwright 采集网页版内容。

可靠性：低-中等 — 抖音网页版功能非常有限。
大部分内容仅在移动端可见，网页版仅能获取搜索结果的封面图。
完整功能需要 Android 模拟器 + ADB 自动化（远期增强）。
"""

import logging

from app.scrapers.base import BaseScraper, RawContent

logger = logging.getLogger(__name__)


class DouyinScraper(BaseScraper):
    """抖音平台爬虫。

    当前限制：
    - 网页版仅支持搜索，无发现页推荐流
    - 视频播放受限，只能获取封面图
    - 完整功能需要移动端自动化方案

    远期计划：Android 模拟器 + ADB 实现完整的视频和图片采集。
    """

    platform = "douyin"

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser = None
        self._page = None

    async def login(self) -> bool:
        """抖音网页版登录 — 需要扫描二维码。Phase 4 实现。"""
        logger.info("抖音登录 — Phase 4 实现")
        return False

    async def search(self, keyword: str, count: int = 20) -> list[RawContent]:
        """在抖音网页版搜索并提取视频封面图。Phase 4 实现。"""
        logger.info(f"抖音搜索 '{keyword}' — Phase 4 实现")
        return []

    async def get_feed(self, count: int = 20) -> list[RawContent]:
        """抖音网页版不提供发现页推荐流。"""
        logger.info("抖音发现页 — 网页版不支持")
        return []

    async def close(self):
        """关闭浏览器实例。"""
        if self._browser:
            await self._browser.close()
