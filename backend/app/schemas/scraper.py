"""采集任务的 Pydantic 请求/响应模型。"""

from datetime import datetime
from pydantic import BaseModel, Field, field_serializer


class ScraperTaskCreate(BaseModel):
    """创建采集任务"""
    platform: str = Field(..., description="xiaohongshu | douyin")
    keywords: list[str] = Field(default=[], description="搜索关键词列表")
    max_count: int = Field(default=100, ge=1, le=500)
    headless: bool = Field(default=False, description="是否无头模式")
    cdp_port: int | None = Field(default=None, description="CDP 端口，连接真实 Chrome 实现零检测采集")
    cookie_file: str | None = Field(default=None, description="Cookie 文件路径")


class ScraperTaskOut(BaseModel):
    """采集任务输出"""
    id: int
    platform: str
    status: str
    config: str | None = None
    items_found: int
    items_added: int
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer('started_at', 'finished_at', 'created_at')
    def serialize_datetime(self, dt: datetime | None, _info) -> str | None:
        if dt is None:
            return None
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
