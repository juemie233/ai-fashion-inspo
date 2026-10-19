"""质量审核误判反馈表 quality_corrections

Revision ID: 007f6b562035
Revises: 16c2f1e31141

用途：记录「AI 判错 → 人工纠正」的闭环（原判定、纠正结果、错在哪类、备注）。
`quality_reviewed_by='human'` 只能说明「改过」，说不出「为什么错」，本表补齐这一层，
让 prompt 调优与初筛器重训有已知误判集可用。

注意：autogenerate 仍捎带既有漂移，已手工剔除，本次只建一张新表。
"""

import sqlalchemy as sa
from alembic import op

revision = "007f6b562035"
down_revision = "16c2f1e31141"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建误判反馈表。"""
    op.create_table(
        "quality_corrections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "inspiration_id",
            sa.String(length=36),
            sa.ForeignKey("inspirations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_status", sa.String(length=16), nullable=False),
        sa.Column("corrected_status", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index(
        "ix_quality_corrections_inspiration_id", "quality_corrections", ["inspiration_id"]
    )


def downgrade() -> None:
    """回滚：删除该表。"""
    op.drop_index("ix_quality_corrections_inspiration_id", table_name="quality_corrections")
    op.drop_table("quality_corrections")
