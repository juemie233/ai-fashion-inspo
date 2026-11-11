"""任务运行台账：每次执行（含重试）落一条，用于复盘耗时与算力成本。

与 `task_queue` 的分工：
- `task_queue` 是**当前状态**：排队/运行/完成，会被重试与清理覆盖；
- `task_run_logs` 是**历史台账**：谁在什么时间跑了多久、处理多少、用的哪张显卡。
  任务被删除/重试都不会丢掉已完成的记录；每条执行尝试独立一行（attempt 为
  worker 的 retry_count 快照），因此「中途崩溃后重跑」也能看清。

GPU 型号由 `nvidia-smi` 探测一次并缓存；探测不到就存 NULL（不猜）。
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.time import utcnow


class TaskRunLog(Base):
    """一次任务执行尝试的耗时台账。"""

    __tablename__ = "task_run_logs"

    __table_args__ = (
        # 2026-11-06 数据库审查 P2：任务运行记录按 (task_id, started_at) 查
        Index("ix_task_run_logs_task_started", "task_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    task_type: Mapped[str] = mapped_column(String(32), index=True)
    # worker 认领时的重试次数快照：0=首次执行，1=第一次重试，……
    attempt: Mapped[int] = mapped_column(Integer, default=0, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # 本次执行实际处理数量（完成时取 task.done；排队/中断时可能小于 total）
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    gpu_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
