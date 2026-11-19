"""收藏增强：inspirations.favorited_at + favorite_events 事件流水

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-11-19 18:50:00.000000

背景（2026-11-19 收藏增强 P0）：
- 旧版本只有布尔 ``is_favorite``，无法回答「什么时候收藏的」，收藏中心/列表的
  收藏时间排序无从实现；本次新增可空列 ``favorited_at``。
- 收藏/取消收藏的动作历史此前完全没有留痕；本次新增 ``favorite_events`` 流水表。

回填口径（**近似**）：旧数据没有真实收藏时间，用 ``updated_at`` 回填
（``updated_at`` 是「最后一次修改时间」，可能晚于真实收藏时刻；取消收藏的历史
无法重建，只回填当前仍是收藏状态的行）。同时为当前已收藏素材补一条
``source='legacy'`` 的 favorite 事件，时间取回填值，保证上线前的收藏在
「收藏动态」里也有起点。

可重入（迁移可能被中断后重跑）：
- 列 / 表 / 索引都先检查是否存在再创建；
- 回填只处理 ``favorited_at IS NULL`` 的行，不覆盖已有值；
- legacy 事件用 ``NOT EXISTS`` 去重，重跑不会重复插入。
"""

import sqlalchemy as sa
from alembic import op

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None

_FAVORITE_TIME_INDEX = "ix_inspirations_favorite_time"
_FAVORITE_EVENTS_INDEX = "ix_favorite_events_inspiration_time"


def _index_names(table: str) -> set[str]:
    """当前库该表的索引名集合（重入检查用）。"""
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    """新增收藏时间列、事件表与索引，并回填既有收藏。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1) inspirations.favorited_at（可空，历史行 NULL → 下方回填）
    columns = {col["name"] for col in inspector.get_columns("inspirations")}
    if "favorited_at" not in columns:
        op.add_column(
            "inspirations",
            sa.Column("favorited_at", sa.DateTime(), nullable=True),
        )

    # 2) favorite_events 事件流水表
    if "favorite_events" not in set(inspector.get_table_names()):
        op.create_table(
            "favorite_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "inspiration_id",
                sa.String(length=36),
                sa.ForeignKey("inspirations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("action", sa.String(length=10), nullable=False),
            sa.Column("source", sa.String(length=10), nullable=False, server_default="web"),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )

    # 3) 索引（表可能在上一步才创建，重新取 inspector；中断重跑也能补齐）
    if _FAVORITE_EVENTS_INDEX not in _index_names("favorite_events"):
        op.create_index(
            _FAVORITE_EVENTS_INDEX,
            "favorite_events",
            ["inspiration_id", "created_at"],
        )
    if _FAVORITE_TIME_INDEX not in _index_names("inspirations"):
        op.create_index(
            _FAVORITE_TIME_INDEX,
            "inspirations",
            ["is_favorite", "deleted_at", "favorited_at", "id"],
            sqlite_where=sa.text("is_favorite = 1"),
        )

    # 4) 回填收藏时间（只填空；updated_at 为近似值）
    bind.execute(
        sa.text(
            "UPDATE inspirations "
            "SET favorited_at = COALESCE(updated_at, created_at) "
            "WHERE is_favorite = 1 AND favorited_at IS NULL"
        )
    )

    # 5) 老收藏补 legacy 事件（已有事件的不重复插入）
    bind.execute(
        sa.text(
            "INSERT INTO favorite_events (inspiration_id, action, source, created_at) "
            "SELECT i.id, 'favorite', 'legacy', "
            "       COALESCE(i.favorited_at, i.updated_at, i.created_at) "
            "FROM inspirations i "
            "WHERE i.is_favorite = 1 "
            "  AND NOT EXISTS ("
            "      SELECT 1 FROM favorite_events e WHERE e.inspiration_id = i.id"
            "  )"
        )
    )


def downgrade() -> None:
    """回滚：删索引、事件表与收藏时间列（事件历史不可恢复）。"""
    inspector = sa.inspect(op.get_bind())

    if _FAVORITE_TIME_INDEX in _index_names("inspirations"):
        op.drop_index(_FAVORITE_TIME_INDEX, table_name="inspirations")

    if "favorite_events" in set(inspector.get_table_names()):
        if _FAVORITE_EVENTS_INDEX in _index_names("favorite_events"):
            op.drop_index(_FAVORITE_EVENTS_INDEX, table_name="favorite_events")
        op.drop_table("favorite_events")

    columns = {col["name"] for col in inspector.get_columns("inspirations")}
    if "favorited_at" in columns:
        op.drop_column("inspirations", "favorited_at")
