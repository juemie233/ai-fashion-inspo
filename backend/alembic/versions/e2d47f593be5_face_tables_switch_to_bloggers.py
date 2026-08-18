"""人脸特征库切换至穿搭博主（blogger_face_embeddings）

Revision ID: e2d47f593be5
Revises: 271fbfa8f56d
Create Date: 2026-08-18 23:53:22.590460

背景：人脸识别能力原误绑定在职业模特上（model_face_embeddings /
inspiration_face_detections.matched_model_id），实际需求为穿搭博主
（素材人脸自动匹配博主特征库）。执行迁移前两张表均为空（无数据迁移需求），
直接重建为指向 bloggers 的表结构。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2d47f593be5'
down_revision = '271fbfa8f56d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：旧模特人脸表 → 博主特征库新结构。"""
    op.drop_index('ix_inspiration_face_detections_matched_model_id', table_name='inspiration_face_detections')
    op.drop_index('ix_inspiration_face_detections_inspiration_id', table_name='inspiration_face_detections')
    op.drop_index('ix_face_detections_insp_face', table_name='inspiration_face_detections')
    op.drop_table('inspiration_face_detections')
    op.drop_index('ix_model_face_embeddings_model_id', table_name='model_face_embeddings')
    op.drop_table('model_face_embeddings')

    op.create_table(
        'blogger_face_embeddings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('blogger_id', sa.Integer(), nullable=False),
        sa.Column('embedding', sa.LargeBinary(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['blogger_id'], ['bloggers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_blogger_face_embeddings_blogger_id',
        'blogger_face_embeddings',
        ['blogger_id'],
        unique=True,
    )

    op.create_table(
        'inspiration_face_detections',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('inspiration_id', sa.String(length=36), nullable=False),
        sa.Column('face_index', sa.Integer(), nullable=False),
        sa.Column('embedding', sa.LargeBinary(), nullable=False),
        sa.Column('matched_blogger_id', sa.Integer(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['inspiration_id'], ['inspirations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['matched_blogger_id'], ['bloggers.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_face_detections_insp_face',
        'inspiration_face_detections',
        ['inspiration_id', 'face_index'],
        unique=False,
    )
    op.create_index(
        'ix_inspiration_face_detections_inspiration_id',
        'inspiration_face_detections',
        ['inspiration_id'],
        unique=False,
    )
    op.create_index(
        'ix_inspiration_face_detections_matched_blogger_id',
        'inspiration_face_detections',
        ['matched_blogger_id'],
        unique=False,
    )


def downgrade() -> None:
    """执行降级迁移（回滚）：重建旧模特人脸表结构。"""
    op.drop_index('ix_inspiration_face_detections_matched_blogger_id', table_name='inspiration_face_detections')
    op.drop_index('ix_inspiration_face_detections_inspiration_id', table_name='inspiration_face_detections')
    op.drop_index('ix_face_detections_insp_face', table_name='inspiration_face_detections')
    op.drop_table('inspiration_face_detections')
    op.drop_index('ix_blogger_face_embeddings_blogger_id', table_name='blogger_face_embeddings')
    op.drop_table('blogger_face_embeddings')

    op.create_table(
        'model_face_embeddings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('model_id', sa.Integer(), nullable=False),
        sa.Column('embedding', sa.LargeBinary(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['model_id'], ['models.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_model_face_embeddings_model_id',
        'model_face_embeddings',
        ['model_id'],
        unique=True,
    )

    op.create_table(
        'inspiration_face_detections',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('inspiration_id', sa.String(length=36), nullable=False),
        sa.Column('face_index', sa.Integer(), nullable=False),
        sa.Column('embedding', sa.LargeBinary(), nullable=False),
        sa.Column('matched_model_id', sa.Integer(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['inspiration_id'], ['inspirations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['matched_model_id'], ['models.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_face_detections_insp_face',
        'inspiration_face_detections',
        ['inspiration_id', 'face_index'],
        unique=False,
    )
    op.create_index(
        'ix_inspiration_face_detections_inspiration_id',
        'inspiration_face_detections',
        ['inspiration_id'],
        unique=False,
    )
    op.create_index(
        'ix_inspiration_face_detections_matched_model_id',
        'inspiration_face_detections',
        ['matched_model_id'],
        unique=False,
    )
