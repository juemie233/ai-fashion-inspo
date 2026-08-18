"""模特人脸特征库与素材人脸检测表

Revision ID: 271fbfa8f56d
Revises: a1b2c3d4e5f6
Create Date: 2026-08-18 23:09:19.093548

仅新增两张表（model_face_embeddings / inspiration_face_detections），
不包含 autogenerate 误检的历史 schema 漂移。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '271fbfa8f56d'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移。"""
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


def downgrade() -> None:
    """执行降级迁移（回滚）。"""
    op.drop_index(
        'ix_inspiration_face_detections_matched_model_id',
        table_name='inspiration_face_detections',
    )
    op.drop_index(
        'ix_inspiration_face_detections_inspiration_id',
        table_name='inspiration_face_detections',
    )
    op.drop_index('ix_face_detections_insp_face', table_name='inspiration_face_detections')
    op.drop_table('inspiration_face_detections')
    op.drop_index('ix_model_face_embeddings_model_id', table_name='model_face_embeddings')
    op.drop_table('model_face_embeddings')
