"""素材人脸检测表扩展：模特匹配与审核状态列

Revision ID: 424d5c8e7dfe
Revises: cbb4d575c7fb
Create Date: 2026-08-19 21:08:45.456970

inspiration_face_detections 新增：
- matched_model_id：命中职业模特时写入（与 matched_blogger_id 互斥）
- match_status：批量扫描候选状态（NULL 传统结果 / pending AI 候选 /
  confirmed 已审核），带索引供候选列表分页查询
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '424d5c8e7dfe'
down_revision = 'cbb4d575c7fb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：新增两列与索引。"""
    with op.batch_alter_table('inspiration_face_detections', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('matched_model_id', sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('match_status', sa.String(length=16), nullable=True)
        )
        batch_op.create_index(
            batch_op.f('ix_inspiration_face_detections_matched_model_id'),
            ['matched_model_id'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_inspiration_face_detections_match_status'),
            ['match_status'],
            unique=False,
        )
        batch_op.create_foreign_key(
            'fk_inspiration_face_detections_matched_model_id_models',
            'models',
            ['matched_model_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    """执行降级迁移（回滚）。"""
    with op.batch_alter_table('inspiration_face_detections', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_inspiration_face_detections_matched_model_id_models',
            type_='foreignkey',
        )
        batch_op.drop_index(batch_op.f('ix_inspiration_face_detections_match_status'))
        batch_op.drop_index(batch_op.f('ix_inspiration_face_detections_matched_model_id'))
        batch_op.drop_column('match_status')
        batch_op.drop_column('matched_model_id')
