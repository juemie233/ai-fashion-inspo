"""标签和多对多关联模型：多维标签体系的核心数据结构。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, func
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
    pinned: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True
    )  # 是否置顶（常用标签固定排前）
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0
    )  # 自定义排序权重（越小越靠前，仅在自定义排序模式生效）
    description: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # 标签说明文字
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # 关联关系
    inspirations: Mapped[list["InspirationTag"]] = relationship(
        "InspirationTag",
        back_populates="tag",
        cascade="all, delete-orphan",
    )
    aliases: Mapped[list["TagAlias"]] = relationship(
        "TagAlias",
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
    # 关联来源：manual 手动添加/种子关联；ai_generated 由 AI 分析产生。
    # 重新分析时仅清除 ai_generated 关联并替换为最新结果，手动/种子关联保留。
    # 注意：这是「关联」的来源，与 Tag.source（标签本身来源）语义不同。
    source: Mapped[str] = mapped_column(
        String(16), default="manual", server_default="manual"
    )

    # 关联关系
    inspiration: Mapped["Inspiration"] = relationship(
        "Inspiration", back_populates="tags"
    )
    tag: Mapped["Tag"] = relationship("Tag", back_populates="inspirations")

    __table_args__ = (
        UniqueConstraint("inspiration_id", "tag_id", name="uq_inspiration_tag"),
        # 按标签筛选（WHERE tag_id = X）需要 tag_id 单列索引；复合主键索引 (inspiration_id, tag_id) 无法高效服务此类查询
        Index("ix_inspiration_tags_tag_id", "tag_id"),
        # 重新分析时按 (素材, 来源) 删除旧 AI 关联
        Index("ix_inspiration_tags_insp_source", "inspiration_id", "source"),
    )


class TagAlias(Base):
    """标签别名：将同义名称（如「纯白」）归一化到主标签（如「白色」）。"""

    __tablename__ = "tag_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tag_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tags.id", ondelete="CASCADE"),
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # 关联关系
    tag: Mapped["Tag"] = relationship("Tag", back_populates="aliases")

    def __repr__(self) -> str:
        return f"<TagAlias(alias={self.alias}, tag_id={self.tag_id})>"
