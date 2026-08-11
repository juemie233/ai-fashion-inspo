"""标签的 Pydantic 请求/响应模型。"""

from datetime import datetime
from pydantic import BaseModel, Field


class TagCreate(BaseModel):
    """创建标签"""
    name: str = Field(min_length=1, max_length=64)
    category: str = Field(default="free")


class TagUpdate(BaseModel):
    """更新标签（部分更新）"""
    name: str | None = Field(None, min_length=1, max_length=64)
    category: str | None = None


class TagOut(BaseModel):
    """标签输出（含使用次数）"""
    id: int
    name: str
    category: str
    created_at: datetime | None = None
    usage_count: int = 0

    model_config = {"from_attributes": True}


class TagCategoryGroup(BaseModel):
    """按类别分组的标签列表"""
    category: str
    tags: list[TagOut]


class TagMergeRequest(BaseModel):
    """合并标签请求：将 source_tag_id 合并到 target_tag_id，删除源标签"""
    source_tag_id: int
    target_tag_id: int
