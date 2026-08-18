"""inspirations 新增 rating 用户评分字段

Revision ID: 6c8d9e0f1a2b
Revises: 5f6a7b8c9d0e
Create Date: 2026-08-18
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "6c8d9e0f1a2b"
down_revision = "5f6a7b8c9d0e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 rating 列（用户评分 0~5，默认 0）。"""
    op.add_column(
        "inspirations",
        sa.Column("rating", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """回滚：删除 rating 列。"""
    op.drop_column("inspirations", "rating")
