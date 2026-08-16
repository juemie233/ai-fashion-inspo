"""服务心跳表与任务心跳租约字段

Revision ID: 51841a0c0163
Revises: 02b765c8c4e5
Create Date: 2026-08-16 17:11:06.757367
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision = '51841a0c0163'
down_revision = '02b765c8c4e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：新增服务心跳表 + 任务心跳租约字段。"""
    # 服务心跳表：长驻进程（worker / supervisor）定期写入存活心跳
    op.create_table('service_heartbeats',
    sa.Column('service_id', sa.String(length=64), nullable=False),
    sa.Column('service_type', sa.String(length=32), nullable=False),
    sa.Column('pid', sa.Integer(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('last_heartbeat_at', sa.DateTime(), nullable=True),
    sa.Column('extra', sqlite.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('service_id')
    )
    with op.batch_alter_table('service_heartbeats', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_service_heartbeats_last_heartbeat_at'), ['last_heartbeat_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_service_heartbeats_service_type'), ['service_type'], unique=False)

    # task_queue 心跳租约字段：记录认领任务的 worker 实例与其最后心跳时间
    with op.batch_alter_table('task_queue', schema=None) as batch_op:
        batch_op.add_column(sa.Column('claimed_by', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('heartbeat_at', sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f('ix_task_queue_heartbeat_at'), ['heartbeat_at'], unique=False)


def downgrade() -> None:
    """执行降级迁移（回滚）。"""
    with op.batch_alter_table('task_queue', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_task_queue_heartbeat_at'))
        batch_op.drop_column('heartbeat_at')
        batch_op.drop_column('claimed_by')

    with op.batch_alter_table('service_heartbeats', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_service_heartbeats_service_type'))
        batch_op.drop_index(batch_op.f('ix_service_heartbeats_last_heartbeat_at'))

    op.drop_table('service_heartbeats')
