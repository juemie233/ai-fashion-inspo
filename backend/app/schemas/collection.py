"""收藏合集的请求/响应模型（从 routers/collections.py 归位，见 CLAUDE.md 的分层约定：
schemas/ 放 Pydantic 模型，路由只做参数校验与编排）。
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer

class SmartQueryJSON(BaseModel):
    """智能合集筛选条件（query_json 的契约结构；未知键忽略，向前兼容）。"""

    keyword: str | None = None  # 关键词（标签名/作者名/文件名）
    tag_ids: list[int] = Field(default_factory=list)  # 标签 ID 列表
    tag_mode: Literal["and", "or"] = "and"  # 标签组合语义
    source_types: list[str] | None = None  # 来源类型列表（None/空 = 全部）
    media_type: Literal["image", "video"] | None = None  # 媒体类型（None = 全部）
    is_favorite: bool | None = None  # 收藏筛选（null = 不筛选）
    min_rating: int | None = Field(None, ge=0, le=5)  # 评分下限（0 = 不筛选）
    start_date: str | None = None  # 上传日期下限（YYYY-MM-DD）
    end_date: str | None = None  # 上传日期上限（YYYY-MM-DD）

    def to_storage(self) -> dict[str, Any]:
        """转为入库存储结构：剔除 None 与空默认值（空列表、默认 and 语义），
        保证 query_json 只保留用户显式设置的筛选条件。"""
        data = self.model_dump()
        if not data.get("tag_ids"):
            data.pop("tag_ids", None)
        if not data.get("source_types"):
            data.pop("source_types", None)
        if data.get("tag_mode") == "and":
            data.pop("tag_mode", None)
        return {k: v for k, v in data.items() if v is not None}


class CollectionCreate(BaseModel):
    """创建合集请求体：query_json 缺省为手动合集，提供时为智能合集。"""

    name: str = Field(min_length=1, max_length=50, description="合集名 1~50 字")
    description: str | None = None
    query_json: SmartQueryJSON | None = None
    # 非空即建二级收藏夹（父必须是一级；最多两级，见 models/collection.py）
    parent_id: int | None = Field(None, description="父收藏夹 ID（二级才有）")


class CollectionUpdate(BaseModel):
    """更新合集请求体（部分更新，仅更新显式提供的字段）。"""

    name: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = None
    cover_inspiration_id: str | None = None  # 显式 null = 清空手动封面
    query_json: SmartQueryJSON | None = None  # 仅智能合集可更新（转手动用 solidify）


class AddInspirationsRequest(BaseModel):
    """批量加入/移出素材的请求体。"""

    inspiration_ids: list[str] = Field(min_length=1, max_length=200)


class ItemReorderRequest(BaseModel):
    """合集内成员拖拽排序的请求体（按新顺序提交全部成员 ID）。"""

    ordered_ids: list[str] = Field(min_length=1, max_length=1000)


class CollectionReorderRequest(BaseModel):
    """合集列表拖拽排序的请求体（按新顺序提交合集 ID）。"""

    ordered_ids: list[int] = Field(min_length=1, max_length=1000)


class CollectionOut(BaseModel):
    """合集输出（列表 / 创建 / 更新 / 固化共用）。"""

    id: int
    name: str
    description: str | None = None
    kind: str  # manual 手动合集 / smart 智能合集
    position: int
    parent_id: int | None = None  # 二级收藏夹的父 ID；一级为 null
    auto_source: str | None = None  # 自动维护来源（douyin）；用户自建为 null
    child_count: int = 0  # 直接子收藏夹数（一级节点用）
    cover_inspiration_id: str | None = None
    cover_thumbnail_path: str | None = None
    item_count: int | None = None  # 手动 = 成员数；智能 = null（懒计算）
    query_json: dict | None = None  # 仅智能合集返回
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, dt: datetime | None) -> str | None:
        from app.utils.time import format_utc

        return format_utc(dt)
