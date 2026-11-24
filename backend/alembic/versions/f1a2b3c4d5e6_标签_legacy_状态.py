"""标签增加 legacy 状态：旧腿部并拢标签标记为待清理，不物理删除。

Revision ID: f1a2b3c4d5e6
Revises: e4f5a6b7c8d9
Create Date: 2026-11-24

背景：2026-11-24 腿部姿态审计确认 `并拢` 约 55% 是普通默认站姿的低价值标签。
本轮先标记 legacy（保留历史关联，不删除），新 Prompt 不再生成泛化 `并拢`；
后续抽样回填/清理时按 is_legacy 识别旧标签。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tags",
        sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_tags_is_legacy", "tags", ["is_legacy"], unique=False)
    op.get_bind().exec_driver_sql(
        "UPDATE tags SET is_legacy = 1 WHERE category = 'leg_posture' AND name = '并拢'"
    )


def downgrade() -> None:
    op.drop_index("ix_tags_is_legacy", table_name="tags")
    op.drop_column("tags", "is_legacy")
