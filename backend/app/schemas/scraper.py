"""采集任务的 Pydantic 请求/响应模型。"""

import json
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.utils.time import format_utc


class ScraperTaskCreate(BaseModel):
    """创建采集任务"""
    platform: str = Field(..., description="xiaohongshu | douyin")
    keywords: list[str] = Field(default=[], description="搜索关键词列表")
    max_count: int = Field(default=100, ge=1, le=500)
    headless: bool = Field(default=True, description="是否无头模式（默认无头，与 scraper_browser_headless 配置一致）")
    cdp_port: int | None = Field(default=None, description="CDP 端口，连接真实 Chrome 实现零检测采集（仅小红书生效）")
    cookie_file: str | None = Field(default=None, description="Cookie 文件路径")
    sort_mode: str | None = Field(default=None, description="搜索排序: general | latest | popular（仅小红书搜索模式生效）")
    collect_mode: str | None = Field(default=None, description="采集模式: search | user | topic（当前仅 search 生效，其余预留）")


class ScraperTaskOut(BaseModel):
    """采集任务输出"""
    id: int
    platform: str
    status: str
    config: str | None = None
    items_found: int
    items_added: int
    diagnostics: str | None = None  # 漏斗日志 JSON 字符串
    resume_token: str | None = None  # 断点续采进度 JSON 字符串
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer('started_at', 'finished_at', 'created_at')
    def serialize_datetime(self, dt: datetime | None, _info) -> str | None:
        return format_utc(dt)


class ScraperScheduleCreate(BaseModel):
    """创建定时采集计划"""

    platform: str = Field(..., description="xiaohongshu | douyin")
    keywords: list[str] = Field(..., description="搜索关键词列表（至少 1 个）")
    max_count: int = Field(default=20, ge=1, le=500)
    sort_mode: str | None = Field(default=None, description="general | latest | popular（仅小红书生效）")
    interval_minutes: int = Field(default=1440, ge=30, le=10080, description="执行间隔（分钟）")
    enabled: bool = Field(default=True, description="创建后是否启用")


class ScraperScheduleUpdate(BaseModel):
    """更新定时采集计划（仅更新传入的字段）"""

    keywords: list[str] | None = Field(default=None, description="搜索关键词列表")
    max_count: int | None = Field(default=None, ge=1, le=500)
    sort_mode: str | None = Field(default=None, description="general | latest | popular")
    interval_minutes: int | None = Field(default=None, ge=30, le=10080)
    enabled: bool | None = Field(default=None)


class ScraperScheduleOut(BaseModel):
    """定时采集计划输出"""

    id: int
    platform: str
    keywords: list[str]
    max_count: int
    sort_mode: str | None = None
    enabled: bool
    interval_minutes: int
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_task_id: int | None = None
    run_count: int
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("keywords", mode="before")
    @classmethod
    def parse_keywords(cls, v):
        """ORM 中 keywords 为 JSON 字符串，反序列化为列表。"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return []
        return v or []

    @field_serializer('next_run_at', 'last_run_at', 'created_at')
    def serialize_datetime(self, dt: datetime | None, _info) -> str | None:
        return format_utc(dt)
