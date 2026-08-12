"""采集任务模型：管理自动化内容采集作业。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
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
