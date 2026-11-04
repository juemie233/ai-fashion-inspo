"""任务队列列表摘要列：task_queue 增加 result_summary

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-11-04

背景（2026-11-03 评审 P1-2/P2-6）：列表接口原先取 `substr(result, 1, 8192)`，
超长 JSON 被截断后 json.loads 必然失败，147 条历史任务的标题/摘要整条为空。
新增 result_summary 小 JSON 列：写入时由 ORM 事件生成，列表只读摘要列；
历史行由服务端启动时的 backfill 补齐（不在迁移里扫大 JSON，避免迁移过慢）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2w3x4y5z6a7"
down_revision = "u1v2w3x4y5z6"
branch_labels = None
depends_on = None

_TABLE = "task_queue"


def upgrade() -> None:
    """加摘要列（幂等）。"""
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns(_TABLE)}
    if "result_summary" not in columns:
        op.add_column(_TABLE, sa.Column("result_summary", sa.JSON(), nullable=True))


def downgrade() -> None:
    """回滚：删列。"""
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns(_TABLE)}
    if "result_summary" in columns:
        op.drop_column(_TABLE, "result_summary")
