"""向量回填攒批：新增待回填表，并清理历史 1/1 小任务

背景：旧逻辑每上传/裁剪/标签变更一个素材就创建一个 vector_backfill 任务
（total=1, done=1），任务列表被大量小任务淹没。本迁移：
1. 新建 pending_vector_backfills 待回填表（攒批机制的核心，持久化不丢失）
2. 清理历史遗留的 total<=1 向量回填小任务（删除非运行中的，运行中的标记取消）

Revision ID: 5f6a7b8c9d0e
Revises: 34d3c0c1b62f
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "5f6a7b8c9d0e"
down_revision = "34d3c0c1b62f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：建待回填表 + 清理历史小任务。"""
    # 向量回填攒批队列：inspiration_id 唯一索引（同素材只登记一次，幂等）
    op.create_table(
        "pending_vector_backfills",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inspiration_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pending_vector_backfills_inspiration_id",
        "pending_vector_backfills",
        ["inspiration_id"],
        unique=True,
    )

    # 数据清理：删除历史遗留的 total<=1 向量回填小任务（避免继续淹没任务列表与统计）。
    # 仅清理已终态（success/failed/cancelled）的：pending/running 的小任务保留——
    # running 由 worker 心跳租约机制负责（迁移运行时 worker 可能正在执行），
    # pending 保留执行无害（与 worker 启动兜底 purge 的边界约定一致）。
    op.execute(
        "DELETE FROM task_queue "
        "WHERE type='vector_backfill' AND total <= 1 "
        "AND status IN ('success', 'failed', 'cancelled')"
    )


def downgrade() -> None:
    """执行降级迁移（回滚）：删除待回填表（历史任务清理不撤销）。"""
    op.drop_index("ix_pending_vector_backfills_inspiration_id", table_name="pending_vector_backfills")
    op.drop_table("pending_vector_backfills")
