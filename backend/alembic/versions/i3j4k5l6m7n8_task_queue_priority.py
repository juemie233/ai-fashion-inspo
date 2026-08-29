"""task_queue 新增 priority 优先级列（越大越先执行）

Revision ID: i3j4k5l6m7n8
Revises: h2j3k4l5m6n7
Create Date: 2026-08-29 12:00:00.000000

背景：task_queue 原先无优先级，worker 按 id ASC FIFO 认领——大批量分析会
阻塞后续更紧急的任务。新增 priority 整型列（默认 0），worker 认领改为
ORDER BY priority DESC, id ASC；批量删除等清理类任务设为 -5 低优先级，
避免清理阻塞分析链路。默认 0 同时通过 server_default 兜底历史行与裸插入。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'i3j4k5l6m7n8'
down_revision = 'h2j3k4l5m6n7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级：task_queue 加 priority 列（默认 0）+ 索引，已存在则跳过。"""
    conn = op.get_bind()
    columns = {
        row[1] for row in conn.execute(sa.text("PRAGMA table_info(task_queue)")).fetchall()
    }
    if "priority" not in columns:
        op.add_column(
            "task_queue",
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        )
    # 索引幂等创建（列已存在但索引缺失的场景也能补齐）
    existing_index = conn.execute(
        sa.text(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='task_queue' AND name='ix_task_queue_priority'"
        )
    ).fetchone()
    if not existing_index:
        op.create_index('ix_task_queue_priority', 'task_queue', ['priority'])


def downgrade() -> None:
    """回滚：删除 priority 索引与列。"""
    op.drop_index('ix_task_queue_priority', table_name='task_queue')
    with op.batch_alter_table("task_queue") as batch_op:
        batch_op.drop_column("priority")
