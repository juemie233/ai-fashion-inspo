"""采集话题标签存档表 scraper_hashtags

Revision ID: a220636b370a
Revises: 9fba69cc7555
Create Date: 2026-08-20 21:59:51.618171
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a220636b370a'
down_revision = '9fba69cc7555'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：创建采集话题标签存档表。"""
    op.create_table(
        'scraper_hashtags',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('seen_count', sa.Integer(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.Column('source_kind', sa.String(length=16), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=True),
        sa.Column('note_url', sa.Text(), nullable=True),
        sa.Column('source_meta', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('scraper_hashtags', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_scraper_hashtags_name'), ['name'], unique=True
        )
        batch_op.create_index(
            batch_op.f('ix_scraper_hashtags_source_kind'), ['source_kind'], unique=False
        )


def downgrade() -> None:
    """执行降级迁移（回滚）。"""
    with op.batch_alter_table('scraper_hashtags', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_scraper_hashtags_source_kind'))
        batch_op.drop_index(batch_op.f('ix_scraper_hashtags_name'))
    op.drop_table('scraper_hashtags')
