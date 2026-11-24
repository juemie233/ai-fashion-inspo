"""抖音收藏夹 → 穿搭大标签映射的请求/响应模型（2026-11-24）。"""

from typing import Literal

from pydantic import BaseModel, Field


class FolderTagMappingOut(BaseModel):
    """映射总览的一行（含未建映射的待处理行）。"""

    folder_name: str
    mode: Literal["tag", "skip", "pending"]
    needs_confirm: bool = False
    tag_id: int | None = None
    tag_name: str | None = None
    tag_category: str | None = None
    local_collection_id: int | None = None
    local_item_count: int | None = None


class FolderTagUpsertRequest(BaseModel):
    """新建/更新映射。

    - ``mode='tag'``：给 ``tag_id`` 或按 ``new_tag_name``（默认收藏夹名）解析标签；
    - 同名标签已属于其它类别时返回 409，前端三选一后带
      ``reuse_existing_category=True`` 或改写 ``new_tag_name`` 重试；
    - ``mode='skip'``：该夹不再自动打标；``mode='pending'``：重置为待处理。
    """

    folder_name: str = Field(min_length=1, max_length=50)
    mode: Literal["tag", "skip", "pending"]
    tag_id: int | None = None
    new_tag_name: str | None = Field(None, min_length=1, max_length=64)
    reuse_existing_category: bool = False


class FolderTagApplyResult(BaseModel):
    """重打结果（单个或全部）。"""

    applied: int
    folders: list[dict] = Field(default_factory=list)


class FolderTagImpactOut(BaseModel):
    """标签影响面（改名/改类别前确认）。"""

    tag_id: int
    name: str
    category: str
    usage_count: int
    mapped_folders: list[str] = Field(default_factory=list)
