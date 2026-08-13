"""搜索的 Pydantic 请求/响应模型。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.inspiration import InspirationOut


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


class VectorSearchItem(BaseModel):
    """向量搜索结果单项（含相似度分数）"""
    inspiration: InspirationOut
    score: float = 0.0


class VectorSearchOut(BaseModel):
    """向量搜索响应（语义搜索 / 以图搜图）"""
    query_type: Literal["text", "image"]
    query_text: str | None = None
    items: list[VectorSearchItem]
    total: int


class SimilarItemOut(BaseModel):
    """相似素材推荐单项"""
    inspiration: InspirationOut
    similarity: float
    shared_tags: int
    match_source: Literal["visual", "tag", "hybrid"]  # 视觉 / 标签 / 混合


class SimilarOut(BaseModel):
    """相似素材推荐响应"""
    source: InspirationOut
    similar: list[SimilarItemOut]


class VectorStatusOut(BaseModel):
    """向量检索能力状态"""
    lancedb_available: bool
    lancedb_dir: str
    text_embedding: dict
    image_embedding: dict
    text_vector_count: int
    image_vector_count: int
