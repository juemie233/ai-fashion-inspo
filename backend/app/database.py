"""数据库引擎与会话管理：SQLite + SQLAlchemy async。"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# SQLite 异步引擎（check_same_thread=False 允许跨线程访问）
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args={"check_same_thread": False, "timeout": 30},
)

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
