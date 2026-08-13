"""数据库引擎与会话管理：SQLite + SQLAlchemy async。"""

import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

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
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
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


async def init_db():
    """创建所有数据库表。在应用启动时调用。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
