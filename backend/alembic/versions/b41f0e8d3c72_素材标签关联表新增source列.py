"""素材标签关联表新增 source 列

Revision ID: b41f0e8d3c72
Revises: a220636b370a
Create Date: 2026-08-21 10:00:00

inspiration_tags 新增 source 列，标记每条素材-标签关联的来源：
- manual：手动添加/种子关联（默认，保留）
- ai_generated：AI 分析产生（重新分析时先清除再写入最新结果）

存量关联默认置为 manual 保守保留；其中标签本身 source=ai_generated 的
关联回填为 ai_generated，使旧的重复 AI 标签能在下次重分析时被正确清理。
标签本身不删除；ai_extracted_tags 快照不受影响。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b41f0e8d3c72'
down_revision = 'a220636b370a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：新增 source 列与索引，回填 AI 关联。"""
    with op.batch_alter_table('inspiration_tags', schema=None) as batch_op:
        # 存量行由 server_default 自动填 'manual'
        batch_op.add_column(
            sa.Column(
                'source',
                sa.String(length=16),
                nullable=False,
                server_default='manual',
            )
        )
        batch_op.create_index(
            'ix_inspiration_tags_insp_source',
            ['inspiration_id', 'source'],
            unique=False,
        )

    # 回填：标签本身为 ai_generated 的关联标记为 AI 关联
    # （手动/种子标签关联保持默认 manual，重新分析时不被清除）
    op.execute(
        "UPDATE inspiration_tags SET source = 'ai_generated' "
        "WHERE tag_id IN (SELECT id FROM tags WHERE source = 'ai_generated')"
    )


def downgrade() -> None:
    """执行降级迁移（回滚）。"""
    with op.batch_alter_table('inspiration_tags', schema=None) as batch_op:
        batch_op.drop_index('ix_inspiration_tags_insp_source')
        batch_op.drop_column('source')
