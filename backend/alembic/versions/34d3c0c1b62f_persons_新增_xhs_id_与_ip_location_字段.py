"""persons 新增 xhs_id 与 ip_location 字段

Revision ID: 34d3c0c1b62f
Revises: 2f7b3d5a9c1e
Create Date: 2026-08-18
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "34d3c0c1b62f"
down_revision = "2f7b3d5a9c1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增小红书号（唯一索引）与 IP 属地字段。"""
    op.add_column(
        "persons",
        sa.Column("xhs_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "persons",
        sa.Column("ip_location", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_persons_xhs_id", "persons", ["xhs_id"], unique=True)


def downgrade() -> None:
    """回滚：删除索引与字段。"""
    op.drop_index("ix_persons_xhs_id", table_name="persons")
    op.drop_column("persons", "ip_location")
    op.drop_column("persons", "xhs_id")
