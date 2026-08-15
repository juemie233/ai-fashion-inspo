"""灵感素材模型：核心实体，代表一条保存的穿搭图片/视频。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Inspiration(Base):
    """穿搭灵感素材"""

    __tablename__ = "inspirations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    source_type: Mapped[str] = mapped_column(
        String(32), default="manual_upload", index=True
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_platform_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True
    )
    scraper_task_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("scraper_tasks.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )  # 关联采集任务，用于追溯来源

    file_path: Mapped[str] = mapped_column(Text)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )  # 文件内容 SHA-256（上传去重用，存量可回填）
    media_type: Mapped[str] = mapped_column(String(16), default="image")
    dominant_colors: Mapped[str | None] = mapped_column(
        String(128), nullable=True  # JSON 数组字符串
    )

    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)

    quality_status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # 质量审核状态：pending/approved/rejected
    quality_reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # 拒绝原因
    is_ai_generated: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True
    )  # 疑似 AI 生成标记（只标记不拒绝，与 quality_status 正交）

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    # 关联关系
    tags: Mapped[list["InspirationTag"]] = relationship(
        "InspirationTag",
        back_populates="inspiration",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    analysis_logs: Mapped[list["AIAnalysisLog"]] = relationship(
        "AIAnalysisLog",
        back_populates="inspiration",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    persons: Mapped[list["InspirationPerson"]] = relationship(
        "InspirationPerson",
        back_populates="inspiration",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Inspiration(id={self.id}, source={self.source_type})>"


class AIAnalysisLog(Base):
    """AI 分析日志：记录每次模型分析的过程和结果。"""

    __tablename__ = "ai_analysis_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    inspiration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inspirations.id", ondelete="CASCADE"), index=True
    )
    model_name: Mapped[str] = mapped_column(String(64))
    log_type: Mapped[str] = mapped_column(String(16), default="analysis")  # analysis | quality_check
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_time_ms: Mapped[int | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 关联关系
    inspiration: Mapped["Inspiration"] = relationship(
        "Inspiration", back_populates="analysis_logs"
    )


def analysis_log_filter():
    """返回「标签分析日志」的过滤条件（排除 quality_check 质量审核日志）。

    判断「是否已做标签分析」时应使用本条件：quality_check 只做二分类审核，
    不产出标签，不能算作「已分析」。历史日志（迁移前）的 log_type 为 NULL，
    统一按 analysis 处理。
    """
    return func.coalesce(AIAnalysisLog.log_type, "analysis") == "analysis"
