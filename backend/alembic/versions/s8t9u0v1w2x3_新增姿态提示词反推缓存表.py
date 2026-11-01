"""新增姿态提示词反推缓存表 `pose_prompts`。

用途：`scripts/data/backfill_pose_prompts.py` 用 thinking 模式反推图库里全部姿态
（含双人姿态）的提示词并写入本表；表只做缓存/分析，不参与线上业务读写。

为什么单独建表：1,439 条姿态各有一条提示词 + thinking 原文 + token 成本，塞进
现成表会污染业务结构；放在独立表可按 pose_id 幂等 upsert、也可断点续跑。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 's8t9u0v1w2x3'
down_revision = 'q7r8s9t0u1v2'
branch_labels = None
depends_on = None

_TABLE = "pose_prompts"
_INDEX_NAME = "ix_pose_prompts_pose_id"


def upgrade() -> None:
    """建表（幂等：表已存在时只补索引）。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("pose_id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("angle", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("system", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("category", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("thinking", sa.Text(), nullable=True),
            sa.Column("model", sa.String(length=64), nullable=False, server_default="deepseek-flash[1m]"),
            sa.Column("thinking_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("elapsed_s", sa.Float(), nullable=False, server_default="0"),
            sa.Column("image_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    indexes = {ix["name"] for ix in inspector.get_indexes(_TABLE)} if _TABLE in inspector.get_table_names() else set()
    if _INDEX_NAME not in indexes:
        op.create_index(_INDEX_NAME, _TABLE, ["pose_id"], unique=True)


def downgrade() -> None:
    """回滚：删表（含唯一索引）。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in inspector.get_table_names():
        op.drop_table(_TABLE)
