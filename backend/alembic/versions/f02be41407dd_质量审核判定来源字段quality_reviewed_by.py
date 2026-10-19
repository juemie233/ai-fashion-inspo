"""质量审核判定来源字段 quality_reviewed_by

Revision ID: f02be41407dd
Revises: b8c9d0e1f2a3
Create Date: 2026-10-19 20:02:13.639710

用途：记录质量判定是谁做的（`ai` 模型审核 / `human` 人工翻案或手动设置 / NULL 历史数据）。
随机复审（force=True）必须跳过「已由人工判定」的素材，否则人工翻案会被下一次复审推翻。

注意：本次 `--autogenerate` 还检测到 tags / ai_analysis_log / collections 等**既有 schema 漂移**
（类型、NOT NULL、索引差异），与本次需求无关且改动面大，已手工剔除，只保留本字段，
避免误改其它表。那些漂移如要处理，应单独起一次迁移并配测试。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f02be41407dd"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增「质量判定来源」列（可空：历史数据保持 NULL，语义为「未知」）。"""
    with op.batch_alter_table("inspirations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quality_reviewed_by", sa.String(length=8), nullable=True))


def downgrade() -> None:
    """回滚：删除该列。"""
    with op.batch_alter_table("inspirations", schema=None) as batch_op:
        batch_op.drop_column("quality_reviewed_by")
