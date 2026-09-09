"""AI 打标纠错记录模型：显式「标错了」反馈的数据落点。

与 ``tag_history``（标签表自身的写操作快照，用于回滚）不同，本表记录的是
「素材-标签」层面的人工判断，是 AI 打标质量闭环的信号来源：

- ``action=removed``：AI 多标 → 反馈同时删除该素材上该标签的关联；
- ``action=added``：AI 漏标 → 反馈同时补建该标签关联；
- ``action=noted``：类别错 / 名称不规范 → 仅记录，不改动数据（后续人工治理）。

``tag_name`` / ``category`` 为写入时的快照：标签后续被重命名、合并或删除
（``tag_id`` 置空）后，历史统计仍可按名称口径追溯。``prompt_version`` /
``model_name`` 冗余自当次分析日志，便于按提示词版本聚合纠错率（避免每次
聚合都 join 日志表）。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.time import utcnow


class TagCorrection(Base):
    """单次 AI 打标纠错反馈。"""

    __tablename__ = "tag_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inspiration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("inspirations.id", ondelete="CASCADE"),
        index=True,
    )
    # 标签物理删除后置空（记录保留，靠 tag_name 快照统计）
    tag_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("tags.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tag_name: Mapped[str] = mapped_column(String(64))  # 名称快照
    category: Mapped[str] = mapped_column(String(32), default="free")  # 类别快照
    # 反馈原因：multi（AI 多标）/ missing（AI 漏标）/
    # wrong_category（类别错）/ bad_name（名称不规范）
    reason: Mapped[str] = mapped_column(String(24), index=True)
    # 实际执行的动作：removed / added / noted（仅记录）
    action: Mapped[str] = mapped_column(String(16), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 当次分析日志（素材无成功分析时为 null）；日志删除后置空
    log_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("ai_analysis_log.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True
    )

    def __repr__(self) -> str:
        return (
            f"<TagCorrection(id={self.id}, tag={self.tag_name!r}, "
            f"reason={self.reason}, action={self.action})>"
        )
