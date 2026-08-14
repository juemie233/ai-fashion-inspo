"""标签的 Pydantic 请求/响应模型。"""

from datetime import datetime
from pydantic import BaseModel, Field, field_serializer


class TagCreate(BaseModel):
    """创建标签"""
    name: str = Field(min_length=1, max_length=64)
    category: str = Field(default="free")


class TagUpdate(BaseModel):
    """更新标签（部分更新）"""
    name: str | None = Field(None, min_length=1, max_length=64)
    category: str | None = None
    pinned: bool | None = None
    sort_order: int | None = None
    description: str | None = Field(None, max_length=255)


class TagOut(BaseModel):
    """标签输出（含使用次数）"""
    id: int
    name: str
    category: str
    source: str = "seed"
    pinned: bool = False
    sort_order: int = 0
    description: str | None = None
    created_at: datetime | None = None
    usage_count: int = 0

    model_config = {"from_attributes": True}

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


class TagCategoryGroup(BaseModel):
    """按类别分组的标签列表"""
    category: str
    tags: list[TagOut]


class TagMergeRequest(BaseModel):
    """合并标签请求：将 source_tag_id 合并到 target_tag_id，删除源标签"""
    source_tag_id: int
    target_tag_id: int


class TagBatchDelete(BaseModel):
    """批量删除标签请求"""
    tag_ids: list[int]


class TagImportItem(BaseModel):
    """导入标签的单项"""
    name: str
    category: str = "free"


class TagImportRequest(BaseModel):
    """批量导入标签请求"""
    tags: list[TagImportItem]


class AliasCreate(BaseModel):
    """创建标签别名"""
    alias: str = Field(min_length=1, max_length=64)


class AliasOut(BaseModel):
    """标签别名输出"""
    id: int
    tag_id: int
    alias: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


class TagReorderItem(BaseModel):
    """单个标签的自定义排序项"""
    id: int
    sort_order: int


class TagReorderRequest(BaseModel):
    """批量更新标签自定义排序"""
    items: list[TagReorderItem]
