"""质量审核结构化拒绝原因 quality_reason_code

Revision ID: 26338478664f
Revises: 10033efe023f
Create Date: 2026-10-19

用途：把「拒绝原因」从自由文本升级为可筛选/可统计的枚举编码
（not_outfit / single_item / partial / low_quality / ad / other；初筛器拒绝记 prefilter_reject）。
自由文本 `quality_reason` 保留给人看，两列互补。

注意：本次 autogenerate 仍捎带既有 schema 漂移，已手工剔除，仅动 inspirations 一列。
"""

import sqlalchemy as sa
from alembic import op

revision = "26338478664f"
down_revision = "10033efe023f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增结构化原因列（可空；历史数据为 NULL）。"""
    with op.batch_alter_table("inspirations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quality_reason_code", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """回滚：删除该列。"""
    with op.batch_alter_table("inspirations", schema=None) as batch_op:
        batch_op.drop_column("quality_reason_code")
