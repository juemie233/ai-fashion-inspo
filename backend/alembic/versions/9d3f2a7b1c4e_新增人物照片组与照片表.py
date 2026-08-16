"""新增人物照片组与照片表

Revision ID: 9d3f2a7b1c4e
Revises: 51841a0c0163
Create Date: 2026-08-20 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9d3f2a7b1c4e'
down_revision = '51841a0c0163'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 person_photo_sets / person_photos 两张表（人物照片组功能）。

    人物照片组 = 一次从文件夹导入的一组模特写真，与穿搭素材（inspirations）分离。
    """
    op.create_table(
        'person_photo_sets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['person_id'], ['persons.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('person_photo_sets', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_person_photo_sets_person_id'), ['person_id'], unique=False
        )

    op.create_table(
        'person_photos',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('set_id', sa.Integer(), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('thumbnail_path', sa.Text(), nullable=True),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['set_id'], ['person_photo_sets.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('person_photos', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_person_photos_set_id'), ['set_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_person_photos_content_hash'), ['content_hash'], unique=False
        )


def downgrade() -> None:
    """回滚：删除两张表。"""
    with op.batch_alter_table('person_photos', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_person_photos_content_hash'))
        batch_op.drop_index(batch_op.f('ix_person_photos_set_id'))
    op.drop_table('person_photos')

    with op.batch_alter_table('person_photo_sets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_person_photo_sets_person_id'))
    op.drop_table('person_photo_sets')
