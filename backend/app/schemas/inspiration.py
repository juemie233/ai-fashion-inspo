"""灵感素材的 Pydantic 请求/响应模型。"""

from datetime import datetime
from pydantic import BaseModel, Field


class TagOut(BaseModel):
    """标签输出"""
    id: int
    name: str
    category: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class InspirationTagOut(BaseModel):
    """灵感-标签关联输出（含置信度）"""
    tag: TagOut
    confidence: float

    model_config = {"from_attributes": True}


class InspirationCreate(BaseModel):
    """创建灵感的请求体"""
    source_type: str = "manual_upload"
    source_url: str | None = None
    source_author: str | None = None
    source_platform_id: str | None = None
    media_type: str = "image"


class InspirationUpdate(BaseModel):
    """更新灵感的请求体（部分更新）"""
    is_favorite: bool | None = None
    source_author: str | None = None


class InspirationOut(BaseModel):
    """灵感列表项输出"""
    id: str
    source_type: str
    source_url: str | None = None
    source_author: str | None = None
    source_platform_id: str | None = None
    file_path: str
    thumbnail_path: str | None = None
    media_type: str
    dominant_colors: str | None = None
    is_favorite: bool = False
    created_at: datetime
    updated_at: datetime | None = None
    tags: list[InspirationTagOut] = []
    analysis_status: str | None = "none"  # none | analyzing | done | error

    model_config = {"from_attributes": True}


class InspirationListOut(BaseModel):
    """灵感分页列表"""
    items: list[InspirationOut]
    total: int
    page: int
    size: int


class InspirationDetailOut(InspirationOut):
    """灵感详情（含 AI 分析日志）"""
    analysis_logs: list["AnalysisLogOut"] = []

    model_config = {"from_attributes": True}


class AnalysisLogOut(BaseModel):
    """AI 分析日志"""
    id: int
    model_name: str
    processing_time_ms: int | None = None
    error: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
