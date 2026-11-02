"""新增任务运行台账表 `task_run_logs`。

用于记录每次任务执行尝试的 start/end/张数/显卡型号，便于复盘耗时与算力成本；
与 `task_queue` 当前状态分开保存，任务被重试/清理不会丢掉历史。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 't9u0v1w2x3y4'
down_revision = 's8t9u0v1w2x3'
branch_labels = None
depends_on = None

_TABLE = "task_run_logs"


def upgrade() -> None:
    """建表（幂等）。"""
    bind = op.get_bind()
    if _TABLE in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("gpu_model", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_task_run_logs_task_id", _TABLE, ["task_id"])
    op.create_index("ix_task_run_logs_task_type", _TABLE, ["task_type"])
    op.create_index("ix_task_run_logs_attempt", _TABLE, ["attempt"])
    op.create_index("ix_task_run_logs_started_at", _TABLE, ["started_at"])
    op.create_index("ix_task_run_logs_finished_at", _TABLE, ["finished_at"])
    op.create_index("ix_task_run_logs_status", _TABLE, ["status"])


def downgrade() -> None:
    """回滚：删表。"""
    bind = op.get_bind()
    if _TABLE in sa.inspect(bind).get_table_names():
        op.drop_table(_TABLE)
