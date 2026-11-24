"""抖音收藏夹 → 穿搭大标签映射

Revision ID: e5f6a7b8c9d1
Revises: f1a2b3c4d5e6
Create Date: 2026-11-24 12:00:00.000000

背景（2026-11-24）：
- 「获取我的收藏」按抖音收藏夹归位到本地合集，但夹名不能当标签用；
- 新增 ``douyin_folder_tag_map``：收藏夹名 → 穿搭大标签（``tags.category='outfit'``），
  按 ``folder_name`` 做主键，支持**首次获取前**预配置；
- ``needs_confirm`` 标记首次同步自动按同名建标签的行，管理页据此高亮待确认。

旧数据无需回填（新表从空开始）。
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d1"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

_TABLE = "douyin_folder_tag_map"
_MODE_INDEX = "ix_douyin_folder_tag_map_mode"


def upgrade() -> None:
    """创建映射表（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if _TABLE in set(inspector.get_table_names()):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("folder_name", sa.String(length=50), nullable=False),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "needs_confirm",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_douyin_folder_tag_map_folder_name",
        _TABLE,
        ["folder_name"],
        unique=True,
    )
    op.create_index("ix_douyin_folder_tag_map_tag_id", _TABLE, ["tag_id"])
    op.create_index(_MODE_INDEX, _TABLE, ["mode"])


def downgrade() -> None:
    """回滚：删表（映射配置不可恢复）。"""
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in set(inspector.get_table_names()):
        return
    indexes = {item["name"] for item in inspector.get_indexes(_TABLE)}
    for name in (
        _MODE_INDEX,
        "ix_douyin_folder_tag_map_tag_id",
        "ix_douyin_folder_tag_map_folder_name",
    ):
        if name in indexes:
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
