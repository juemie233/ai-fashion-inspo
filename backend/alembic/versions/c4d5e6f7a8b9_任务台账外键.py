"""任务运行台账补 task_queue 外键

Revision ID: c4d5e6f7a8b9
Revises: f3a4b5c6d7e8
Create Date: 2026-11-26 21:40:00.000000

``task_run_logs.task_id`` 此前只是普通整数列。删除任务时 API 手动清理台账，
但 worker 启动清理/其它路径仍可能留下孤儿行。补数据库级 ON DELETE CASCADE，
并先清理历史孤儿（任务已不存在的台账）。
"""

import sqlalchemy as sa
from alembic import op

revision = "c4d5e6f7a8b9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def _foreign_keys() -> list[dict]:
    return sa.inspect(op.get_bind()).get_foreign_keys("task_run_logs")


def upgrade() -> None:
    """清理孤儿台账并补 FK。"""
    op.execute(
        "DELETE FROM task_run_logs "
        "WHERE task_id NOT IN (SELECT id FROM task_queue)"
    )
    if any(fk.get("referred_table") == "task_queue" for fk in _foreign_keys()):
        return
    with op.batch_alter_table("task_run_logs", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_task_run_logs_task_id",
            "task_queue",
            ["task_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    """删除 FK 后恢复普通整数列（SQLite batch 重建）。"""
    if not any(fk.get("referred_table") == "task_queue" for fk in _foreign_keys()):
        return
    with op.batch_alter_table("task_run_logs", schema=None) as batch_op:
        batch_op.drop_constraint("fk_task_run_logs_task_id", type_="foreignkey")
