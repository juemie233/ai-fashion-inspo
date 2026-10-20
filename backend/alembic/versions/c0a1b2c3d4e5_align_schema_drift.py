"""对齐 ORM 与库的既有漂移（类型 / NOT NULL / 索引 / 遗留临时表）

Revision ID: c0a1b2c3d4e5
Revises: 007f6b562035

这些漂移不是某次功能改动引入的，而是历史上「模型先改、迁移没跟上」累积下来的：
- `_alembic_tmp_ai_analysis_log` 是某次 batch_alter_table 中断留下的空临时表（0 行）；
- `ai_analysis_log` / `inspirations` / `tags` 的列类型仍是 `TEXT`，模型已声明 `String(n)`；
- `ai_analysis_log.log_type`、`inspirations.quality_status/is_ai_generated`、
  `tags.source/pinned/sort_order` 模型是 NOT NULL，库里可空；
- 索引与模型不一致（多一个 `model_name`、少 `model_version`/`prompt_version`/`quality_status`/`source` 等）；
- `collections.parent_id` 缺外键（孤儿引用实测 0 条）。

应用前已做数据安全检查（只读）：所有拟置 NOT NULL 的列无 NULL、所有拟收窄的列无超长值、
临时表 0 行、孤儿引用 0 条。

**已知瑕疵**：`inspirations.scraper_task_id` 在 batch 重建后留下**两条外键**（旧 NO ACTION + 新 SET NULL）。
SQLite 无法 `DROP CONSTRAINT`，且 reflect 出来的旧约束无法命名（`drop_constraint` 拿不到名字），
去重需要手写整表重建。行为上不受影响（SQLite 按最后一条匹配外键生效 = `SET NULL`，与模型一致），
`alembic check` 亦为干净；若将来要彻底去重，应在一次专门的整表重建迁移里完成。
"""

import sqlalchemy as sa
from alembic import op

revision = "c0a1b2c3d4e5"
down_revision = "007f6b562035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """按「先表内、后跨表」的顺序对齐漂移。"""
    # 1) 遗留临时表（上次中断的 batch_alter_table 残留，0 行）
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_ai_analysis_log")

    # 2) ai_analysis_log：类型收窄 + log_type NOT NULL + 索引对齐
    with op.batch_alter_table("ai_analysis_log", schema=None) as batch_op:
        batch_op.alter_column(
            "log_type",
            existing_type=sa.TEXT(),
            type_=sa.String(length=16),
            nullable=False,
            existing_server_default=sa.text("'analysis'"),
        )
        batch_op.alter_column("prompt_version", existing_type=sa.TEXT(), type_=sa.String(length=64))
        batch_op.alter_column("model_version", existing_type=sa.TEXT(), type_=sa.String(length=64))
        batch_op.drop_index("ix_ai_analysis_log_model_name")
        batch_op.create_index("ix_ai_analysis_log_model_version", ["model_version"])
        batch_op.create_index("ix_ai_analysis_log_prompt_version", ["prompt_version"])

    # 3) collection_items：模型未声明的多余索引
    with op.batch_alter_table("collection_items", schema=None) as batch_op:
        batch_op.drop_index("ix_collection_items_inspiration_id")

    # 4) collections：补 parent_id 外键
    with op.batch_alter_table("collections", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_collections_parent_id", "collections", ["parent_id"], ["id"], ondelete="CASCADE"
        )

    # 5) inspirations：类型 / NOT NULL / 索引
    with op.batch_alter_table("inspirations", schema=None) as batch_op:
        batch_op.alter_column("content_hash", existing_type=sa.TEXT(), type_=sa.String(length=64))
        batch_op.alter_column(
            "quality_status",
            existing_type=sa.TEXT(),
            type_=sa.String(length=16),
            nullable=False,
        )
        batch_op.alter_column(
            "is_ai_generated",
            existing_type=sa.INTEGER(),
            type_=sa.Boolean(),
            nullable=False,
        )
        batch_op.alter_column("trash_reason", existing_type=sa.TEXT(), type_=sa.String(length=32))
        batch_op.create_index("ix_inspirations_quality_status", ["quality_status"])
        batch_op.create_index("ix_inspirations_scraper_task_id", ["scraper_task_id"])
        # 旧外键没有 ondelete 语义；SQLite 下 batch 会整表重建，正好按模型重建为 SET NULL
        batch_op.create_foreign_key(
            "fk_inspirations_scraper_task_id",
            "scraper_tasks",
            ["scraper_task_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # 6) quality_corrections：created_at 置 NOT NULL（表新建、实测 0 行）
    with op.batch_alter_table("quality_corrections", schema=None) as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(),
            nullable=False,
            existing_server_default=sa.text("CURRENT_TIMESTAMP"),
        )

    # 7) scraper_tasks：模型已删除 is_deleted
    with op.batch_alter_table("scraper_tasks", schema=None) as batch_op:
        batch_op.drop_column("is_deleted")

    # 8) tags：NOT NULL / 类型 / 索引
    with op.batch_alter_table("tags", schema=None) as batch_op:
        batch_op.alter_column("source", existing_type=sa.TEXT(), nullable=False)
        batch_op.alter_column(
            "pinned", existing_type=sa.INTEGER(), type_=sa.Boolean(), nullable=False
        )
        batch_op.alter_column("sort_order", existing_type=sa.INTEGER(), nullable=False)
        batch_op.alter_column("description", existing_type=sa.TEXT(), type_=sa.String(length=255))
        batch_op.create_index("ix_tags_source", ["source"])


def downgrade() -> None:
    """回滚：只恢复可逆的部分（类型/NOT NULL 恢复为原状），临时表与已删列不重建。"""
    with op.batch_alter_table("tags", schema=None) as batch_op:
        batch_op.drop_index("ix_tags_source")
        batch_op.alter_column("description", existing_type=sa.String(length=255), type_=sa.TEXT())
        batch_op.alter_column("sort_order", existing_type=sa.INTEGER(), nullable=True)
        batch_op.alter_column("pinned", existing_type=sa.Boolean(), type_=sa.INTEGER(), nullable=True)
        batch_op.alter_column("source", existing_type=sa.TEXT(), nullable=True)

    with op.batch_alter_table("inspirations", schema=None) as batch_op:
        batch_op.drop_index("ix_inspirations_scraper_task_id")
        batch_op.drop_index("ix_inspirations_quality_status")
        batch_op.alter_column("trash_reason", existing_type=sa.String(length=32), type_=sa.TEXT())
        batch_op.alter_column(
            "is_ai_generated", existing_type=sa.Boolean(), type_=sa.INTEGER(), nullable=True
        )
        batch_op.alter_column(
            "quality_status", existing_type=sa.String(length=16), type_=sa.TEXT(), nullable=True
        )
        batch_op.alter_column("content_hash", existing_type=sa.String(length=64), type_=sa.TEXT())

    with op.batch_alter_table("collections", schema=None) as batch_op:
        batch_op.drop_constraint("fk_collections_parent_id", type_="foreignkey")

    with op.batch_alter_table("collection_items", schema=None) as batch_op:
        batch_op.create_index("ix_collection_items_inspiration_id", ["inspiration_id"])

    with op.batch_alter_table("ai_analysis_log", schema=None) as batch_op:
        batch_op.drop_index("ix_ai_analysis_log_prompt_version")
        batch_op.drop_index("ix_ai_analysis_log_model_version")
        batch_op.create_index("ix_ai_analysis_log_model_name", ["model_name"])
        batch_op.alter_column("model_version", existing_type=sa.String(length=64), type_=sa.TEXT())
        batch_op.alter_column("prompt_version", existing_type=sa.String(length=64), type_=sa.TEXT())
        batch_op.alter_column(
            "log_type",
            existing_type=sa.String(length=16),
            type_=sa.TEXT(),
            nullable=True,
            existing_server_default=sa.text("'analysis'"),
        )
