"""智能合集支持半智能扩充

Revision ID: e4f5a6b7c8d9
Revises: e3f4a5b6c7d8
Create Date: 2026-11-22 21:30:00.000000

背景（2026-11-22 智能合集扩充）：
- 智能合集内容由 ``query_json`` 动态求值，没有实体成员，无法「按此扩充」；
- 用户口径：转成**半智能合集**——保留规则匹配，同时允许往 ``collection_items``
  补充额外成员（扩充/手动加入），内容是「规则匹配 ∪ 实体成员」；
- 本次新增 ``collections.semi_manual``（默认 0），只对智能合集有意义。

回填：旧数据全部为 0（纯智能或纯手动），无需回填；手写迁移可重入。
"""

import sqlalchemy as sa
from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 collections.semi_manual 列（幂等）。"""
    columns = {
        col["name"] for col in sa.inspect(op.get_bind()).get_columns("collections")
    }
    if "semi_manual" not in columns:
        op.add_column(
            "collections",
            sa.Column(
                "semi_manual",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    """回滚：删除 semi_manual 列（半智能标记不可恢复）。"""
    columns = {
        col["name"] for col in sa.inspect(op.get_bind()).get_columns("collections")
    }
    if "semi_manual" in columns:
        op.drop_column("collections", "semi_manual")
