"""平台ID部分唯一索引：垃圾桶素材释放平台 ID，允许删除后重新采集

Revision ID: 02b765c8c4e5
Revises: 7f3a9c1d2e4b
Create Date: 2026-08-16 15:16:31.234302
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '02b765c8c4e5'
down_revision = '7f3a9c1d2e4b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """唯一索引改为部分唯一索引：仅未删除素材之间保持平台 ID 唯一。

    原全局唯一索引会让垃圾桶素材永久占用 platform_id，导致
    「移入垃圾桶 → 重新采集同一笔记」恒 409/IntegrityError。
    改为 WHERE deleted_at IS NULL 的部分索引后，垃圾桶素材释放该 ID，
    与内容哈希去重的「垃圾桶素材可重新入库」语义对齐。
    """
    op.drop_index('ix_inspirations_source_platform_id', table_name='inspirations')
    op.create_index(
        'ix_inspirations_source_platform_id',
        'inspirations',
        ['source_platform_id'],
        unique=True,
        sqlite_where=sa.text('deleted_at IS NULL'),
    )


def downgrade() -> None:
    """回滚：恢复全局唯一索引。"""
    op.drop_index('ix_inspirations_source_platform_id', table_name='inspirations')
    op.create_index(
        'ix_inspirations_source_platform_id',
        'inspirations',
        ['source_platform_id'],
        unique=True,
    )
