"""姿态提示词反推缓存（每个图库姿态一条，含双人姿态）。

背景：`scripts/data/backfill_pose_prompts.py` 用 thinking 模式逐张反推姿态线稿的
图生图提示词，并 upsert 到本表，供后续生成链路/分析复用；表本身不参与线上业务读写。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.time import utcnow


class PosePrompt(Base):
    """一条姿态线稿的反推提示词及调用成本。"""

    __tablename__ = "pose_prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 图库姿态 id（如 full_sexy_P037_diag / pair60_P026_front），唯一
    pose_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # 反推时的姿态元数据快照（图库目录可能变化，报告/分析仍能对上）
    name: Mapped[str] = mapped_column(String(128), default="")
    angle: Mapped[str] = mapped_column(String(32), default="")
    system: Mapped[str] = mapped_column(String(64), default="")
    category: Mapped[str] = mapped_column(String(128), default="")

    prompt: Mapped[str] = mapped_column(Text)
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)

    model: Mapped[str] = mapped_column(String(64), default="deepseek-flash[1m]")
    thinking_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_s: Mapped[float] = mapped_column(Float, default=0.0)
    image_bytes: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
