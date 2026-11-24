"""抖音收藏夹 → 穿搭大标签映射（2026-11-24）。

背景：抖音「获取我的收藏」把作品归位到两级收藏夹，但夹名只存在于合集结构里，
搜索/筛选/扩充画像都用不上。这里把每个收藏夹映射成一个 ``tags.category='outfit'``
的大标签，同步时给该夹素材自动打上。

为什么按 ``folder_name`` 而不是 ``collection_id`` 做主键：
- 首次「获取我的收藏」之前，本地还没有对应合集行，但用户要能**预先配置**映射；
- 收藏夹名是抖音侧稳定标识（改名的处理见服务层：旧名映射保留，新名成为待处理项）。

口径（用户已确认）：
- 未预先配置的收藏夹：首次同步先按同名建标签并打上，``needs_confirm=True`` 进待确认；
- 作品移出收藏夹后标签**保留**（快照语义），不做自动摘除。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.time import utcnow

# 映射模式
MODE_TAG = "tag"  # 已指定大标签
MODE_SKIP = "skip"  # 明确不打
MODE_PENDING = "pending"  # 待处理（未选标签 / 名称冲突）

MODES = (MODE_TAG, MODE_SKIP, MODE_PENDING)


class DouyinFolderTagMap(Base):
    """抖音收藏夹 → 穿搭大标签映射。"""

    __tablename__ = "douyin_folder_tag_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 抖音收藏夹名（同步前就存在，唯一）
    folder_name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    # 目标大标签；标签被删除时置空（SET NULL），映射保留为待处理
    tag_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("tags.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(16), default=MODE_PENDING)
    # 是否待用户确认：首次同步自动按同名建标签时置 True，用户在管理页确认后置 False
    needs_confirm: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        # 单列 unique 已由 folder_name 的 unique=True 建索引；这里补模式过滤索引
        Index("ix_douyin_folder_tag_map_mode", "mode"),
    )

    def __repr__(self) -> str:
        return (
            f"<DouyinFolderTagMap(folder={self.folder_name!r}, "
            f"mode={self.mode}, tag_id={self.tag_id})>"
        )
