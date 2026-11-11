"""SQLite 连接级调优（2026-11-06 数据库审查 P2-9）。"""

from __future__ import annotations

from sqlalchemy import text

from app.config import settings
from app.database import engine


async def test_connection_pragmas_tuned():
    async with engine.connect() as conn:
        cache = (await conn.execute(text("PRAGMA cache_size"))).scalar()
        mmap = (await conn.execute(text("PRAGMA mmap_size"))).scalar()
        temp_store = (await conn.execute(text("PRAGMA temp_store"))).scalar()
    assert cache == settings.sqlite_cache_size_kb
    assert mmap == settings.sqlite_mmap_size_bytes
    assert temp_store == 2  # MEMORY
