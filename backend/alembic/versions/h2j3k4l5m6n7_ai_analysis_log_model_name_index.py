"""ai_analysis_log 新增 model_name 索引（多模型组合分析历史筛选提速）

Revision ID: h2j3k4l5m6n7
Revises: g8h9i0j1k2l3
Create Date: 2026-08-29 12:00:00.000000

背景：多模型 × 多提示词组合分析上线后，分析历史页新增「按模型筛选」与
组合幂等判定（按 model_name + prompt_version 查询已成功组合），
prompt_version 已有索引而 model_name 缺失，按模型筛选会全表扫描。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'h2j3k4l5m6n7'
down_revision = 'g8h9i0j1k2l3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级：为 ai_analysis_log.model_name 添加索引（已存在则跳过）。"""
    conn = op.get_bind()
    existing = conn.execute(
        sa.text(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='ai_analysis_log' AND name='ix_ai_analysis_log_model_name'"
        )
    ).fetchone()
    if not existing:
        op.create_index(
            'ix_ai_analysis_log_model_name',
            'ai_analysis_log',
            ['model_name'],
        )


def downgrade() -> None:
    """回滚：删除 model_name 索引。"""
    op.drop_index('ix_ai_analysis_log_model_name', table_name='ai_analysis_log')
