"""add inspirations.phash column (perceptual hash cache for near-duplicate scan)

Revision ID: 2f7b3d5a9c1e
Revises: 4c9bdd8a7fe1
Create Date: 2026-08-17 22:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2f7b3d5a9c1e'
down_revision = '4c9bdd8a7fe1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """给 inspirations 表新增 phash 列（近似重复检测的感知哈希缓存，懒计算）。

    注意：手工精简为仅添加本列，避免 autogenerate 夹带 ensure_schema 兜底
    造成的无关 schema 差异（与 trash_source 迁移同策略）。
    """
    with op.batch_alter_table('inspirations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('phash', sa.String(length=192), nullable=True))


def downgrade() -> None:
    """回滚：删除 phash 列。"""
    with op.batch_alter_table('inspirations', schema=None) as batch_op:
        batch_op.drop_column('phash')
