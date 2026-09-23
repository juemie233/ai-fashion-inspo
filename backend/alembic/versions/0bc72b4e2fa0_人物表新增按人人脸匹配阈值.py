"""人物表新增按人人脸匹配阈值

Revision ID: 0bc72b4e2fa0
Revises: m6n7o8p9q0r1
Create Date: 2026-09-23 20:12:49.354034

说明：本文件由 ``alembic revision --autogenerate`` 生成后**手工裁剪**。
autogenerate 同时带出大量与本次改动无关的历史 drift（``_alembic_tmp_ai_analysis_log``
残留临时表、ai_analysis_log/inspirations/tags 的 TEXT↔String、INTEGER↔Boolean
类型差异、若干索引增删，以及 ``drop_column('scraper_tasks.is_deleted')``）——
那些是真实库经 ``ensure_schema`` 兼容兜底维护后与 ORM 模型的既有偏差，与本次
功能无关，一并执行会有删列风险。此处只保留本次新增的两个字段。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0bc72b4e2fa0'
down_revision = 'm6n7o8p9q0r1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：博主/模特各加一个可空阈值列（NULL = 跟随全局配置）。"""
    with op.batch_alter_table('bloggers', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('face_match_threshold', sa.Float(), nullable=True)
        )

    with op.batch_alter_table('models', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('face_match_threshold', sa.Float(), nullable=True)
        )


def downgrade() -> None:
    """执行降级迁移（回滚）：删除两列。"""
    with op.batch_alter_table('models', schema=None) as batch_op:
        batch_op.drop_column('face_match_threshold')

    with op.batch_alter_table('bloggers', schema=None) as batch_op:
        batch_op.drop_column('face_match_threshold')
