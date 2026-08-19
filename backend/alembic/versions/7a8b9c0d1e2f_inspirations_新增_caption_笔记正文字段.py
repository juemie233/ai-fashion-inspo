"""inspirations 新增 caption 笔记正文字段

Revision ID: 7a8b9c0d1e2f
Revises: 27602f89be55
Create Date: 2026-08-19
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "7a8b9c0d1e2f"
down_revision = "27602f89be55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 caption 列（笔记正文描述，详情页采集；手动上传可为空）。"""
    op.add_column(
        "inspirations",
        sa.Column("caption", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """回滚：删除 caption 列。"""
    op.drop_column("inspirations", "caption")
