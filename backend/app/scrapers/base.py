"""爬虫基类接口：每个平台都需要实现此抽象基类。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawContent:
    """爬虫提取的原始内容数据。"""
    platform: str
    platform_id: str
    url: str
    author: str | None = None
    image_urls: list[str] = field(default_factory=list)
    video_url: str | None = None
    caption: str | None = None
    metadata: dict = field(default_factory=dict)


"""两个平台共用的浏览器 UA（此前在 douyin/xiaohongshu 里各写了一遍，逐字节相同）。"""
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

"""两个平台共用的视口（1920×1080，与原实现一致）。"""
DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}


def launch_sync_page(
    playwright: Any, headless: bool, launch_args: list[str]
) -> tuple[Any, Any, Any]:
    """用已启动的 sync Playwright 建「浏览器 + 上下文 + 页面」，返回三元组。

    只统一**确实一样**的部分：UA 与视口（两份原实现逐字节相同，是最容易漂移的重复）。
    不强行合流的部分：
    - ``launch_args`` 由各平台自己给：抖音要 ``--disable-dev-shm-usage``，小红书要关掉
      ``AutomationControlled``；
    - 小红书额外的 ``add_init_script``（抹 ``navigator.webdriver``）由它在取到 context 后自己加。
    """
    browser = playwright.chromium.launch(headless=headless, args=launch_args)
    context = browser.new_context(user_agent=DEFAULT_USER_AGENT, viewport=DEFAULT_VIEWPORT)
    return browser, context, context.new_page()


class BaseScraper(ABC):
    """平台爬虫的抽象基类。"""

    platform: str = "unknown"

    @abstractmethod
    async def login(self) -> bool:
        """登录平台。返回 True 表示登录成功。"""
        ...

    @abstractmethod
    async def search(
        self, keyword: str, count: int = 20
    ) -> list[RawContent]:
        """按关键词搜索内容。"""
        ...

    @abstractmethod
    async def get_feed(self, count: int = 20) -> list[RawContent]:
        """获取发现页/推荐流的内容。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """清理资源（浏览器、连接等）。"""
        ...
