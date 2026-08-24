"""标签层级 parent_id 与操作历史表

Revision ID: 305b92d2197d
Revises: b41f0e8d3c72
Create Date: 2026-08-24 18:21:47.425950

说明：手写迁移（autogenerate 会夹带大量与本次无关的 schema 漂移变更），
仅包含 tags 表新增 updated_at / parent_id 与新建 tag_history 表。

背景：tags.source 列历史默认值存为 ``DEFAULT "seed"``（双引号在 SQLite 中
视为标识符），batch 模式重建表时反射渲染为 ``DEFAULT ("seed")`` 触发
「default value is not constant」错误。本迁移通过 copy_from 反射表并
修正为文本常量 ``DEFAULT 'seed'``（语义一致），顺带根治该历史遗留问题。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '305b92d2197d'
down_revision = 'b41f0e8d3c72'
branch_labels = None
depends_on = None


def _reflected_tags_table() -> sa.Table:
    """反射 tags 表，并修正 source 列默认值的渲染。"""
    meta = sa.MetaData()
    table = sa.Table("tags", meta, autoload_with=op.get_bind())
    table.c.source.server_default = sa.text("'seed'")
    return table


def upgrade() -> None:
    """执行升级迁移。"""
    # tags 表：新增 updated_at（最后修改时间，供操作历史回滚的冲突检测）
    # 与 parent_id（父标签 ID，层级结构，null 表示根节点，与 category 正交）
    with op.batch_alter_table(
        "tags", schema=None, copy_from=_reflected_tags_table()
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_tags_parent_id"), ["parent_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_tags_parent_id", "tags", ["parent_id"], ["id"], ondelete="SET NULL"
        )

    # 新建标签操作历史表（before/after 快照，支持审计与单条操作回滚）
    op.create_table(
        "tag_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("tag_ids", sa.Text(), nullable=False),
        sa.Column("before_snapshot", sa.Text(), nullable=False),
        sa.Column("after_snapshot", sa.Text(), nullable=False),
        sa.Column("meta", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tag_history", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_tag_history_batch_id"), ["batch_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_tag_history_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_tag_history_operation"), ["operation"], unique=False
        )


def downgrade() -> None:
    """执行降级迁移（回滚）。"""
    with op.batch_alter_table("tag_history", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tag_history_operation"))
        batch_op.drop_index(batch_op.f("ix_tag_history_created_at"))
        batch_op.drop_index(batch_op.f("ix_tag_history_batch_id"))
    op.drop_table("tag_history")

    with op.batch_alter_table(
        "tags", schema=None, copy_from=_reflected_tags_table()
    ) as batch_op:
        batch_op.drop_constraint("fk_tags_parent_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_tags_parent_id"))
        batch_op.drop_column("parent_id")
        batch_op.drop_column("updated_at")
