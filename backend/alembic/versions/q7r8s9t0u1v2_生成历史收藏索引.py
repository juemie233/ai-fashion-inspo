"""生成历史 P1 补齐：`favorite` 的索引。

背景（2026-10-28 CI 红）：模型里 `favorite` 声明了 `index=True`，但上一版迁移
（`p1q2r3s4t5u6`）只加了列、没建索引。于是「空库按迁移链建出来的结构」与 ORM
不一致——CI 的迁移链自检和本地库漂移检查都会报：

    Detected added index ''ix_generation_runs_favorite'' on '('favorite',)'

为什么索引值得单独一条迁移而不是回改 `p1q2r3s4t5u6`：那条迁移已经发布并在真实库
执行过（`alembic_version` 记着它），回改历史迁移会让「已升级的库」与「从零建的库」
走到不同结构上，属于不可逆操作。按项目约定补一条对齐迁移。

收藏筛选（`GET /generation/runs?favorite=1`）与清理任务都要按这列过滤，索引有必要。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'q7r8s9t0u1v2'
down_revision = 'p1q2r3s4t5u6'
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_generation_runs_favorite"
_TABLE = "generation_runs"


def _index_names() -> set[str]:
    """当前库里该表已有索引名（让迁移可重复执行、也不回滚别人的索引）。"""
    inspector = sa.inspect(op.get_bind())
    return {ix["name"] for ix in inspector.get_indexes(_TABLE)}


def upgrade() -> None:
    """补 `ix_generation_runs_favorite`（幂等：已存在就跳过）。"""
    if _INDEX_NAME not in _index_names():
        op.create_index(_INDEX_NAME, _TABLE, ["favorite"])


def downgrade() -> None:
    """回滚：只删自己建的那条索引。"""
    if _INDEX_NAME in _index_names():
        op.drop_index(_INDEX_NAME, table_name=_TABLE)
