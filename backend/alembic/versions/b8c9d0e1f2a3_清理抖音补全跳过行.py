"""清理抖音博主的「补全跳过」行：那些原因可重试，不该把博主永久移出补全列表

背景（2026-09-27 排查「一朵芝士为什么不在一键补全列表里」）：
抖音侧的补全（离线读 f2 用户库回填 IP 属地）有三种「条件未具备」的情形——
缺 sec_user_id、f2 用户库里没有该账号、f2 库该账号没有属地。原实现把它们当成
「确定性无法补全」写入 ``blogger_enrichment_skips``，而补全列表会**排除**跳过表中
的博主。后果：跑过一次补全后所有缺口博主都被跳过、列表被清空
（实测：缺口抖音博主 439 个、跳过表 450 行、补全列表 0 个），
「一键补全」从此不出现在界面上，而这三个原因恰恰都是「再采一次/补个 ID 就能补」的。

代码侧已同时修改（``blogger_enrichment_service.backfill_douyin_from_f2``）：
抖音路径不再写跳过表，只把原因记在本轮结果明细里；跳过表从此只由小红书路径写入
（缺小红书号 / 不在关注列表——那才是确定性无法获取主页信息）。

本迁移清理历史遗留的抖音跳过行，让这些博主重新进入补全列表。幂等：只删
platform='douyin' 的博主对应的跳过行，重复执行无副作用。

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除抖音博主的补全跳过行（原因可重试，不该永久跳过）。"""
    conn = op.get_bind()
    affected = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM blogger_enrichment_skips WHERE blogger_id IN "
            "(SELECT id FROM bloggers WHERE platform = 'douyin')"
        )
    ).scalar()
    conn.execute(
        sa.text(
            "DELETE FROM blogger_enrichment_skips WHERE blogger_id IN "
            "(SELECT id FROM bloggers WHERE platform = 'douyin')"
        )
    )
    print(f"[migration] 已清理 {affected} 条抖音补全跳过行（原因可重试，不该永久跳过）")


def downgrade() -> None:
    """不可逆：被删的是「错误的跳过标记」，无从（也不应）恢复。

    若要重新跳过，跑一次补全任务即可——但小红书之外的路径已经不会再写跳过表。
    """
    pass
