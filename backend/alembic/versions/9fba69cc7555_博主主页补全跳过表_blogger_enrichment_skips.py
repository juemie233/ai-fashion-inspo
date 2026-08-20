"""博主主页补全跳过表 blogger_enrichment_skips

Revision ID: 9fba69cc7555
Revises: 6c61e446a7b1
Create Date: 2026-08-20 20:31:02.724134
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9fba69cc7555'
down_revision = '6c61e446a7b1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：创建博主补全跳过表。"""
    op.create_table(
        'blogger_enrichment_skips',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('blogger_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['blogger_id'], ['bloggers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('blogger_enrichment_skips', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_blogger_enrichment_skips_blogger_id'),
            ['blogger_id'],
            unique=True,
        )


def downgrade() -> None:
    """执行降级迁移（回滚）。"""
    with op.batch_alter_table('blogger_enrichment_skips', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_blogger_enrichment_skips_blogger_id'))
    op.drop_table('blogger_enrichment_skips')
