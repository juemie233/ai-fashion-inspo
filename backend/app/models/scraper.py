"""采集任务模型：管理自动化内容采集作业。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScraperTask(Base):
    """采集任务：记录每次采集作业的参数和进度。"""

    __tablename__ = "scraper_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )
    config: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 字符串
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_added: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
