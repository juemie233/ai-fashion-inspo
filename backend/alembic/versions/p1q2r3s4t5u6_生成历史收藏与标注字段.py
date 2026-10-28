"""生成历史 P1 · 后端第一步：三列迁移（收藏 / 命名 / 备注）。

设计依据：docs/本地生图历史记录增强方案.md §B1。

- ``favorite``：收藏的批次**不参与**过期清理（图不会被清掉），UI 显示收藏占用；
- ``title``：用户命名（空则前端按 prompt 前 40 字展示）；
- ``note``：自由备注。

刻意不加「收藏上限」之类的约束：磁盘占用用「显示 + 可手动删」兜底，
加硬上限反而会误伤（用户正想留的那批被挡住）。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'p1q2r3s4t5u6'
down_revision = 'n7o8p9q0r1s2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """给 generation_runs 补收藏与标注字段。"""
    with op.batch_alter_table("generation_runs") as batch:
        # 非空 + 默认 0：老数据自动是「未收藏」，不需要数据回填
        batch.add_column(
            sa.Column("favorite", sa.Boolean(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("title", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    """回滚：移除三个标注字段。"""
    with op.batch_alter_table("generation_runs") as batch:
        batch.drop_column("note")
        batch.drop_column("title")
        batch.drop_column("favorite")
