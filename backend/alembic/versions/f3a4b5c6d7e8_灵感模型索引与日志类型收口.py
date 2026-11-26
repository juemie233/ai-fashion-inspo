"""灵感模型：日志类型回填/索引与收藏全局索引

Revision ID: f3a4b5c6d7e8
Revises: e5f6a7b8c9d1
Create Date: 2026-11-26 20:20:00.000000

背景（2026-11-26 模型审查）：
- ``analysis_log_filter`` 原先用 ``coalesce(log_type, 'analysis')``，无法命中
  索引；先把历史 NULL 回填为 analysis，再改等值条件；
- 新增 ``(log_type, inspiration_id)`` 覆盖「最新分析日志」子查询；
- 收藏中心最近动态按全局 created_at 倒序，补一条全局时间索引。
"""

import sqlalchemy as sa
from alembic import op

revision = "f3a4b5c6d7e8"
down_revision = "e5f6a7b8c9d1"
branch_labels = None
depends_on = None


def _index_names() -> set[str]:
    return {
        row["name"]
        for row in sa.inspect(op.get_bind())
        .get_indexes("ai_analysis_log")
    }


def upgrade() -> None:
    """回填 NULL 日志类型并补索引；不重建表，避免 SQLite 级联删子表。"""
    op.execute(
        "UPDATE ai_analysis_log SET log_type = 'analysis' WHERE log_type IS NULL"
    )
    if "ix_ai_log_type_insp" not in _index_names():
        op.create_index(
            "ix_ai_log_type_insp", "ai_analysis_log", ["log_type", "inspiration_id"]
        )
    favorite_indexes = {
        row["name"]
        for row in sa.inspect(op.get_bind()).get_indexes("favorite_events")
    }
    if "ix_favorite_events_created" not in favorite_indexes:
        op.create_index(
            "ix_favorite_events_created", "favorite_events", ["created_at"]
        )


def downgrade() -> None:
    """回退索引（不回填 NULL，避免丢失历史 NULL 语义）。"""
    indexes = _index_names()
    if "ix_ai_log_type_insp" in indexes:
        op.drop_index("ix_ai_log_type_insp", table_name="ai_analysis_log")
    favorite_indexes = {
        row["name"]
        for row in sa.inspect(op.get_bind()).get_indexes("favorite_events")
    }
    if "ix_favorite_events_created" in favorite_indexes:
        op.drop_index("ix_favorite_events_created", table_name="favorite_events")
