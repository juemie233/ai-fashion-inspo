"""新增收藏合集表 collections 与 collection_items

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-08-29

背景：收藏合集（Lookbook / Board）功能，见 docs/收藏合集设计方案.md。
- collections：手动合集与智能合集同表区分（query_json IS NULL = 手动，
  IS NOT NULL = 智能动态求值）；name 唯一（重名 409）；封面素材物理删除
  时由外键 ON DELETE SET NULL 自动置空，回退到「加入最早」的成员。
- collection_items：素材 ↔ 合集多对多（仅手动合集有行），素材/合集任一
  物理删除时由外键级联清理，均不影响对端数据。
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "j4k5l6m7n8o9"
down_revision = "i3j4k5l6m7n8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """建 collections 与 collection_items 两表（含唯一约束与索引）。"""
    op.create_table(
        "collections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=50), nullable=False, unique=True, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "cover_inspiration_id",
            sa.String(length=36),
            sa.ForeignKey("inspirations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("query_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "collection_items",
        sa.Column(
            "collection_id",
            sa.Integer(),
            sa.ForeignKey("collections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "inspiration_id",
            sa.String(length=36),
            sa.ForeignKey("inspirations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("collection_id", "inspiration_id", name="uq_collection_inspiration"),
    )
    # 合集内按 position 编排展示顺序；按素材反查所属合集也需要 inspiration_id 索引
    # （复合主键索引以 collection_id 为前缀，无法高效服务仅按 inspiration_id 的查询）
    op.create_index(
        "ix_collection_items_inspiration_id", "collection_items", ["inspiration_id"]
    )


def downgrade() -> None:
    """回滚：删除两表（先子表后主表）。"""
    op.drop_index("ix_collection_items_inspiration_id", table_name="collection_items")
    op.drop_table("collection_items")
    op.drop_table("collections")
