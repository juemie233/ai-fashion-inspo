"""操作审计日志模型：记录破坏性批量操作，便于事后追溯。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.inspiration import utcnow


class AuditLog(Base):
    """破坏性操作审计记录（批量删除/去重/清理孤立文件/清空垃圾桶等）。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(32), index=True)  # 操作类型（见 AUDIT_ACTIONS）
    target_type: Mapped[str] = mapped_column(String(32), default="inspirations")
    count: Mapped[int] = mapped_column(Integer, default=0)  # 影响数量（删除/处理的条目数）
    freed_bytes: Mapped[int] = mapped_column(Integer, default=0)  # 释放的磁盘字节数
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # 附加说明（任务 ID / 条件等）
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True
    )

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action={self.action})>"
