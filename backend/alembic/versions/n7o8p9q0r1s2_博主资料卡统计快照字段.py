"""博主资料卡：新增粉丝/作品/获赞等平台统计快照字段（人物详情页 P0-A1）

Revision ID: n7o8p9q0r1s2
Revises: c92d4f8329ac
Create Date: 2026-10-27 16:10:00.000000

设计依据：docs/person-detail-enhancements.md §A1。

背景：f2 用户库 ``douyin_users.db`` 的 ``user_info_web`` 里存着博主公开资料
（粉丝数/关注数/作品数/获赞总数/签名），离线可读、零网络零风控；而素材库此前
只回填了 ``ip_location`` 一项，简介覆盖率仅 4.4%。

字段口径：

- ``follower_count`` / ``following_count`` / ``aweme_count`` / ``total_favorited``：
  **快照**，不是实时值。抖音侧没有任何稳定免登录接口可实时刷新，所以必须配合
  ``stats_synced_at`` 一起展示（前端要写清「同步于 X 天前」）。
- ``bio_source``：简介来源（``f2_signature`` / ``ai`` / ``manual``），
  用于区分「抖音签名原文」与「AI 生成」——两者可信度不同，用户需要知道自己在看什么。
- ``stats_synced_at``：上面几个统计字段的采集时间。

刻意**不加** `city` / `gender`：详情页不使用（2026-10-27 需求确认），
少两列就少两处要维护的展示与同步口径。

注意：只给 ``bloggers`` 加列，``models`` 不动——这类平台统计是抖音博主专属，
模特只有 1 位且不在详情页展示统计。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'n7o8p9q0r1s2'
down_revision = 'c92d4f8329ac'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """给 bloggers 表补平台统计快照与简介来源字段。"""
    with op.batch_alter_table("bloggers") as batch:
        batch.add_column(sa.Column("follower_count", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("following_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("aweme_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("total_favorited", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("bio_source", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("stats_synced_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """回滚：移除统计快照字段。"""
    with op.batch_alter_table("bloggers") as batch:
        batch.drop_column("stats_synced_at")
        batch.drop_column("bio_source")
        batch.drop_column("total_favorited")
        batch.drop_column("aweme_count")
        batch.drop_column("following_count")
        batch.drop_column("follower_count")
