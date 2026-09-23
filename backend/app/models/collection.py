"""收藏合集模型：手动合集（实体成员）与智能合集（动态求值）两类同表区分。

约定（见 docs/收藏合集设计方案.md）：
- ``query_json IS NULL`` 为手动合集：成员关系落在 collection_items 表；
- ``query_json IS NOT NULL`` 为智能合集：成员由筛选条件动态求值，
  items 表不存它的行；
- 垃圾桶素材（deleted_at 非空）一律不作为合集内容返回（查询层排除），
  恢复后自动重现。

两级结构（``parent_id``）：
- 一级（``parent_id IS NULL``）只有两个：**系统入库手动收藏**（智能合集，
  ``is_favorite``）与**抖音入库自动收藏**（f2 自动维护的父节点）；
- 二级挂在「抖音入库自动收藏」之下，名字与抖音侧的收藏夹一一对应，由 f2
  入库阶段按「作品 ID → 收藏夹」清单自动建立（见
  ``scripts/f2_collects.py`` 写的 ``_collect_folders.json``）；
- 最多两级：不允许把二级当父节点继续套娃。
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.utils.time import utcnow

# ── 一级收藏夹的名字（唯一口径：服务、f2 导入、前端文案都从这里取）──
# 一级只有两类（用户口径）：系统入库手动收藏（智能合集，按 is_favorite 求值）
# 与抖音入库自动收藏（f2 自动维护的父节点，二级挂在它下面）。
MANUAL_ROOT_COLLECTION_NAME = "系统入库手动收藏"
MANUAL_ROOT_QUERY: dict = {"is_favorite": True}
DOUYIN_ROOT_COLLECTION_NAME = "抖音入库自动收藏"
"""自动维护来源标记：只有一个来源（douyin），但不能用 bool——将来可能还有别的来源。"""
DOUYIN_AUTO_SOURCE = "douyin"


class Collection(Base):
    """收藏合集：手动合集 / 智能合集两类共用一张表（支持父子两级）。"""

    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 名字唯一性按**同层**判定（见 __table_args__ 的两个部分唯一索引）：
    # 全局唯一会让「抖音收藏夹『穿搭』」和用户自建合集「穿搭」撞名。
    name: Mapped[str] = mapped_column(String(50), index=True)  # 1~50 字，同层重名 409
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

    # 父合集（二级才有值）；父被删时子合集一并删除（它们是父的子分类，单独留下没有意义）
    parent_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # 自动维护来源：NULL = 用户自建；"douyin" = f2「我的收藏」按抖音收藏夹自动建
    # （名字由抖音侧决定，故禁止改名；可删除，下次同步会按需重建）
    auto_source: Mapped[str | None] = mapped_column(String(16), nullable=True)

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
    children: Mapped[list["Collection"]] = relationship(
        "Collection",
        back_populates="parent",
        cascade="all, delete-orphan",
        # 不交给数据库级联：迁移新增的 parent_id 列在 SQLite 上**无法**带外键
        # （ALTER ADD COLUMN 不支持 REFERENCES），旧库因此没有 ON DELETE CASCADE。
        # 由 ORM 逐行删除子节点，行为不依赖连接是否开了 PRAGMA foreign_keys。
        lazy="select",
    )
    parent: Mapped["Collection | None"] = relationship(
        "Collection", back_populates="children", remote_side=[id]
    )

    # 同层唯一：SQLite 的 UNIQUE 索引里 NULL 互不相等，故一级（parent_id IS NULL）
    # 与二级必须各用一个**部分唯一索引**，否则两个顶级同名合集都能建出来。
    __table_args__ = (
        Index(
            "uq_collections_root_name",
            "name",
            unique=True,
            sqlite_where=text("parent_id IS NULL"),
        ),
        Index(
            "uq_collections_child_name",
            "parent_id",
            "name",
            unique=True,
            sqlite_where=text("parent_id IS NOT NULL"),
        ),
    )

    @property
    def kind(self) -> str:
        """合集类型：manual 手动合集 / smart 智能合集（由 query_json 区分）。"""
        return "smart" if self.query_json is not None else "manual"

    @property
    def is_auto(self) -> bool:
        """是否由同步流程自动维护（自动合集不允许改名）。"""
        return self.auto_source is not None

    def __repr__(self) -> str:
        return (
            f"<Collection(id={self.id}, name={self.name}, kind={self.kind}, "
            f"parent_id={self.parent_id}, auto_source={self.auto_source})>"
        )



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
