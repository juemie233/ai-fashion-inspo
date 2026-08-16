"""数据库引擎与会话管理：SQLite + SQLAlchemy async。"""

import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

# SQLite 异步引擎（check_same_thread=False 允许跨线程访问）
# 注意：不使用 echo 标志，改用 logging 级别控制 SQL 回显。
# echo=True 会让 SQLAlchemy 额外安装自己的 handler，导致每条 SQL 打印两份。
engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30},
)

if settings.debug:
    # debug 开启时记录 SQL；各进程可用 setLevel 覆盖（如 worker 降为 WARNING 避免刷屏）
    logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)


@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """每次建立连接时启用 SQLite 外键约束。

    SQLite 默认关闭外键，导致 ON DELETE SET NULL / ON DELETE CASCADE 失效。
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # WAL 模式：读不阻塞写、写不阻塞读，显著提升并发读写能力（尤其 AI 分析并发写日志时）
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


# 异步会话工厂
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""
    pass


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入：提供异步数据库会话，请求结束时自动提交或回滚。"""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """创建所有数据库表。在应用启动时调用。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def run_migrations() -> None:
    """将数据库迁移到 Alembic 管理的 head 版本（同步引擎，启动时调用）。

    幂等策略：
    - 全新空库：执行 ``upgrade head``，由 baseline 迁移建出全部表
    - 历史库（有业务表但无 alembic_version，由 create_all 建成）：``stamp head``
      仅记录版本号，不重建表
    - 已管理库：``upgrade head`` 应用基线之后的增量迁移

    Alembic 是正式迁移工具（支持 DROP/RENAME/改约束等 create_all 与手写
    ALTER TABLE 做不到的操作）；``ensure_schema`` 保留作兼容兜底。
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    # 从 settings.database_url 推导 SQLite 文件路径（与异步引擎同源），
    # 避免自定义 DATABASE_URL 时迁移错打「storage 旁的默认库」。
    _db_name = make_url(settings.database_url).database
    if _db_name:
        db_path = Path(_db_name)
    else:
        db_path = settings.storage_root.parent / "fashion_inspo.db"
    sync_url = f"sqlite:///{db_path.as_posix()}"
    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    # script_location 显式设为绝对路径，避免相对 CWD 解析错误
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))

    # timeout=10：SQLite 锁竞争（如 server 与 worker 并发启动同时迁移）时最多等 10 秒，
    # 超时抛 OperationalError 由下方 try-except 降级，避免进程无限卡在迁移上
    engine_sync = create_engine(sync_url, connect_args={"timeout": 10})
    with engine_sync.connect() as conn:
        biz_tables = conn.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
            )
        ).scalar()
        has_alembic = conn.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='alembic_version'"
            )
        ).scalar()

    try:
        if biz_tables == 0:
            command.upgrade(cfg, "head")  # 空库：baseline 建表
        elif not has_alembic:
            command.stamp(cfg, "head")  # 历史库：标记到 baseline，不重建
        else:
            command.upgrade(cfg, "head")  # 已管理库：应用增量
    except Exception as e:
        # Alembic 失败（如并发锁竞争超时、脚本缺失）时静默降级，
        # 由 ensure_schema（手写补列）兜底，不阻断启动；
        # 用 ERROR 级日志，确保迁移停摆能被运维及时察觉
        logger.error(f"Alembic 迁移失败（降级到 ensure_schema 兜底）: {e}")
