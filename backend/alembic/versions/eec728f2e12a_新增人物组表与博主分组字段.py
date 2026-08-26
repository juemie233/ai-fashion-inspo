"""新增人物组表与博主分组字段

Revision ID: eec728f2e12a
Revises: 305b92d2197d
Create Date: 2026-08-26 13:20:28.902244

方案 B（账号关联）：同一现实人物在多个平台（抖音/小红书）的账号集合。
- 新增 person_groups 表（组 id / 组名 / 创建时间）
- bloggers 增加 person_group_id 外键（nullable，空 = 独立账号）
组删除时博主回退为独立账号（ondelete=SET NULL），不级联删账号。

SQLite 不支持 ALTER TABLE ADD CONSTRAINT，故博主加列用 batch_alter_table
（copy-and-move 策略）一并建索引与外键。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'eec728f2e12a'
down_revision = '305b92d2197d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：建组表 + 博主加分组字段（batch 兼容 SQLite 外键）。"""
    op.create_table(
        'person_groups',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('primary_blogger_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['primary_blogger_id'], ['bloggers.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('bloggers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('person_group_id', sa.Integer(), nullable=True))
        batch_op.create_index(
            'ix_bloggers_person_group_id', ['person_group_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_bloggers_person_group_id',
            'person_groups',
            ['person_group_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    """执行降级迁移（回滚）：去掉分组字段 + 删组表。"""
    with op.batch_alter_table('bloggers', schema=None) as batch_op:
        batch_op.drop_constraint('fk_bloggers_person_group_id', type_='foreignkey')
        batch_op.drop_index('ix_bloggers_person_group_id')
        batch_op.drop_column('person_group_id')
    op.drop_table('person_groups')
