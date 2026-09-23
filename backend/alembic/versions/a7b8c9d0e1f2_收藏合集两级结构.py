"""收藏合集两级结构：parent_id + auto_source + 同层唯一

背景：收藏来源只有「手动收藏」与「抖音收藏」两类，抖音侧本身还有收藏夹
（实测 31 个夹约 2000 件）。原实现把它们全量倒进一个扁平的「抖音收藏」合集，
既看不出收藏夹维度，也把「系统入库手动收藏」和「抖音入库自动收藏」混在一起。

本次改动：
1. ``collections`` 新增 ``parent_id``（自引用，父删子删）与 ``auto_source``
   （``douyin`` = f2 自动维护，禁止改名）；
2. 名字唯一性由「全局唯一」改为「同层唯一」——SQLite 的 UNIQUE 索引把 NULL
   视为互不相等，故一级（``parent_id IS NULL``）与二级各用一个**部分唯一索引**；
3. 建两个一级合集：**系统入库手动收藏**（智能合集 ``is_favorite``）与
   **抖音入库自动收藏**（二级挂它下面，按抖音收藏夹名自动建）；
4. 删掉旧的扁平「抖音收藏」自动合集（只删 f2 自动建的那个：描述前缀 + 手动
   合集 + 顶级；素材本身留在库里，重新入库后会按收藏夹归位）。

Revision ID: a7b8c9d0e1f2
Revises: 0bc72b4e2fa0
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7b8c9d0e1f2"
down_revision = "0bc72b4e2fa0"
branch_labels = None
depends_on = None

# 一级合集名（与 app.services.task_runners.f2_import 里的常量保持一致）
MANUAL_ROOT_NAME = "系统入库手动收藏"
DOUYIN_ROOT_NAME = "抖音入库自动收藏"
# 旧扁平自动合集的识别特征（只删它，用户自建的同名合集不动）
LEGACY_NAME = "抖音收藏"
LEGACY_DESC_PREFIX = "由「一键获取我的收藏」自动聚合"


def _now() -> str:
    """与 app.utils.time.utcnow() 同格式的 UTC naive 时间串。"""
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S.%f"
    )


def _has_column(table: str, column: str) -> bool:
    """列是否已存在（PRAGMA table_info）。"""
    rows = op.get_bind().execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def _has_index(name: str) -> bool:
    """索引是否已存在。"""
    row = op.get_bind().execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :n"),
        {"n": name},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    """加两级字段 / 换同层唯一索引 / 建两个一级合集 / 删旧扁平自动合集。

    ⚠ 本迁移按「可重入」写：SQLite 的 DDL 在 pysqlite 下**不会随事务回滚**，
    迁移中途失败会留下「列已加、版本号没动」的半成品。实测过一次——parent_id
    加完、auto_source 报错时，版本号仍停在旧值而列已经在了。因此每步先判存在再执行，
    重跑即从断点继续。

    ⚠ parent_id **不带**外键约束：SQLite 方言不支持 `ALTER TABLE ... ADD COLUMN`
    带 REFERENCES（alembic 直接抛 NotImplementedError）。级联删除由应用层显式完成
    （collection_service.delete_collection 先删子节点），模型里保留外键只为
    create_all 建新库时的一致性。
    """
    if not _has_column("collections", "parent_id"):
        op.add_column("collections", sa.Column("parent_id", sa.Integer(), nullable=True))
    if not _has_column("collections", "auto_source"):
        op.add_column(
            "collections", sa.Column("auto_source", sa.String(length=16), nullable=True)
        )

    # 名字索引原来是 UNIQUE：不清掉它，二级收藏夹与用户自建同名合集就建不出来
    if _has_index("ix_collections_name"):
        op.drop_index("ix_collections_name", table_name="collections")
    if not _has_index("ix_collections_name"):
        op.create_index("ix_collections_name", "collections", ["name"], unique=False)
    if not _has_index("ix_collections_parent_id"):
        op.create_index(
            "ix_collections_parent_id", "collections", ["parent_id"], unique=False
        )
    if not _has_index("uq_collections_root_name"):
        op.create_index(
            "uq_collections_root_name",
            "collections",
            ["name"],
            unique=True,
            sqlite_where=sa.text("parent_id IS NULL"),
        )
    if not _has_index("uq_collections_child_name"):
        op.create_index(
            "uq_collections_child_name",
            "collections",
            ["parent_id", "name"],
            unique=True,
            sqlite_where=sa.text("parent_id IS NOT NULL"),
        )

    conn = op.get_bind()
    now = _now()

    # 旧扁平自动合集：先删成员关系再删合集。不依赖 ON DELETE CASCADE——迁移连接
    # 默认没开 PRAGMA foreign_keys，级联不会触发，会留下孤儿 collection_items。
    conn.execute(
        sa.text(
            "DELETE FROM collection_items WHERE collection_id IN ("
            "  SELECT id FROM collections"
            "  WHERE name = :name AND parent_id IS NULL AND query_json IS NULL"
            "    AND description LIKE :prefix)"
        ),
        {"name": LEGACY_NAME, "prefix": f"{LEGACY_DESC_PREFIX}%"},
    )
    legacy = conn.execute(
        sa.text(
            "DELETE FROM collections"
            " WHERE name = :name AND parent_id IS NULL AND query_json IS NULL"
            "   AND description LIKE :prefix"
        ),
        {"name": LEGACY_NAME, "prefix": f"{LEGACY_DESC_PREFIX}%"},
    )
    if legacy.rowcount:
        print(f"[迁移] 已删除旧的扁平自动合集「{LEGACY_NAME}」（{legacy.rowcount} 个）")

    # 一级①：系统入库手动收藏 = 智能合集（素材卡片上点收藏的 is_favorite）
    conn.execute(
        sa.text(
            "INSERT INTO collections (name, description, position, query_json,"
            " created_at, updated_at, parent_id, auto_source)"
            " SELECT :name, :desc, :pos, :qj, :now, :now, NULL, NULL"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM collections WHERE name = :name AND parent_id IS NULL)"
        ),
        {
            "name": MANUAL_ROOT_NAME,
            "desc": "素材库里手动点过收藏的素材（按 is_favorite 动态求值，自动同步）",
            "pos": 0,
            "qj": '{"is_favorite": true}',
            "now": now,
        },
    )
    # 一级②：抖音入库自动收藏 = 父节点，二级按抖音收藏夹名由 f2 导入自动建立
    conn.execute(
        sa.text(
            "INSERT INTO collections (name, description, position, query_json,"
            " created_at, updated_at, parent_id, auto_source)"
            " SELECT :name, :desc, :pos, NULL, :now, :now, NULL, 'douyin'"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM collections WHERE name = :name AND parent_id IS NULL)"
        ),
        {
            "name": DOUYIN_ROOT_NAME,
            "desc": "由「获取我的收藏」自动聚合：二级收藏夹与抖音侧的收藏夹一一对应",
            "pos": 1,
            "now": now,
        },
    )


def downgrade() -> None:
    """退回扁平结构：删两个一级合集（连带二级）并还原名字索引。"""
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM collection_items WHERE collection_id IN ("
            "  SELECT id FROM collections"
            "  WHERE name IN (:manual, :douyin) OR parent_id IN ("
            "    SELECT id FROM collections WHERE name = :douyin))"
        ),
        {"manual": MANUAL_ROOT_NAME, "douyin": DOUYIN_ROOT_NAME},
    )
    conn.execute(
        sa.text(
            "DELETE FROM collections WHERE name IN (:manual, :douyin)"
            " OR parent_id IN (SELECT id FROM collections WHERE name = :douyin)"
        ),
        {"manual": MANUAL_ROOT_NAME, "douyin": DOUYIN_ROOT_NAME},
    )

    op.drop_index("uq_collections_child_name", table_name="collections")
    op.drop_index("uq_collections_root_name", table_name="collections")
    op.drop_index("ix_collections_parent_id", table_name="collections")
    op.drop_index("ix_collections_name", table_name="collections")
    op.create_index("ix_collections_name", "collections", ["name"], unique=True)

    # 旧库可能已经没有外键（见 upgrade 说明），batch 模式按当前表结构重建即可
    with op.batch_alter_table("collections") as batch:
        batch.drop_column("auto_source")
        batch.drop_column("parent_id")
