"""人脸特征库模型：博主注册特征 + 素材人脸检测结果。

- blogger_face_embeddings：一位博主一条平均池化后的 512 维特征（float32 BLOB）
- inspiration_face_detections：素材图内每张人脸一条记录（含特征与匹配结果，
  matched_blogger_id 为空表示未匹配到已知博主，即「疑似未知人脸」）
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.inspiration import utcnow


class BloggerFaceEmbedding(Base):
    """博主人脸特征库：平均池化后的 512 维归一化特征（float32 BLOB，2048 字节）。"""

    __tablename__ = "blogger_face_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    blogger_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bloggers.id", ondelete="CASCADE"), unique=True, index=True
    )
    embedding: Mapped[bytes] = mapped_column(LargeBinary)  # 512 维 float32
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    # 关联关系
    blogger: Mapped["Blogger"] = relationship(
        "Blogger", back_populates="face_embedding"
    )

    def __repr__(self) -> str:
        return f"<BloggerFaceEmbedding(blogger_id={self.blogger_id})>"


class InspirationFaceDetection(Base):
    """素材人脸检测：一张素材图内每张人脸一条记录。

    matched_blogger_id 非空表示匹配到已知博主；为空表示未匹配（疑似未知人脸），
    可由用户手动选择博主或解除。
    """

    __tablename__ = "inspiration_face_detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inspiration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inspirations.id", ondelete="CASCADE"), index=True
    )
    face_index: Mapped[int] = mapped_column(Integer)  # 图内人脸序号（0 起）
    embedding: Mapped[bytes] = mapped_column(LargeBinary)  # 512 维 float32
    # 人脸检测框（原图坐标 [x1, y1, x2, y2] 的 JSON 字符串，可空）：
    # 用于从素材图中裁剪人脸小图（博主列表头像），检测子服务返回、入库保留
    bbox: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_blogger_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("bloggers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 匹配余弦相似度
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # 关联关系
    inspiration: Mapped["Inspiration"] = relationship(
        "Inspiration", back_populates="face_detections"
    )
    matched_blogger: Mapped["Blogger | None"] = relationship(
        "Blogger", back_populates="face_detections"
    )

    __table_args__ = (
        Index("ix_face_detections_insp_face", "inspiration_id", "face_index"),
    )

    def __repr__(self) -> str:
        return (
            f"<InspirationFaceDetection(inspiration_id={self.inspiration_id}, "
            f"face_index={self.face_index}, matched_blogger_id={self.matched_blogger_id})>"
        )
