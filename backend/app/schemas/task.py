"""任务队列的 Pydantic 请求/响应模型。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_serializer


class TaskOut(BaseModel):
    """任务状态输出（前端轮询使用）。"""

    id: int
    type: str
    status: str  # pending/running/success/failed/cancelled
    progress: int  # 0~100
    total: int
    done: int
    result: dict[str, Any] | None = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 2
    next_retry_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("next_retry_at", "created_at", "updated_at")
    def serialize_datetime(self, dt: datetime | None, _info) -> str | None:
        """将 naive UTC datetime 格式化为带 Z 后缀的 ISO 字符串。"""
        if dt is None:
            return None
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TaskListOut(BaseModel):
    """任务分页列表。"""

    items: list[TaskOut]
    total: int
    page: int
    size: int


class TaskCancelOut(BaseModel):
    """取消任务响应。"""

    message: str
    task_id: int
