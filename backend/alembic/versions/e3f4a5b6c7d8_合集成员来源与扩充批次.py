"""合集成员来源与扩充批次

Revision ID: e3f4a5b6c7d8
Revises: c2d3e4f5a6b7
Create Date: 2026-11-22 14:00:00.000000

背景（2026-11-22 合集智能扩充）：
- ``collection_items`` 之前只有 ``position / added_at``，分不清成员是手工加入、
  抖音同步写入还是「按此合集扩充」推荐的；扩充后也无法按批次回退。
- 本次新增 ``added_by``（manual / ai_suggest / sync）与 ``added_batch``（批次号）。

回填口径：``collections.auto_source IS NOT NULL`` 的合集成员一律标记为 ``sync``
（抖音同步写入的二级收藏夹/未分类收藏），其余保持默认 ``manual``。
可重入：列与索引先检查是否存在再创建，回填可重复执行。
"""

import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

_BATCH_INDEX = "ix_collection_items_added_batch"


def _index_names(table: str) -> set[str]:
    """当前库该表的索引名集合（重入检查用）。"""
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    """新增成员来源与扩充批次列，并把抖音同步成员标记为 sync。"""
    columns = {
        col["name"] for col in sa.inspect(op.get_bind()).get_columns("collection_items")
    }
    if "added_by" not in columns:
        op.add_column(
            "collection_items",
            sa.Column(
                "added_by",
                sa.String(length=16),
                nullable=False,
                server_default="manual",
            ),
        )
    if "added_batch" not in columns:
        op.add_column(
            "collection_items",
            sa.Column("added_batch", sa.String(length=32), nullable=True),
        )

    if _BATCH_INDEX not in _index_names("collection_items"):
        op.create_index(_BATCH_INDEX, "collection_items", ["added_batch"])

    # 老数据回填：抖音自动维护合集（一级/二级）的成员都是同步写入的
    op.get_bind().execute(
        sa.text(
            "UPDATE collection_items SET added_by = 'sync' "
            "WHERE collection_id IN ("
            "    SELECT id FROM collections WHERE auto_source IS NOT NULL"
            ") AND added_by = 'manual'"
        )
    )


def downgrade() -> None:
    """回滚：删索引与两列（来源/批次信息不可恢复）。"""
    if _BATCH_INDEX in _index_names("collection_items"):
        op.drop_index(_BATCH_INDEX, table_name="collection_items")

    columns = {
        col["name"] for col in sa.inspect(op.get_bind()).get_columns("collection_items")
    }
    if "added_batch" in columns:
        op.drop_column("collection_items", "added_batch")
    if "added_by" in columns:
        op.drop_column("collection_items", "added_by")
