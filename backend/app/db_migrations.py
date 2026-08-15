"""统一的数据库迁移模块。

所有会访问数据库的进程（FastAPI 服务端、采集脚本等）在启动时调用
`ensure_schema()`，确保物理表结构包含 ORM 模型声明的新列，避免 schema 漂移。

背景：
- SQLAlchemy 的 `create_all` 只创建表，不会更新已有表的新增列
- 开发期模型字段变更频繁，需要一个轻量的自动迁移机制
- 采集脚本作为独立子进程运行，不经过服务端 lifespan，需自行确保表结构

新增字段时，在下方 `_SCHEMA_COLUMNS` 清单中追加即可，
所有进程启动时会自动补全缺失列，无需手动 ALTER TABLE。
"""

from pathlib import Path

import aiosqlite

from app.config import settings

# 字段清单：表名 -> [(列名, 列定义), ...]
_SCHEMA_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "inspirations": [
        ("scraper_task_id", "INTEGER REFERENCES scraper_tasks(id) ON DELETE SET NULL"),
        ("quality_status", "TEXT DEFAULT 'pending'"),
        ("quality_reason", "TEXT"),
        ("is_ai_generated", "INTEGER DEFAULT 0"),
    ],
    "scraper_tasks": [
        ("diagnostics", "TEXT"),
    ],
    "ai_analysis_log": [
        ("log_type", "TEXT DEFAULT 'analysis'"),
    ],
    "tags": [
        ("pinned", "INTEGER DEFAULT 0"),
        ("sort_order", "INTEGER DEFAULT 0"),
        ("description", "TEXT"),
    ],
}

# 索引清单：表名 -> [(索引名, 列名), ...]
# 存量库通过 ALTER TABLE 补列的字段不会自动获得 ORM 中 index=True 生成的索引，
# 需在迁移时手动补齐（索引名参考 SQLAlchemy 默认命名 ix_{table}_{column}）。
_SCHEMA_INDEXES: dict[str, list[tuple[str, str]]] = {
    "inspirations": [
        ("ix_inspirations_is_ai_generated", "is_ai_generated"),
    ],
    "tags": [
        ("ix_tags_pinned", "pinned"),
    ],
}


def get_db_path() -> Path:
    """返回 SQLite 数据库文件的绝对路径。"""
    return settings.storage_root.parent / "fashion_inspo.db"


async def ensure_schema() -> list[str]:
    """确保所有表都包含清单中的列，返回本次新增的列名列表。

    Returns:
        本次迁移新增的列名列表（如 ``["scraper_tasks.diagnostics"]``），
        无变更时返回空列表。
    """
    added: list[str] = []
    async with aiosqlite.connect(str(get_db_path())) as conn:
        for table, columns in _SCHEMA_COLUMNS.items():
            cursor = await conn.execute(f"PRAGMA table_info({table})")
            rows = await cursor.fetchall()
            existing = {r[1] for r in rows}

            for col_name, col_def in columns:
                if col_name not in existing:
                    await conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                    )
                    added.append(f"{table}.{col_name}")
                    print(f"[迁移] {table} 添加列: {col_name}")

        # 补齐存量库缺失的索引（幂等，CREATE INDEX IF NOT EXISTS）
        for table, indexes in _SCHEMA_INDEXES.items():
            for idx_name, column in indexes:
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column})"
                )

        await conn.commit()

    return added
