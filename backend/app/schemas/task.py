"""任务队列的 Pydantic 请求/响应模型。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_serializer

from app.utils.time import format_utc


class TaskOut(BaseModel):
    """任务状态输出（前端轮询使用）。"""

    id: int
    type: str
    status: str  # pending/running/success/failed/cancelled
    priority: int = 0  # 队列优先级：越大越先执行（默认 0，清理类任务 -5）
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
    def serialize_datetime(self, dt: datetime | None, _info: Any) -> str | None:
        """将 naive UTC datetime 格式化为带 Z 后缀的 ISO 字符串。"""
        return format_utc(dt)


class TaskListOut(BaseModel):
    """任务分页列表。"""

    items: list[TaskOut]
    total: int
    page: int
    size: int


class TaskCancelOut(BaseModel):
    """取消任务响应。

    ``deleted``：是否物理删除了任务记录（True = pending 任务被删除；
    False = 运行中取消/标记，记录保留）。
    """

    message: str
    task_id: int
    deleted: bool = False


class TaskResultOut(BaseModel):
    """任务完整 result（``GET /api/tasks/{id}/result``）。

    详情接口默认只返回摘要（大结果不随轮询返回）；排障/展开完整结果时显式调用
    本端点。2026-11-06 数据库审查 P1-2。
    """

    id: int
    result: dict[str, Any] | None = None


class TaskRunOut(BaseModel):
    """一次任务执行尝试的台账（``GET /api/tasks/{id}/runs``）。

    字段与 ``task_run_logs`` 表一一对应；前端据此展示每次执行的耗时、GPU 与
    错误摘要（2026-11-02 日志扫描 P1-4：此前只有表、没有读接口）。
    """

    id: int
    task_id: int
    task_type: str
    attempt: int
    started_at: datetime
    finished_at: datetime | None = None
    item_count: int
    total_count: int
    status: str  # running/success/retry/failed
    gpu_model: str | None = None
    error: str | None = None

    model_config = {"from_attributes": True}

    @field_serializer("started_at", "finished_at")
    def serialize_datetime(self, dt: datetime | None, _info: Any) -> str | None:
        """将 naive UTC datetime 格式化为带 Z 后缀的 ISO 字符串。"""
        return format_utc(dt)
