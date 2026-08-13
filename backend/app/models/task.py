"""任务队列模型：持久化异步任务的状态、进度与结果。

由独立 worker 进程（app/worker.py）负责执行，API 只负责创建任务与查询状态。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.inspiration import utcnow


class TaskQueue(Base):
    """任务队列：记录异步任务的类型、状态、进度与结果。

    状态流转：
    - pending  排队中（可取消）
    - running  执行中
    - success  执行成功
    - failed   执行失败（超过最大重试次数或遇到永久错误）
    - cancelled 用户手动取消
    """

    __tablename__ = "task_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32), index=True)  # 任务类型：batch_analyze 等
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending/running/success/failed/cancelled
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0~100
    total: Mapped[int] = mapped_column(Integer, default=0)  # 批处理总数量
    done: Mapped[int] = mapped_column(Integer, default=0)  # 已完成数量
    result: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # JSON 结果（初始为输入载荷，完成后含统计）
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # 失败原因
    retry_count: Mapped[int] = mapped_column(Integer, default=0)  # 已重试次数
    max_retries: Mapped[int] = mapped_column(Integer, default=2)  # 最大重试次数
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )  # 下次可重试时间（指数退避）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return f"<TaskQueue(id={self.id}, type={self.type}, status={self.status})>"
