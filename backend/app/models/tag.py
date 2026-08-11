"""标签和多对多关联模型：多维标签体系的核心数据结构。"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Tag(Base):
    """标签：支持风格、颜色、单品类型等多个类别。"""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    category: Mapped[str] = mapped_column(
        String(32), default="free", index=True
    )
    source: Mapped[str] = mapped_column(
        String(16), default="seed", index=True
    )  # seed | ai_generated | manual
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # 关联关系
    inspirations: Mapped[list["InspirationTag"]] = relationship(
        "InspirationTag",
        back_populates="tag",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Tag(name={self.name}, category={self.category})>"


class InspirationTag(Base):
    """灵感-标签多对多关联表：带 AI 置信度分数。"""

    __tablename__ = "inspiration_tags"

    inspiration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("inspirations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    # 关联关系
    inspiration: Mapped["Inspiration"] = relationship(
        "Inspiration", back_populates="tags"
    )
    tag: Mapped["Tag"] = relationship("Tag", back_populates="inspirations")

    __table_args__ = (
        UniqueConstraint("inspiration_id", "tag_id", name="uq_inspiration_tag"),
    )
