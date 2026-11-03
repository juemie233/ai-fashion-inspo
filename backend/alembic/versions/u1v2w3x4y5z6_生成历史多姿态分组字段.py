"""生成历史多姿态分组：generation_runs 增加 group_id

Revision ID: u1v2w3x4y5z6
Revises: t9u0v1w2x3y4
Create Date: 2026-11-03

背景：云端一次选择多个姿态时，每个姿态仍是独立批次/任务；前端给同一次提交的
多个批次写入同一个 group_id，历史列表与候选区按组展示。group_id 为 NULL 表示
单批次提交（本地出图或单姿态云端），行为不变。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "u1v2w3x4y5z6"
down_revision = "t9u0v1w2x3y4"
branch_labels = None
depends_on = None

_TABLE = "generation_runs"


def upgrade() -> None:
    """加列 + 索引（幂等：已有列/索引时跳过）。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if "group_id" not in columns:
        op.add_column(_TABLE, sa.Column("group_id", sa.String(length=32), nullable=True))
    indexes = {index["name"] for index in inspector.get_indexes(_TABLE)}
    if "ix_generation_runs_group_id" not in indexes:
        op.create_index("ix_generation_runs_group_id", _TABLE, ["group_id"])


def downgrade() -> None:
    """回滚：删索引与列。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes(_TABLE)}
    if "ix_generation_runs_group_id" in indexes:
        op.drop_index("ix_generation_runs_group_id", table_name=_TABLE)
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if "group_id" in columns:
        op.drop_column(_TABLE, "group_id")
