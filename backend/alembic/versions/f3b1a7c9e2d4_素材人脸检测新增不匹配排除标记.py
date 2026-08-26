"""素材人脸检测新增「不匹配」排除标记

Revision ID: f3b1a7c9e2d4
Revises: eec728f2e12a
Create Date: 2026-08-26 14:00:00.000000

背景：用户在扫描审核页对某张人脸点「驳回/不匹配」后，旧实现只清空匹配
字段，下次全库匹配会重新匹配它并再次产出候选，同一张被拒图反复出现。
新增 match_excluded 列（默认 False）：置 True 后该人脸不再参与全库匹配，
也不再显示在候选/未匹配区域（人工「不匹配」决定持久化）。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3b1a7c9e2d4'
down_revision = 'eec728f2e12a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：加 match_excluded 布尔列（默认 False）+ 索引。"""
    op.add_column(
        'inspiration_face_detections',
        sa.Column('match_excluded', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        'ix_inspiration_face_detections_match_excluded',
        'inspiration_face_detections',
        ['match_excluded'],
        unique=False,
    )


def downgrade() -> None:
    """执行降级迁移（回滚）：删列与索引。"""
    op.drop_index(
        'ix_inspiration_face_detections_match_excluded',
        table_name='inspiration_face_detections',
    )
    op.drop_column('inspiration_face_detections', 'match_excluded')
