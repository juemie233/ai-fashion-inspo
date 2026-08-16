"""Alembic 迁移环境：加载 SQLAlchemy metadata 并配置同步 SQLite 引擎。

应用运行时使用异步引擎（sqlite+aiosqlite），但 Alembic 迁移统一用同步引擎
（sqlite），两者共享同一套 SQLAlchemy 模型与 Base.metadata。

数据库 URL 从 app.config.settings 动态读取；生成 baseline / 测试时可设置
环境变量 ``ALEMBIC_DB_URL`` 覆盖（指向临时空库），避免触碰真实数据库。
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 依赖 alembic.ini 的 prepend_sys_path=. 使 backend 目录在 sys.path 中
from app.config import settings
from app.database import Base

import app.models  # noqa: F401  导入全部模型，注册到 Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 同步 SQLite URL，优先级：环境变量 > alembic.ini 显式值 > settings 默认（真实库）。
# 这样命令行 `alembic upgrade head` 默认作用于真实库，而脚本/测试可通过
# ALEMBIC_DB_URL 或 Config.set_main_option 指向临时库，避免误触真实数据。
env_url = os.environ.get("ALEMBIC_DB_URL")
if env_url:
    config.set_main_option("sqlalchemy.url", env_url)
elif not config.get_main_option("sqlalchemy.url"):
    db_path = settings.storage_root.parent / "fashion_inspo.db"
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL 脚本，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # SQLite 锁竞争时最多等 10 秒，超时抛错由调用方（run_migrations）降级处理
        connect_args={"timeout": 10},
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite 的 ALTER TABLE 能力有限（无法直接 DROP/RENAME 列等），
            # batch 模式用「建新表 + 拷贝 + 删旧表 + 重命名」绕过限制
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
