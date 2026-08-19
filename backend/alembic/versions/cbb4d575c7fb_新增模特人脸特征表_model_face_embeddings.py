"""新增模特人脸特征表 model_face_embeddings

Revision ID: cbb4d575c7fb
Revises: 7a8b9c0d1e2f
Create Date: 2026-08-19 21:05:11.323115

注意：autogenerate 曾检测到 tags/ai_analysis_log/inspirations/scraper_tasks 等
历史库表与 ORM 的漂移，均与本功能无关，已从本迁移中剔除，仅保留
model_face_embeddings 建表（对应 ORM ModelFaceEmbedding）。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cbb4d575c7fb'
down_revision = '7a8b9c0d1e2f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：创建模特人脸特征表。"""
    op.create_table(
        'model_face_embeddings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('model_id', sa.Integer(), nullable=False),
        sa.Column('embedding', sa.LargeBinary(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['model_id'], ['models.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('model_face_embeddings', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_model_face_embeddings_model_id'),
            ['model_id'],
            unique=True,
        )


def downgrade() -> None:
    """执行降级迁移（回滚）。"""
    with op.batch_alter_table('model_face_embeddings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_model_face_embeddings_model_id'))

    op.drop_table('model_face_embeddings')
