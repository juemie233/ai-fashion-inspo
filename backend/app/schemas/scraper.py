"""采集任务的 Pydantic 请求/响应模型。"""

from datetime import datetime
from pydantic import BaseModel, Field


class ScraperTaskCreate(BaseModel):
    """创建采集任务"""
    platform: str = Field(..., description="xiaohongshu | douyin")
    keywords: list[str] = Field(default=[], description="搜索关键词列表")
    max_count: int = Field(default=50, ge=1, le=500)


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
