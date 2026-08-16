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
    diagnostics: Mapped[str | None] = mapped_column(Text, nullable=True)  # 漏斗日志 JSON
    resume_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # 断点续采进度 JSON
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class ScraperSeenURL(Base):
    """采集 URL 墓碑表：记录所有已下载过的图片 URL。

    素材被物理删除后，该 URL 仍保留在此表中，
    确保下次采集不会重复下载相同的图片。
    """

    __tablename__ = "scraper_seen_urls"

    source_url: Mapped[str] = mapped_column(
        Text, primary_key=True
    )  # 图片 URL 作为主键，天然去重
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class ScraperSchedule(Base):
    """定时采集计划：按固定间隔自动创建采集任务。"""

    __tablename__ = "scraper_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    keywords: Mapped[str] = mapped_column(Text)  # JSON 数组字符串
    max_count: Mapped[int] = mapped_column(Integer, default=20)
    sort_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 仅小红书生效
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=1440)  # 执行间隔（分钟）
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
