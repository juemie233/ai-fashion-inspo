"""删除博主简介来源字段（抖音签名简介能力下线）

Revision ID: b1c2d3e4f5a6
Revises: 9c3d4e5f6a7b
Create Date: 2026-11-13 22:05:00.000000

背景：``bio_source`` 只服务于「从抖音穿搭博主获取简介」——把 f2 用户库里的
主页签名回填到 ``bloggers.bio``，并区分 f2_signature / ai / manual。
该能力已按产品要求整体删除（2026-11-13），简介只保留用户手填与「AI 按标签
生成」两条来源，不再记录/展示来源标签，因此该列一并删除。

为什么用原生 ``DROP COLUMN`` 而不是 ``batch_alter_table``：
SQLite 3.35+ 支持原地删列；``bloggers`` 被 ``inspiration_bloggers`` 等表
外键引用，batch 重建表在开启 ``PRAGMA foreign_keys`` 的环境里可能触发级联，
原生删列不重建表、不影响关联数据。本项目 Python 3.12 自带的 SQLite 已满足版本要求。
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "9c3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除简介来源列；已有 bio 文本保留，仅丢失来源标记。"""
    op.execute("ALTER TABLE bloggers DROP COLUMN bio_source")


def downgrade() -> None:
    """回滚：加回可空的来源列（历史来源值无法恢复）。"""
    op.add_column(
        "bloggers",
        sa.Column("bio_source", sa.String(length=16), nullable=True),
    )
