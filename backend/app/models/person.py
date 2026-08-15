"""人物（模特/博主）模型：穿搭素材的主体人物实体。"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.inspiration import utcnow


class Person(Base):
    """人物：穿搭图片中的主体人物（职业模特 / 小红书博主等）。

    与标签不同，人物是一级实体——拥有平台主页、头像、平台用户 ID 等元数据，
    用于支撑「按博主采集」「博主风格画像」「AI 识别人物」等增值能力。
    """

    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), index=True)  # 人物名 / 博主昵称
    platform: Mapped[str] = mapped_column(
        String(32), default="other", index=True
    )  # 平台标识：xiaohongshu | douyin | other
    platform_user_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )  # 平台用户 ID（支撑"按博主采集"；手动录入的人物可为空）
    profile_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # 主页链接
    avatar_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # 头像文件路径
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)  # 简介
    source: Mapped[str] = mapped_column(
        String(16), default="manual", index=True
    )  # manual | ai_generated（对标 tags.source）
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    # 关联关系
    inspirations: Mapped[list["InspirationPerson"]] = relationship(
        "InspirationPerson",
        back_populates="person",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Person(name={self.name}, platform={self.platform})>"


class InspirationPerson(Base):
    """人物-素材多对多关联表：带 AI 置信度分数。

    对标 inspiration_tags：一条素材可关联多个人物（转发/撞图场景），
    confidence 记录 AI 识别出「图片里是谁」时的置信度。
    """

    __tablename__ = "inspiration_persons"

    inspiration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("inspirations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    person_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("persons.id", ondelete="CASCADE"),
        primary_key=True,
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    # 关联关系
    inspiration: Mapped["Inspiration"] = relationship(
        "Inspiration", back_populates="persons"
    )
    person: Mapped["Person"] = relationship("Person", back_populates="inspirations")

    __table_args__ = (
        UniqueConstraint("inspiration_id", "person_id", name="uq_inspiration_person"),
        # 按人物筛选（WHERE person_id = X）需要 person_id 单列索引；
        # 复合主键索引 (inspiration_id, person_id) 无法高效服务此类查询
        Index("ix_inspiration_persons_person_id", "person_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<InspirationPerson(inspiration_id={self.inspiration_id}, "
            f"person_id={self.person_id})>"
        )
