"""收藏审查修复：inspirations 仅收藏部分索引

Revision ID: 9c3d4e5f6a7b
Revises: 8a1b2c3d4e5f
Create Date: 2026-11-12

P2：「仅收藏」筛选高频；部分索引只覆盖 is_favorite=1 的行，
查询 `WHERE deleted_at IS NULL AND is_favorite=1` 直接命中。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "9c3d4e5f6a7b"
down_revision = "8a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_inspirations_favorite_active",
        "inspirations",
        ["is_favorite", "deleted_at", "created_at"],
        sqlite_where=sa.text("is_favorite = 1"),
    )


def downgrade() -> None:
    op.drop_index("ix_inspirations_favorite_active", table_name="inspirations")
