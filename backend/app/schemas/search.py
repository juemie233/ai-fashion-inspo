"""搜索的 Pydantic 请求/响应模型。"""

from pydantic import BaseModel, Field


class SearchParams(BaseModel):
    """多维度搜索参数"""
    include_tags: list[str] = []
    exclude_tags: list[str] = []
    dominant_color: str | None = None
    source_type: str | None = None
    date_from: str | None = None  # ISO 日期字符串
    date_to: str | None = None
    combine: str = "AND"  # AND | OR
    page: int = Field(default=1, ge=1)
    size: int = Field(default=50, ge=1, le=200)


class SearchResult(BaseModel):
    """搜索结果"""
    items: list  # InspirationOut
    total: int
    page: int
    size: int
    query: SearchParams
