"""素材人脸检测记录保存检测框坐标（bbox）

Revision ID: 27602f89be55
Revises: e2d47f593be5
Create Date: 2026-08-19

背景：为支持从素材图中裁剪人脸小图（穿搭博主列表人脸头像），在
inspiration_face_detections 表新增 bbox 列，保存检测子服务返回的
原图坐标 [x1, y1, x2, y2]（JSON 字符串，可空）。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '27602f89be55'
down_revision = 'e2d47f593be5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移：新增 bbox 列（存量记录为空，检测过的素材可重新检测补全）。"""
    op.add_column(
        'inspiration_face_detections',
        sa.Column('bbox', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """执行降级迁移：删除 bbox 列。"""
    op.drop_column('inspiration_face_detections', 'bbox')
