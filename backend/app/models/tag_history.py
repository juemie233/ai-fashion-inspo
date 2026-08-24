"""标签操作历史模型：记录标签变更的 before/after 快照，支持审计与单条操作回滚。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.inspiration import utcnow


class TagHistory(Base):
    """标签操作历史：每次标签写操作记录一条快照，供审计与回滚。

    快照字段（before_snapshot / after_snapshot）为 JSON 文本，记录受影响标签
    操作前后的完整状态（name / category / parent_id / description / pinned /
    sort_order / aliases / usage_count 等）；merge 类操作在 after 中额外记录
    源标签删除与关联转移详情，供回滚时恢复。
    """

    __tablename__ = "tag_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 同批次操作分组（批量编辑 / 聚类 apply / 一次批量移动），单条操作为 null
    batch_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # 操作类型：create / rename / category_change / move / merge /
    # alias_add / alias_remove / batch_edit / delete
    operation: Mapped[str] = mapped_column(String(32), index=True)
    # 受影响标签 ID 列表（JSON 数组文本）
    tag_ids: Mapped[str] = mapped_column(Text)
    before_snapshot: Mapped[str] = mapped_column(Text)  # 操作前快照（JSON）
    after_snapshot: Mapped[str] = mapped_column(Text)  # 操作后快照（JSON）
    meta: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # 附加信息（正则规则 / 源目标名等，JSON）
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True
    )

    def __repr__(self) -> str:
        return f"<TagHistory(id={self.id}, operation={self.operation})>"
