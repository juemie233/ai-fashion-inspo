"""add trash_source column

Revision ID: 4c9bdd8a7fe1
Revises: 9d3f2a7b1c4e
Create Date: 2026-08-17 12:02:10.684441
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4c9bdd8a7fe1'
down_revision = '9d3f2a7b1c4e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """给 inspirations 表新增 trash_source 列（移入来源：manual 手动 / auto 自动移动）。

    注意：autogenerate 会夹带 ensure_schema 兜底造成的无关 schema 差异，
    这里手工精简为仅添加本列，避免意外改动生产库其他表结构。
    """
    with op.batch_alter_table('inspirations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('trash_source', sa.String(length=16), nullable=True))


def downgrade() -> None:
    """回滚：删除 trash_source 列。"""
    with op.batch_alter_table('inspirations', schema=None) as batch_op:
        batch_op.drop_column('trash_source')
