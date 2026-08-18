"""人脸特征库模型：模特注册特征 + 素材人脸检测结果。

对应需求「2.3 数据表扩展」：
- model_face_embeddings：一位模特一条平均池化后的 512 维特征（float32 BLOB）
- inspiration_face_detections：素材图内每张人脸一条记录（含特征与匹配结果，
  matched_model_id 为空表示未匹配到已知模特，即「疑似未知人脸」）
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.inspiration import utcnow


class ModelFaceEmbedding(Base):
    """模特人脸特征库：平均池化后的 512 维归一化特征（float32 BLOB，2048 字节）。"""

    __tablename__ = "model_face_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("models.id", ondelete="CASCADE"), unique=True, index=True
    )
    embedding: Mapped[bytes] = mapped_column(LargeBinary)  # 512 维 float32
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    # 关联关系
    model: Mapped["Model"] = relationship("Model", back_populates="face_embedding")

    def __repr__(self) -> str:
        return f"<ModelFaceEmbedding(model_id={self.model_id})>"


class InspirationFaceDetection(Base):
    """素材人脸检测：一张素材图内每张人脸一条记录。

    matched_model_id 非空表示匹配到已知模特；为空表示未匹配（疑似未知人脸），
    可由用户手动选择模特或解除。
    """

    __tablename__ = "inspiration_face_detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inspiration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inspirations.id", ondelete="CASCADE"), index=True
    )
    face_index: Mapped[int] = mapped_column(Integer)  # 图内人脸序号（0 起）
    embedding: Mapped[bytes] = mapped_column(LargeBinary)  # 512 维 float32
    matched_model_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("models.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 匹配余弦相似度
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # 关联关系
    inspiration: Mapped["Inspiration"] = relationship(
        "Inspiration", back_populates="face_detections"
    )
    matched_model: Mapped["Model | None"] = relationship(
        "Model", back_populates="face_detections"
    )

    __table_args__ = (
        Index("ix_face_detections_insp_face", "inspiration_id", "face_index"),
    )

    def __repr__(self) -> str:
        return (
            f"<InspirationFaceDetection(inspiration_id={self.inspiration_id}, "
            f"face_index={self.face_index}, matched_model_id={self.matched_model_id})>"
        )
