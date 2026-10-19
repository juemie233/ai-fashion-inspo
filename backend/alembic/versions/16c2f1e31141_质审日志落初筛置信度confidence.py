"""质量审核日志落初筛置信度 confidence

Revision ID: 16c2f1e31141
Revises: 26338478664f

用途：记录初筛器本次给出的「垃圾概率」，支撑「灰区优先人工复审」——
0.5 ~ 阈值的样本模型最没把握，人工看一眼收益最大（随机抽样的信息量远低于此）。

注意：autogenerate 仍捎带既有漂移，已手工剔除，仅动 ai_quality_review 一列。
"""

import sqlalchemy as sa
from alembic import op

revision = "16c2f1e31141"
down_revision = "26338478664f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 confidence 列（可空：未跑初筛器的审核为空）。"""
    with op.batch_alter_table("ai_quality_review", schema=None) as batch_op:
        batch_op.add_column(sa.Column("confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    """回滚：删除该列。"""
    with op.batch_alter_table("ai_quality_review", schema=None) as batch_op:
        batch_op.drop_column("confidence")
