"""新增出图批次表与素材的批次关联（ComfyUI 出图接入 · P0）

Revision ID: c92d4f8329ac
Revises: d1b2c3d4e5f6
Create Date: 2026-10-21 08:53:48.390616

设计依据：docs/ComfyUI出图接入方案.md 第 4 节。

- `generation_runs`：一次出图批次的参数快照与状态机（queued/running/waiting_vram/
  done/failed/cancelled）。图片本体落 `storage/generated/<run_id>/`，DB 不存二进制。
- `inspirations.generation_run_id`：仅 `source_type='generated'` 有值，用于溯源与
  「同参再生成」。**ON DELETE SET NULL**：删除批次（清空生成历史）不得连带删掉
  已入库的素材——它们已经是独立素材。

注意：SQLite 不支持「给已有表追加外键」，Alembic 用 batch_alter_table 重建表来模拟；
外键必须**显式命名**，否则 downgrade 里的 drop_constraint 无从指定。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c92d4f8329ac'
down_revision = 'd1b2c3d4e5f6'
branch_labels = None
depends_on = None

FK_NAME = "fk_inspirations_generation_run_id"


def upgrade() -> None:
    """创建出图批次表，并给素材表加上批次外键。"""
    op.create_table(
        "generation_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("negative", sa.Text(), nullable=True),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("model_key", sa.String(length=32), nullable=False),
        sa.Column("workflow_name", sa.String(length=64), nullable=False),
        sa.Column("workflow_hash", sa.String(length=16), nullable=False),
        sa.Column("reference_ids", sa.Text(), nullable=True),
        sa.Column("batch_dir", sa.String(length=255), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generation_runs_status", "generation_runs", ["status"])
    op.create_index("ix_generation_runs_task_id", "generation_runs", ["task_id"])
    op.create_index("ix_generation_runs_created_at", "generation_runs", ["created_at"])

    with op.batch_alter_table("inspirations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("generation_run_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            "ix_inspirations_generation_run_id", ["generation_run_id"]
        )
        batch_op.create_foreign_key(
            FK_NAME, "generation_runs", ["generation_run_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    """回滚：移除批次外键与列，再删批次表。"""
    with op.batch_alter_table("inspirations", schema=None) as batch_op:
        batch_op.drop_constraint(FK_NAME, type_="foreignkey")
        batch_op.drop_index("ix_inspirations_generation_run_id")
        batch_op.drop_column("generation_run_id")

    op.drop_index("ix_generation_runs_created_at", table_name="generation_runs")
    op.drop_index("ix_generation_runs_task_id", table_name="generation_runs")
    op.drop_index("ix_generation_runs_status", table_name="generation_runs")
    op.drop_table("generation_runs")
