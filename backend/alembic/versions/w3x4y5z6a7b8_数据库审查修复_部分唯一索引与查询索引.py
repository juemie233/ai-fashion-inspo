"""数据库审查 2026-11-06 修复：平台 ID 部分唯一索引 + 查询索引

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-11-06

修复内容（docs/ops/数据库审查-2026-11-06.md §10）：
- P1-1：把历史库里「全量唯一」的 ix_inspirations_source_platform_id 重建为
  ORM/迁移要求的「仅未删除行唯一」，让垃圾桶释放平台 ID；
- P2-1/P2-3/P2-4：补默认列表/任务领取/标签反查/FK 前导索引，给
  ai_extracted_tags 加 (log_id, tag_id) 唯一索引；
- P2-2：删除被 (inspiration_id, face_index) 覆盖的冗余单列索引。

SQLite 的 alembic check 不比较部分索引的 WHERE 谓词，故本迁移之外
scripts/check_alembic_drift.py 还会做 sqlite_master 对账。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "w3x4y5z6a7b8"
down_revision = "v2w3x4y5z6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── P1-1：重建部分唯一索引 ──
    active_dups = bind.exec_driver_sql(
        """
        SELECT COUNT(*) FROM (
            SELECT source_platform_id FROM inspirations
            WHERE deleted_at IS NULL AND source_platform_id IS NOT NULL
            GROUP BY source_platform_id HAVING COUNT(*) > 1
        )
        """
    ).scalar()
    if active_dups:
        raise RuntimeError(
            "存在重复的未删除平台 ID，无法创建部分唯一索引；"
            f"请先人工处理 {active_dups} 组重复后再迁移"
        )
    op.execute("DROP INDEX IF EXISTS ix_inspirations_source_platform_id")
    op.create_index(
        "ix_inspirations_source_platform_id",
        "inspirations",
        ["source_platform_id"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )

    # ── P2-1：列表/筛选复合索引 ──
    op.create_index(
        "ix_inspirations_active_created", "inspirations", ["deleted_at", "created_at"]
    )
    op.create_index(
        "ix_inspirations_active_quality_created",
        "inspirations",
        ["deleted_at", "quality_status", "created_at"],
    )
    op.create_index(
        "ix_inspirations_active_source_created",
        "inspirations",
        ["deleted_at", "source_type", "created_at"],
    )
    op.create_index(
        "ix_task_queue_claim", "task_queue", ["status", "priority", "id"]
    )
    op.create_index("ix_task_queue_created_at", "task_queue", ["created_at"])
    op.create_index("ix_task_run_logs_task_started", "task_run_logs", ["task_id", "started_at"])
    op.create_index(
        "ix_inspiration_tags_tag_insp", "inspiration_tags", ["tag_id", "inspiration_id"]
    )

    # ── P2-2：删除被复合索引覆盖的冗余索引 ──
    op.execute("DROP INDEX IF EXISTS ix_inspiration_face_detections_inspiration_id")

    # ── P2-3：FK 前导索引 ──
    op.create_index("ix_collection_items_inspiration_id", "collection_items", ["inspiration_id"])
    op.create_index("ix_collections_cover_inspiration_id", "collections", ["cover_inspiration_id"])
    op.create_index("ix_person_groups_primary_blogger_id", "person_groups", ["primary_blogger_id"])

    # ── P2-4：ai_extracted_tags 唯一约束（先清历史重复，当前实测 0）──
    op.execute(
        """
        DELETE FROM ai_extracted_tags
        WHERE id NOT IN (
            SELECT MIN(id) FROM ai_extracted_tags GROUP BY log_id, tag_id
        )
        """
    )
    op.create_index(
        "uq_ai_extracted_tags_log_tag",
        "ai_extracted_tags",
        ["log_id", "tag_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_ai_extracted_tags_log_tag", table_name="ai_extracted_tags")
    op.drop_index("ix_person_groups_primary_blogger_id", table_name="person_groups")
    op.drop_index("ix_collections_cover_inspiration_id", table_name="collections")
    op.drop_index("ix_collection_items_inspiration_id", table_name="collection_items")
    op.create_index(
        "ix_inspiration_face_detections_inspiration_id",
        "inspiration_face_detections",
        ["inspiration_id"],
    )
    op.drop_index("ix_inspiration_tags_tag_insp", table_name="inspiration_tags")
    op.drop_index("ix_task_run_logs_task_started", table_name="task_run_logs")
    op.drop_index("ix_task_queue_created_at", table_name="task_queue")
    op.drop_index("ix_task_queue_claim", table_name="task_queue")
    op.drop_index("ix_inspirations_active_source_created", table_name="inspirations")
    op.drop_index("ix_inspirations_active_quality_created", table_name="inspirations")
    op.drop_index("ix_inspirations_active_created", table_name="inspirations")
    op.drop_index("ix_inspirations_source_platform_id", table_name="inspirations")
    op.create_index(
        "ix_inspirations_source_platform_id",
        "inspirations",
        ["source_platform_id"],
        unique=True,
    )
