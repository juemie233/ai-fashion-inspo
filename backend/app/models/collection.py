"""收藏合集模型：手动合集（实体成员）与智能合集（动态求值）两类同表区分。

约定（见 docs/收藏合集设计方案.md）：
- ``query_json IS NULL`` 为手动合集：成员关系落在 collection_items 表；
- ``query_json IS NOT NULL`` 为智能合集：成员由筛选条件动态求值，
  items 表不存它的行；
- 垃圾桶素材（deleted_at 非空）一律不作为合集内容返回（查询层排除），
  恢复后自动重现。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.utils.time import utcnow


class Collection(Base):
    """收藏合集：手动合集 / 智能合集两类共用一张表。"""

    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # 1~50 字，重名 409
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_inspiration_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("inspirations.id", ondelete="SET NULL"),
        nullable=True,
    )  # 封面素材 ID（可空；未手动指定时取「加入最早」的一张，素材物理删除自动置空）
    position: Mapped[int] = mapped_column(Integer, default=0)  # 合集列表排序（越大越靠后）
    query_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # 智能合集筛选条件 JSON 字符串；手动合集恒为 NULL

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    # 关联关系
    items: Mapped[list["CollectionItem"]] = relationship(
        "CollectionItem",
        back_populates="collection",
        cascade="all, delete-orphan",
        # 成员关系仅手动合集使用，列表接口不展示，默认不预加载
        lazy="select",
    )

    @property
    def kind(self) -> str:
        """合集类型：manual 手动合集 / smart 智能合集（由 query_json 区分）。"""
        return "smart" if self.query_json is not None else "manual"

    def __repr__(self) -> str:
        return f"<Collection(id={self.id}, name={self.name}, kind={self.kind})>"


class CollectionItem(Base):
    """素材 ↔ 合集多对多关联：仅手动合集有行（按 position 编排展示顺序）。"""

    __tablename__ = "collection_items"

    collection_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("collections.id", ondelete="CASCADE"),
        primary_key=True,
    )
    inspiration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("inspirations.id", ondelete="CASCADE"),
        primary_key=True,
    )  # 素材物理删除时由外键级联自动出合集
    position: Mapped[int] = mapped_column(Integer, default=0)  # 合集内自定义顺序（拖拽编排）
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)  # 加入时间

    # 关联关系
    collection: Mapped["Collection"] = relationship(
        "Collection", back_populates="items"
    )
    inspiration: Mapped["Inspiration"] = relationship("Inspiration")

    __table_args__ = (
        UniqueConstraint("collection_id", "inspiration_id", name="uq_collection_inspiration"),
    )

    def __repr__(self) -> str:
        return (
            f"<CollectionItem(collection_id={self.collection_id}, "
            f"inspiration_id={self.inspiration_id}, position={self.position})>"
        )
