"""灵感素材的 Pydantic 请求/响应模型。"""

from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, Field, field_serializer


class TagOut(BaseModel):
    """标签输出"""
    id: int
    name: str
    category: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


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
    quality_status: Literal["pending", "approved", "rejected"] | None = None  # 人工复核翻案
    quality_reason: str | None = None  # 翻案为 rejected 时的自定义原因


class BatchAddTagsRequest(BaseModel):
    """批量给多个素材关联标签的请求体"""

    inspiration_ids: list[str] = Field(min_length=1, max_length=200)
    names: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        min_length=1, max_length=50
    )
    category: str = "free"
    source: str = "manual"


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
    quality_status: str | None = "pending"
    quality_reason: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    tags: list[InspirationTagOut] = []
    analysis_status: str | None = "none"

    model_config = {"from_attributes": True}

    @field_serializer('created_at', 'updated_at')
    def serialize_datetime(self, dt: datetime | None, _info) -> str | None:
        if dt is None:
            return None
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


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

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
