"""灵感素材模型：核心实体，代表一条保存的穿搭图片/视频。"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    ColumnElement,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.utils.time import utcnow  # noqa: F401  # 供其他模型/服务统一导入，保持旧 import 路径可用


class Inspiration(Base):
    """穿搭灵感素材"""

    __tablename__ = "inspirations"

    __table_args__ = (
        # 部分唯一索引：仅在未删除素材（deleted_at IS NULL）之间保证平台 ID 唯一。
        # 垃圾桶素材释放该 ID，允许通过上传等非采集路径重新入库（采集路径由
        # URL 墓碑拦截，不参与重新采集）；同时充当查重查询的查找索引。
        Index(
            "ix_inspirations_source_platform_id",
            "source_platform_id",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    source_type: Mapped[str] = mapped_column(
        String(32), default="manual_upload", index=True
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_platform_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )  # 唯一性由 __table_args__ 中的部分唯一索引保证（垃圾桶素材不参与）
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

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )  # 软删除时间戳（非空表示在垃圾桶中，可恢复）
    trash_reason: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # 删除原因：质量差/重复/不喜欢/隐私/其他（负样本学习只用「质量差」子集）

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


# 未删除素材的统一过滤条件（软删除后所有正常查询都应排除垃圾桶素材）。
# 单一来源定义在此，服务/路由统一导入，避免各文件重复维护。
NOT_DELETED = Inspiration.deleted_at.is_(None)


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
    prompt_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )  # 分析所用 Prompt 的内容哈希（前 8 位），用于版本追溯
    model_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )  # 模型版本标识（当前为模型名，如 qwen3-vl:8b-instruct）
    processing_time_ms: Mapped[int | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 关联关系
    inspiration: Mapped["Inspiration"] = relationship(
        "Inspiration", back_populates="analysis_logs"
    )
    extracted_tags: Mapped[list["AIAnalysisTag"]] = relationship(
        "AIAnalysisTag",
        back_populates="log",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    quality_reviews: Mapped[list["AIQualityReview"]] = relationship(
        "AIQualityReview",
        back_populates="log",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<AIAnalysisLog(id={self.id}, model={self.model_name})>"


class AIAnalysisTag(Base):
    """AI 分析提取标签的结构化快照：每次分析「提取了什么」的版本记录。

    与 inspiration_tags（素材当前全量标签）不同，本表按日志记录单次分析的
    提取结果，支撑「不同模型/Prompt 版本的历史标签对比与追溯」。
    """

    __tablename__ = "ai_extracted_tags"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    log_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_analysis_log.id", ondelete="CASCADE"), index=True
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tags.id", ondelete="CASCADE"), index=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # 关联关系
    log: Mapped["AIAnalysisLog"] = relationship("AIAnalysisLog", back_populates="extracted_tags")
    tag: Mapped["Tag"] = relationship("Tag")


class AIQualityReview(Base):
    """质量审核的结构化结果：单次审核的判定与原因。

    与 Inspiration.quality_status（素材当前状态）不同，本表按日志记录每次
    审核的判定，支撑「质量审核与标签提取互不干扰、可独立管理」的追溯需求。
    """

    __tablename__ = "ai_quality_review"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    log_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_analysis_log.id", ondelete="CASCADE"), index=True
    )
    result: Mapped[str] = mapped_column(String(16))  # approved | rejected | pending
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # 关联关系
    log: Mapped["AIAnalysisLog"] = relationship("AIAnalysisLog", back_populates="quality_reviews")


def analysis_log_filter() -> ColumnElement[bool]:
    """返回「标签分析日志」的过滤条件（排除 quality_check 质量审核日志）。

    判断「是否已做标签分析」时应使用本条件：quality_check 只做二分类审核，
    不产出标签，不能算作「已分析」。历史日志（迁移前）的 log_type 为 NULL，
    统一按 analysis 处理。
    """
    return func.coalesce(AIAnalysisLog.log_type, "analysis") == "analysis"


def latest_analysis_log_subquery() -> Any:
    """返回「每个素材最新一条标签分析日志」的子查询（inspiration_id, max_id）。

    分析状态的判定（done/error）应基于**最新一条**日志，而非「任意一条」：
    旧失败日志不应覆盖后续成功（反之亦然）。调用方 join 本子查询并按
    ``AIAnalysisLog.id == max_id`` 取最新记录后判断 error。
    """
    return (
        select(
            AIAnalysisLog.inspiration_id,
            func.max(AIAnalysisLog.id).label("max_id"),
        )
        .where(analysis_log_filter())
        .group_by(AIAnalysisLog.inspiration_id)
        .subquery()
    )
