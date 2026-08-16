"""爬虫基类接口：每个平台都需要实现此抽象基类。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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
