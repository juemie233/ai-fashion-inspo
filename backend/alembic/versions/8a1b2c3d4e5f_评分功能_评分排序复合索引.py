"""评分功能：inspirations 评分排序复合索引

Revision ID: 8a1b2c3d4e5f
Revises: w3x4y5z6a7b8
Create Date: 2026-11-12

评分方案 §4.4：评分排序必须有稳定次序并避免临时排序。
索引 (deleted_at, rating, created_at) 同时服务 deleted_at + rating 筛选/排序；
降序扫描复用同一索引，id tie-breaker 由 SQLite 行序稳定兜底。
"""

from __future__ import annotations

from alembic import op

revision = "8a1b2c3d4e5f"
down_revision = "w3x4y5z6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_inspirations_active_rating_created",
        "inspirations",
        ["deleted_at", "rating", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inspirations_active_rating_created", table_name="inspirations"
    )
