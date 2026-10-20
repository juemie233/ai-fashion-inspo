"""对齐「空库建出来的结构」与 ORM：补 12 处历史迁移漏掉的 NOT NULL / 唯一索引

Revision ID: d1b2c3d4e5f6
Revises: c0a1b2c3d4e5

背景（2026-10-20 由新门禁 `scripts/check_fresh_migrations.py` 发现）：
本地与线上建库走的是 `create_all` + `stamp head`（见 app/database.py），迁移链平时
根本不执行。真正执行一次才发现两件事：

1. `c0a1b2c3d4e5`（对齐漂移）假定「从漂移老库升级」，在空库上会撞索引已存在 → 已改为
   幂等（先 inspect 再改）；
2. 更根本的：`a1b2c3d4e5f6`（persons 拆表）、`9d3f2a7b1c4e`（照片组）、`271fbfa8f56d`
   （人脸特征库）建表时**没有**带模型声明的 NOT NULL 与唯一索引，后续也没人补。于是
   「恢复服务器后按升级建库」得到的库比真实库松散：`bloggers.xhs_id` / `models.xhs_id`
   丢唯一约束（同一小红书号可重复入库）、`confidence` / `created_at` 等列可空
   （读出来是 None，前端与统计口径都要额外防御）。

本迁移把这些补齐。**写法必须幂等**：真实库（dev/线上）早已有这些约束，本迁移对它是
空操作；只有「按迁移链从空库建出来」的库才会真正执行 ALTER。判据全部来自
`sa.inspect`，不做假设。

回滚：不还原。这些约束是历史迁移的遗漏，还原等于把漂移重新引入；真要回滚请在对应
业务迁移里显式处理。
"""

import sqlalchemy as sa
from alembic import op

revision = "d1b2c3d4e5f6"
down_revision = "c0a1b2c3d4e5"
branch_labels = None
depends_on = None


def _columns(table: str) -> dict[str, dict]:
    """该表当前的列信息（含 nullable 与类型）。"""
    return {col["name"]: col for col in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> dict[str, dict]:
    """该表当前的索引信息（含 unique 与列顺序）。"""
    return {idx["name"]: idx for idx in sa.inspect(op.get_bind()).get_indexes(table)}


def _align_not_null(table: str, columns: tuple[str, ...]) -> None:
    """把指定列改成 NOT NULL——已经是 NOT NULL 的列跳过（空库/老库都安全）。"""
    current = _columns(table)
    todo = [name for name in columns if name in current and current[name]["nullable"]]
    if not todo:
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        for name in todo:
            batch_op.alter_column(name, existing_type=current[name]["type"], nullable=False)


def _align_unique_index(table: str, name: str, columns: tuple[str, ...]) -> None:
    """把指定索引建成唯一索引——已经存在且唯一则跳过。"""
    current = _indexes(table)
    idx = current.get(name)
    if idx is not None:
        same_columns = tuple(idx.get("column_names") or ()) == columns
        if idx.get("unique") and same_columns:
            return
    with op.batch_alter_table(table, schema=None) as batch_op:
        if idx is not None:
            batch_op.drop_index(name)
        batch_op.create_index(name, list(columns), unique=True)


def upgrade() -> None:
    """补齐历史迁移遗漏的约束（对已对齐的库是空操作）。"""
    _align_not_null("bloggers", ("created_at", "updated_at"))
    _align_not_null("models", ("created_at", "updated_at"))
    _align_not_null("model_photo_sets", ("created_at", "updated_at"))
    _align_not_null("model_photos", ("sort_order", "created_at"))
    # 同一张照片上「素材↔人」的匹配置信度：模型是必填（default=1.0 / 0.8）
    _align_not_null("inspiration_bloggers", ("confidence",))
    _align_not_null("inspiration_models", ("confidence",))

    # 唯一索引：同一小红书号两个人 = 数据完整性问题（模型声明 unique=True）
    _align_unique_index("bloggers", "ix_bloggers_xhs_id", ("xhs_id",))
    _align_unique_index("models", "ix_models_xhs_id", ("xhs_id",))


def downgrade() -> None:
    """不还原（见模块 docstring 末尾）。"""
    raise NotImplementedError(
        "本迁移不回滚：它补的是历史迁移遗漏的约束，回滚等于重新引入漂移"
    )
