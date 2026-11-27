"""inspirations 增加 file_size

Revision ID: e5f6a7b8c9d0
Revises: c4d5e6f7a8b9
Create Date: 2026-11-27 08:30:00.000000

``largest`` 排序此前每次全库 stat 文件；入库时记录字节数，历史行由
``backend/scripts/data/backfill_inspiration_file_size.py`` 回填。
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加 file_size 列（可空，避免强制回填阻塞迁移）。"""
    op.add_column(
        "inspirations",
        sa.Column("file_size", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """删除 file_size 列。"""
    with op.batch_alter_table("inspirations") as batch_op:
        batch_op.drop_column("file_size")
