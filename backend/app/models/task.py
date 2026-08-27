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
    claimed_by: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # 认领该任务的 worker 实例标识（心跳租约用）
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )  # 认领 worker 的最后心跳时间（超时视为 worker 已死）
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # 暂停时间（暂停/恢复机制用）
    last_stage: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # 暂停时的阶段名（用于恢复时续算）
    stage_state: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # 暂停时的中间状态（JSON，含社区标签/中心度等）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return f"<TaskQueue(id={self.id}, type={self.type}, status={self.status})>"


class PendingVectorBackfill(Base):
    """向量回填攒批队列：记录等待回填向量的素材 ID（持久化，重启不丢失）。

    触发策略（批量回填，替代「每素材一个任务」）：
    - 素材入库 / 裁剪 / 标签变更后不再立即创建任务，而是把素材 ID 登记到本表；
    - 累计达到 VECTOR_BACKFILL_BATCH_SIZE（100）时，由
      vector_backfill.flush_pending_vector_backfills 统一取出并创建 1 个批量任务；
    - 用户手动触发一键回填、或 worker 启动兜底时也会立即 flush，
      保证所有素材最终都能被回填、不丢失。
    """

    __tablename__ = "pending_vector_backfills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 待回填素材 ID（唯一约束 + 索引：同素材重复登记自动去重）
    inspiration_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<PendingVectorBackfill(id={self.id}, inspiration_id={self.inspiration_id})>"
