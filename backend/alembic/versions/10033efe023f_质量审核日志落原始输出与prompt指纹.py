"""质量审核日志落原始输出与 prompt 指纹

Revision ID: 10033efe023f
Revises: f02be41407dd
Create Date: 2026-10-19

用途：`ai_quality_review` 增加三列，支撑「事后复盘」与「prompt 调优」：
- `raw_output`：模型原始输出（JSON 文本），调 prompt 时能看到模型当时回了什么；
- `prompt_hash`：本次审核所用 prompt 的指纹，便于同 prompt 之间可比、跨 prompt 分组统计；
- `model_name`：判定使用的模型名（同一批数据可能跨模型）。

注意：本次 `--autogenerate` 仍检测到 tags / ai_analysis_log 等**既有 schema 漂移**，与本次需求
无关，已手工剔除；本次迁移只动 `ai_quality_review` 一表。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "10033efe023f"
down_revision = "f02be41407dd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增三列（均可空：历史审核没有这些信息）。"""
    with op.batch_alter_table("ai_quality_review", schema=None) as batch_op:
        batch_op.add_column(sa.Column("raw_output", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("prompt_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("model_name", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """回滚：删除三列。"""
    with op.batch_alter_table("ai_quality_review", schema=None) as batch_op:
        batch_op.drop_column("model_name")
        batch_op.drop_column("prompt_hash")
        batch_op.drop_column("raw_output")
