"""人物模型：穿搭博主（Blogger）与职业模特（Model）两张独立表。

原 ``persons`` 单表以 ``person_type`` 区分博主/模特，因两者后续业务逻辑
分叉（博主：平台主页/小红书号/按博主采集；模特：写真照片组），现拆为
``bloggers`` 与 ``models`` 两张独立表，素材关联同样拆为
``inspiration_bloggers`` / ``inspiration_models`` 两张关联表；
模特写真组独立为 ``model_photo_sets`` / ``model_photos``。
"""

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


class _PersonBaseFields:
    """博主/模特共用的字段定义（mixin 仅提供字段，不参与建表）。"""

    name: Mapped[str] = mapped_column(String(128), index=True)  # 人物名 / 博主昵称
    platform: Mapped[str] = mapped_column(
        String(32), default="other", index=True
    )  # 平台标识：xiaohongshu | douyin | other
    platform_user_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )  # 平台用户 ID（支撑"按博主采集"；手动录入的人物可为空）
    xhs_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )  # 小红书号（唯一索引，CSV 导入按此 upsert 防重复）
    ip_location: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # IP 属地（如「浙江」「江苏」）
    profile_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # 主页链接
    avatar_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # 头像文件路径
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)  # 简介
    source: Mapped[str] = mapped_column(
        String(16), default="manual", index=True
    )  # manual | ai_generated
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class Blogger(_PersonBaseFields, Base):
    """穿搭博主：穿搭图片中的主体人物，拥有平台主页/小红书号等元数据。

    与标签不同，博主是一级实体——支撑「按博主采集」「博主风格画像」
    「AI 识别人物」等增值能力。
    """

    __tablename__ = "bloggers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 关联关系
    inspirations: Mapped[list["InspirationBlogger"]] = relationship(
        "InspirationBlogger",
        back_populates="blogger",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Blogger(name={self.name}, platform={self.platform})>"


class Model(_PersonBaseFields, Base):
    """职业模特：写真主体人物，拥有独立的写真照片组（与穿搭素材分离）。"""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 关联关系
    inspirations: Mapped[list["InspirationModel"]] = relationship(
        "InspirationModel",
        back_populates="model",
        cascade="all, delete-orphan",
    )
    photo_sets: Mapped[list["ModelPhotoSet"]] = relationship(
        "ModelPhotoSet",
        back_populates="model",
        cascade="all, delete-orphan",
    )
    face_embedding: Mapped["ModelFaceEmbedding | None"] = relationship(
        "ModelFaceEmbedding",
        back_populates="model",
        uselist=False,
        cascade="all, delete-orphan",
    )
    face_detections: Mapped[list["InspirationFaceDetection"]] = relationship(
        "InspirationFaceDetection",
        back_populates="matched_model",
    )

    def __repr__(self) -> str:
        return f"<Model(name={self.name}, platform={self.platform})>"


class InspirationBlogger(Base):
    """素材-博主多对多关联表：带 AI 置信度分数。

    对标 inspiration_tags：一条素材可关联多位博主（转发/撞图场景），
    confidence 记录 AI 识别出「图里是谁」时的置信度。
    """

    __tablename__ = "inspiration_bloggers"

    inspiration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("inspirations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    blogger_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bloggers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    # 关联关系
    inspiration: Mapped["Inspiration"] = relationship(
        "Inspiration", back_populates="bloggers"
    )
    blogger: Mapped["Blogger"] = relationship("Blogger", back_populates="inspirations")

    __table_args__ = (
        UniqueConstraint("inspiration_id", "blogger_id", name="uq_inspiration_blogger"),
        # 按博主筛选（WHERE blogger_id = X）需要单列索引；
        # 复合主键索引 (inspiration_id, blogger_id) 无法高效服务此类查询
        Index("ix_inspiration_bloggers_blogger_id", "blogger_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<InspirationBlogger(inspiration_id={self.inspiration_id}, "
            f"blogger_id={self.blogger_id})>"
        )


class InspirationModel(Base):
    """素材-模特多对多关联表：带 AI 置信度分数。"""

    __tablename__ = "inspiration_models"

    inspiration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("inspirations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    model_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("models.id", ondelete="CASCADE"),
        primary_key=True,
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    # 关联关系
    inspiration: Mapped["Inspiration"] = relationship(
        "Inspiration", back_populates="models"
    )
    model: Mapped["Model"] = relationship("Model", back_populates="inspirations")

    __table_args__ = (
        UniqueConstraint("inspiration_id", "model_id", name="uq_inspiration_model"),
        Index("ix_inspiration_models_model_id", "model_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<InspirationModel(inspiration_id={self.inspiration_id}, "
            f"model_id={self.model_id})>"
        )


class ModelPhotoSet(Base):
    """模特照片组：一组属于某位模特的写真照片（对应一次导入的文件夹）。

    与穿搭素材（Inspiration）分离：模特写真不进入素材库、不参与 AI 打标与
    检索，仅按「模特 → 照片组 → 照片」浏览。
    """

    __tablename__ = "model_photo_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))  # 组名（默认取文件夹名）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    # 关联关系
    model: Mapped["Model"] = relationship("Model", back_populates="photo_sets")
    photos: Mapped[list["ModelPhoto"]] = relationship(
        "ModelPhoto",
        back_populates="photo_set",
        cascade="all, delete-orphan",
        order_by="ModelPhoto.sort_order",
    )

    def __repr__(self) -> str:
        return f"<ModelPhotoSet(id={self.id}, name={self.name})>"


class ModelPhoto(Base):
    """模特照片：照片组内的一张写真照片。"""

    __tablename__ = "model_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    set_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_photo_sets.id", ondelete="CASCADE"), index=True
    )
    file_path: Mapped[str] = mapped_column(Text)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )  # 文件内容 SHA-256（组内去重用）
    sort_order: Mapped[int] = mapped_column(Integer, default=0)  # 组内排序（按文件名）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # 关联关系
    photo_set: Mapped["ModelPhotoSet"] = relationship(
        "ModelPhotoSet", back_populates="photos"
    )

    def __repr__(self) -> str:
        return f"<ModelPhoto(id={self.id}, set_id={self.set_id})>"
