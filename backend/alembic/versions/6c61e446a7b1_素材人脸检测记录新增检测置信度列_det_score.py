"""素材人脸检测记录新增检测置信度列 det_score

Revision ID: 6c61e446a7b1
Revises: 424d5c8e7dfe
Create Date: 2026-08-20 18:27:54.540567
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6c61e446a7b1'
down_revision = '424d5c8e7dfe'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：inspiration_face_detections 新增 det_score 列。"""
    with op.batch_alter_table('inspiration_face_detections', schema=None) as batch_op:
        batch_op.add_column(sa.Column('det_score', sa.Float(), nullable=True))


def downgrade() -> None:
    """执行降级迁移（回滚）。"""
    with op.batch_alter_table('inspiration_face_detections', schema=None) as batch_op:
        batch_op.drop_column('det_score')
