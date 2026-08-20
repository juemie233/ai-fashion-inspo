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


class ScraperHashtag(Base):
    """采集话题标签存档：详情页正文提取的 #话题 全局去重存档。

    供「话题 → 定时采集关键词」闭环：发现博主常发话题 → 作为搜索关键词
    建定时采集任务。仅存档采集上下文（不进入素材标签体系）。

    - name 全局唯一（去 #、strip 后），重复出现累加 seen_count
    - source_kind：blogger（先落地）/ search（预留）
    - source_meta：JSON，最近若干条来源明细（保留 10 条，防膨胀）
    """

    __tablename__ = "scraper_hashtags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 话题词（去 #）
    seen_count: Mapped[int] = mapped_column(Integer, default=1)  # 累计出现次数
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), server_default=func.now()
    )
    source_kind: Mapped[str] = mapped_column(
        String(16), default="blogger", index=True
    )  # blogger | search（预留）
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 博主 id 或任务 id
    note_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # 最近来源笔记链接
    source_meta: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 来源明细
