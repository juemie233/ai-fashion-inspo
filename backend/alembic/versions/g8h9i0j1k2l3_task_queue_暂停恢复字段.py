"""task_queue 新增暂停恢复三字段：paused_at / last_stage / stage_state

Revision ID: g8h9i0j1k2l3
Revises: f3b1a7c9e2d4
Create Date: 2026-08-27 10:00:00.000000

背景：标签网络分析任务执行时间长（社区发现/中心度计算），用户需要能暂停并
后续恢复（断点续算）。新增三个字段：
- paused_at: 暂停时间戳（NULL 表示未暂停）
- last_stage: 暂停时的阶段名（community_detection / betweenness_centrality）
- stage_state: JSON 中间状态（社区标签/采样进度/中心度部分结果）
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'g8h9i0j1k2l3'
down_revision = 'f3b1a7c9e2d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级：为 task_queue 表添加暂停恢复三字段。"""
    op.add_column(
        'task_queue',
        sa.Column('paused_at', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'task_queue',
        sa.Column('last_stage', sa.String(32), nullable=True),
    )
    op.add_column(
        'task_queue',
        sa.Column('stage_state', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """执行降级：移除三字段。"""
    op.drop_column('task_queue', 'stage_state')
    op.drop_column('task_queue', 'last_stage')
    op.drop_column('task_queue', 'paused_at')
