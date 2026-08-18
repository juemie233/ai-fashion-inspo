"""人物模块拆分：persons 单表 → bloggers / models 两表

拆分背景：
    原 ``persons`` 表以 ``person_type``（model/blogger）区分职业模特与穿搭博主，
    两者后续业务逻辑分叉（博主：平台主页/小红书号/按博主采集；模特：写真照片组），
    故物理拆分为两张独立表，素材关联表与模特照片组同步拆分：
      - persons           → bloggers（person_type='blogger'）/ models（person_type='model'）
      - inspiration_persons → inspiration_bloggers / inspiration_models（按原 person_type 分流）
      - person_photo_sets / person_photos → model_photo_sets / model_photos（写真组全归模特）

Revision ID: a1b2c3d4e5f6
Revises: 6c8d9e0f1a2b
Create Date: 2026-08-18
"""

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "6c8d9e0f1a2b"
branch_labels = None
depends_on = None


def _create_person_table(table_name: str) -> None:
    """建博主/模特主体表（字段一致，仅表名不同）。"""
    op.create_table(
        table_name,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False, server_default="other"),
        sa.Column("platform_user_id", sa.String(128), nullable=True),
        sa.Column("xhs_id", sa.String(64), nullable=True, unique=True),
        sa.Column("ip_location", sa.String(64), nullable=True),
        sa.Column("profile_url", sa.Text(), nullable=True),
        sa.Column("avatar_path", sa.Text(), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    for col in ("name", "platform", "platform_user_id", "xhs_id", "source", "created_at"):
        op.create_index(f"ix_{table_name}_{col}", table_name, [col])


def _person_table_exists(bind) -> bool:
    """6 张新表是否已全部存在。

    应用启动时 ``create_all`` 兜底可能先于本迁移建表（表已存在且为空），
    此时跳过建表/建索引，仅执行数据搬迁与旧表清理。
    """
    names = (
        "bloggers",
        "models",
        "inspiration_bloggers",
        "inspiration_models",
        "model_photo_sets",
        "model_photos",
    )
    return all(bind.dialect.has_table(bind, n) for n in names)


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. 建新表（若已被 create_all 兜底创建则跳过）──
    if not _person_table_exists(bind):
        _create_person_table("bloggers")
        _create_person_table("models")

        op.create_table(
            "inspiration_bloggers",
            sa.Column(
                "inspiration_id",
                sa.String(36),
                sa.ForeignKey("inspirations.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "blogger_id",
                sa.Integer(),
                sa.ForeignKey("bloggers.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("confidence", sa.Float(), nullable=True, server_default="1.0"),
            sa.UniqueConstraint("inspiration_id", "blogger_id", name="uq_inspiration_blogger"),
        )
        op.create_index(
            "ix_inspiration_bloggers_blogger_id", "inspiration_bloggers", ["blogger_id"]
        )

        op.create_table(
            "inspiration_models",
            sa.Column(
                "inspiration_id",
                sa.String(36),
                sa.ForeignKey("inspirations.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "model_id",
                sa.Integer(),
                sa.ForeignKey("models.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("confidence", sa.Float(), nullable=True, server_default="1.0"),
            sa.UniqueConstraint("inspiration_id", "model_id", name="uq_inspiration_model"),
        )
        op.create_index(
            "ix_inspiration_models_model_id", "inspiration_models", ["model_id"]
        )

        op.create_table(
            "model_photo_sets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "model_id",
                sa.Integer(),
                sa.ForeignKey("models.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_model_photo_sets_model_id", "model_photo_sets", ["model_id"])

        op.create_table(
            "model_photos",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "set_id",
                sa.Integer(),
                sa.ForeignKey("model_photo_sets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("file_path", sa.Text(), nullable=False),
            sa.Column("thumbnail_path", sa.Text(), nullable=True),
            sa.Column("content_hash", sa.String(64), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_model_photos_set_id", "model_photos", ["set_id"])
        op.create_index("ix_model_photos_content_hash", "model_photos", ["content_hash"])

    # ── 2. 数据搬迁（旧表可能不存在于新库，逐一容错）──
    if bind.dialect.has_table(bind, "persons"):
        # 博主：person_type='blogger'（含 person_type IS NULL 的历史数据按博主处理）
        op.execute(
            """
            INSERT INTO bloggers (id, name, platform, platform_user_id, xhs_id,
                ip_location, profile_url, avatar_path, bio, source, created_at, updated_at)
            SELECT id, name, platform, platform_user_id, xhs_id, ip_location,
                profile_url, avatar_path, bio, source, created_at, updated_at
            FROM persons
            WHERE person_type = 'blogger' OR person_type IS NULL
            """
        )
        # 模特：person_type='model'
        op.execute(
            """
            INSERT INTO models (id, name, platform, platform_user_id, xhs_id,
                ip_location, profile_url, avatar_path, bio, source, created_at, updated_at)
            SELECT id, name, platform, platform_user_id, xhs_id, ip_location,
                profile_url, avatar_path, bio, source, created_at, updated_at
            FROM persons
            WHERE person_type = 'model'
            """
        )

    if bind.dialect.has_table(bind, "inspiration_persons"):
        # 素材-博主关联：按人物原类型分流
        op.execute(
            """
            INSERT INTO inspiration_bloggers (inspiration_id, blogger_id, confidence)
            SELECT ip.inspiration_id, ip.person_id, ip.confidence
            FROM inspiration_persons ip
            JOIN persons p ON p.id = ip.person_id
            WHERE p.person_type = 'blogger' OR p.person_type IS NULL
            """
        )
        op.execute(
            """
            INSERT INTO inspiration_models (inspiration_id, model_id, confidence)
            SELECT ip.inspiration_id, ip.person_id, ip.confidence
            FROM inspiration_persons ip
            JOIN persons p ON p.id = ip.person_id
            WHERE p.person_type = 'model'
            """
        )

    if bind.dialect.has_table(bind, "person_photo_sets"):
        # 写真组全归模特（写真组是模特专属能力）
        op.execute(
            """
            INSERT INTO model_photo_sets (id, model_id, name, created_at, updated_at)
            SELECT id, person_id, name, created_at, updated_at
            FROM person_photo_sets
            """
        )
    if bind.dialect.has_table(bind, "person_photos"):
        op.execute(
            """
            INSERT INTO model_photos (id, set_id, file_path, thumbnail_path,
                content_hash, sort_order, created_at)
            SELECT id, set_id, file_path, thumbnail_path, content_hash,
                sort_order, created_at
            FROM person_photos
            """
        )

    # ── 3. 删除旧表（先子表后主表，避免 SQLite 外键悬挂）──
    for table in (
        "person_photos",
        "person_photo_sets",
        "inspiration_persons",
        "persons",
    ):
        if bind.dialect.has_table(bind, table):
            op.drop_table(table)


def downgrade() -> None:
    """回滚：新表数据合并回 persons 单表（幂等性有限，仅作结构回退）。"""
    bind = op.get_bind()

    op.create_table(
        "persons",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("person_type", sa.String(16), nullable=False, server_default="blogger"),
        sa.Column("platform", sa.String(32), nullable=False, server_default="other"),
        sa.Column("platform_user_id", sa.String(128), nullable=True),
        sa.Column("xhs_id", sa.String(64), nullable=True, unique=True),
        sa.Column("ip_location", sa.String(64), nullable=True),
        sa.Column("profile_url", sa.Text(), nullable=True),
        sa.Column("avatar_path", sa.Text(), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        """
        INSERT INTO persons (id, name, person_type, platform, platform_user_id, xhs_id,
            ip_location, profile_url, avatar_path, bio, source, created_at, updated_at)
        SELECT id, name, 'blogger', platform, platform_user_id, xhs_id,
            ip_location, profile_url, avatar_path, bio, source, created_at, updated_at
        FROM bloggers
        """
    )
    op.execute(
        """
        INSERT INTO persons (id, name, person_type, platform, platform_user_id, xhs_id,
            ip_location, profile_url, avatar_path, bio, source, created_at, updated_at)
        SELECT id, name, 'model', platform, platform_user_id, xhs_id,
            ip_location, profile_url, avatar_path, bio, source, created_at, updated_at
        FROM models
        """
    )

    op.create_table(
        "inspiration_persons",
        sa.Column(
            "inspiration_id",
            sa.String(36),
            sa.ForeignKey("inspirations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("confidence", sa.Float(), nullable=True, server_default="1.0"),
    )
    op.execute(
        "INSERT INTO inspiration_persons (inspiration_id, person_id, confidence) "
        "SELECT inspiration_id, blogger_id, confidence FROM inspiration_bloggers"
    )
    op.execute(
        "INSERT INTO inspiration_persons (inspiration_id, person_id, confidence) "
        "SELECT inspiration_id, model_id, confidence FROM inspiration_models"
    )

    op.create_table(
        "person_photo_sets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        "INSERT INTO person_photo_sets (id, person_id, name, created_at, updated_at) "
        "SELECT id, model_id, name, created_at, updated_at FROM model_photo_sets"
    )
    op.create_table(
        "person_photos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "set_id",
            sa.Integer(),
            sa.ForeignKey("person_photo_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("thumbnail_path", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        "INSERT INTO person_photos (id, set_id, file_path, thumbnail_path, "
        "content_hash, sort_order, created_at) "
        "SELECT id, set_id, file_path, thumbnail_path, content_hash, "
        "sort_order, created_at FROM model_photos"
    )

    for table in ("model_photos", "model_photo_sets", "inspiration_models", "inspiration_bloggers", "models", "bloggers"):
        if bind.dialect.has_table(bind, table):
            op.drop_table(table)
