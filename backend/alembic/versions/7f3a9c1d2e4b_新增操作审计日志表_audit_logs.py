"""新增操作审计日志表 audit_logs

Revision ID: 7f3a9c1d2e4b
Revises: e90015898fcd
Create Date: 2026-08-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7f3a9c1d2e4b'
down_revision = 'e90015898fcd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移。"""
    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('target_type', sa.String(length=32), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('freed_bytes', sa.Integer(), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_logs_action'), ['action'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_logs_created_at'), ['created_at'], unique=False)


def downgrade() -> None:
    """执行降级迁移（回滚）。"""
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_logs_created_at'))
        batch_op.drop_index(batch_op.f('ix_audit_logs_action'))

    op.drop_table('audit_logs')
