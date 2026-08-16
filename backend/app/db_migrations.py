"""数据库迁移的兼容兜底模块（手写补列）。

正式迁移已改用 Alembic（``backend/alembic/``），由 ``database.run_migrations()``
在服务端进程启动时执行；本模块的 ``ensure_schema()`` 保留作兼容兜底：

- 存量库在引入 Alembic 之前，通过手写 ``ALTER TABLE`` 补齐的列（``_SCHEMA_COLUMNS``）
- Alembic 不可用或失败时，仍能补齐缺失列，不阻断启动

**新增字段/表请走 Alembic**（``alembic revision --autogenerate`` + ``alembic
upgrade head``），不再往 ``_SCHEMA_COLUMNS`` 手写追加；``compute_schema_version()``
仍用于前后端 schema 版本握手。
"""

import hashlib
import json
from pathlib import Path

import aiosqlite

from app.config import settings

# API/Pydantic 契约版本：修改请求/响应模型、路由字段等「不落库」的契约时手动 +1。
# 与数据库结构哈希拼接成前后端握手用的 schema_version（见 compute_schema_version）。
API_CONTRACT_VERSION = 4

# 字段清单：表名 -> [(列名, 列定义), ...]
_SCHEMA_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "inspirations": [
        ("scraper_task_id", "INTEGER REFERENCES scraper_tasks(id) ON DELETE SET NULL"),
        ("quality_status", "TEXT DEFAULT 'pending'"),
        ("quality_reason", "TEXT"),
        ("is_ai_generated", "INTEGER DEFAULT 0"),
        ("content_hash", "TEXT"),
        ("deleted_at", "DATETIME"),
        ("trash_reason", "TEXT"),
    ],
    "scraper_tasks": [
        ("diagnostics", "TEXT"),
        ("resume_token", "TEXT"),
    ],
    "ai_analysis_log": [
        ("log_type", "TEXT DEFAULT 'analysis'"),
        ("prompt_version", "TEXT"),
        ("model_version", "TEXT"),
    ],
    "tags": [
        ("pinned", "INTEGER DEFAULT 0"),
        ("sort_order", "INTEGER DEFAULT 0"),
        ("description", "TEXT"),
    ],
    "persons": [
        ("person_type", "TEXT DEFAULT 'blogger'"),
    ],
}

# 索引清单：表名 -> [(索引名, 列名), ...]
# 存量库通过 ALTER TABLE 补列的字段不会自动获得 ORM 中 index=True 生成的索引，
# 需在迁移时手动补齐（索引名参考 SQLAlchemy 默认命名 ix_{table}_{column}）。
_SCHEMA_INDEXES: dict[str, list[tuple[str, str]]] = {
    "inspirations": [
        ("ix_inspirations_is_ai_generated", "is_ai_generated"),
        ("ix_inspirations_content_hash", "content_hash"),
        ("ix_inspirations_deleted_at", "deleted_at"),
    ],
    "tags": [
        ("ix_tags_pinned", "pinned"),
    ],
    "persons": [
        ("ix_persons_person_type", "person_type"),
    ],
}


def compute_schema_version() -> str:
    """计算前后端握手用的 schema 版本号。

    格式：``{数据库结构哈希}-{API 契约版本}``

    - 数据库结构哈希：对 _SCHEMA_COLUMNS / _SCHEMA_INDEXES 做稳定序列化后取
      SHA-256 前 8 位，修改列/索引清单时自动变化（无需手动递增）。
    - API 契约版本：修改 Pydantic 模型、路由字段等「不落库」的契约时手动 +1。

    该值通过 /api/health 暴露，前端启动时比对，用于把「静默失败」变成「显式提示」。
    """
    payload = {
        "columns": {table: sorted(cols) for table, cols in sorted(_SCHEMA_COLUMNS.items())},
        "indexes": {table: sorted(idxs) for table, idxs in sorted(_SCHEMA_INDEXES.items())},
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    db_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:8]
    return f"{db_hash}-{API_CONTRACT_VERSION}"


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
                    try:
                        await conn.execute(
                            f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                        )
                        added.append(f"{table}.{col_name}")
                        print(f"[迁移] {table} 添加列: {col_name}")
                    except Exception as e:
                        # 多进程（服务端 + worker）同时启动时可能并发执行迁移，
                        # 后到者会报 "duplicate column name"，幂等容忍即可。
                        if "duplicate column" in str(e).lower():
                            print(f"[迁移] {table}.{col_name} 已被其他进程添加，跳过")
                            continue
                        raise

        # 补齐存量库缺失的索引（幂等，CREATE INDEX IF NOT EXISTS）
        for table, indexes in _SCHEMA_INDEXES.items():
            for idx_name, column in indexes:
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column})"
                )

        await conn.commit()

    return added
