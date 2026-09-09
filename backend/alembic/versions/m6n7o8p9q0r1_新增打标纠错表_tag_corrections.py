"""新增打标纠错表 tag_corrections

Revision ID: m6n7o8p9q0r1
Revises: k5l6m7n8o9p0
Create Date: 2026-09-08

背景：AI 打标质量闭环（P1）——素材详情页「标错了」显式反馈的落点，
用于提示词版本质量看板的纠错率统计与规则回归集的案例来源。

- inspiration_id：素材物理删除时级联清理（反馈随素材消失）；
- tag_id：标签物理删除时置空（记录保留，靠 tag_name 快照统计）；
- log_id：分析日志删除时置空（当次分析溯源用，非强依赖）；
- prompt_version / model_name：冗余自当次日志，避免聚合时 join。
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "m6n7o8p9q0r1"
down_revision = "k5l6m7n8o9p0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """建 tag_corrections 表与查询索引。"""
    op.create_table(
        "tag_corrections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "inspiration_id",
            sa.String(length=36),
            sa.ForeignKey("inspirations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tag_name", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False, server_default="free"),
        sa.Column("reason", sa.String(length=24), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "log_id",
            sa.Integer(),
            sa.ForeignKey("ai_analysis_log.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tag_corrections_inspiration_id", "tag_corrections", ["inspiration_id"])
    op.create_index("ix_tag_corrections_tag_id", "tag_corrections", ["tag_id"])
    op.create_index("ix_tag_corrections_reason", "tag_corrections", ["reason"])
    op.create_index("ix_tag_corrections_action", "tag_corrections", ["action"])
    op.create_index("ix_tag_corrections_log_id", "tag_corrections", ["log_id"])
    op.create_index("ix_tag_corrections_prompt_version", "tag_corrections", ["prompt_version"])
    op.create_index("ix_tag_corrections_created_at", "tag_corrections", ["created_at"])


def downgrade() -> None:
    """回滚：删除索引与表。"""
    op.drop_index("ix_tag_corrections_created_at", table_name="tag_corrections")
    op.drop_index("ix_tag_corrections_prompt_version", table_name="tag_corrections")
    op.drop_index("ix_tag_corrections_log_id", table_name="tag_corrections")
    op.drop_index("ix_tag_corrections_action", table_name="tag_corrections")
    op.drop_index("ix_tag_corrections_reason", table_name="tag_corrections")
    op.drop_index("ix_tag_corrections_tag_id", table_name="tag_corrections")
    op.drop_index("ix_tag_corrections_inspiration_id", table_name="tag_corrections")
    op.drop_table("tag_corrections")
