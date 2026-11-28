"""数据库迁移兼容兜底模块（db_migrations）的边缘单测。

既有 tests/test_unit_schema_repair.py 覆盖「全量唯一索引 → 部分唯一索引」的一次
成功重建；本文件用临时 SQLite 补齐：schema 版本握手格式/稳定性、``get_db_path``
口径、``ensure_schema`` 的整表补列 + 索引幂等、并发启动的
「duplicate column name」容忍、以及重建部分索引时的 OperationalError 容忍。
不触碰真实数据库。
"""

import hashlib
import json
import sqlite3

import aiosqlite
import pytest

import app.db_migrations as db_migrations


# ═══════════════════════════════════════════════════════════════
#  版本号 / 路径
# ═══════════════════════════════════════════════════════════════


def test_compute_schema_version_format_and_stability():
    version = db_migrations.compute_schema_version()
    db_hash, api_version = version.split("-")
    assert len(db_hash) == 8
    int(db_hash, 16)  # 合法十六进制
    assert api_version == str(db_migrations.API_CONTRACT_VERSION)
    assert db_migrations.compute_schema_version() == version  # 稳定可复现


def test_compute_schema_version_changes_with_schema_lists(monkeypatch):
    baseline = db_migrations.compute_schema_version()
    columns = {table: list(cols) for table, cols in db_migrations._SCHEMA_COLUMNS.items()}
    columns["dummy_table"] = [("new_col", "TEXT")]
    monkeypatch.setattr(db_migrations, "_SCHEMA_COLUMNS", columns)

    assert db_migrations.compute_schema_version() != baseline

    # 期望值可用同一套序列化口径复算（防止实现偷偷改哈希输入）
    payload = {
        "columns": {
            table: sorted(cols) for table, cols in sorted(columns.items())
        },
        "indexes": {
            table: sorted(idxs)
            for table, idxs in sorted(db_migrations._SCHEMA_INDEXES.items())
        },
    }
    expected_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]
    assert db_migrations.compute_schema_version() == (
        f"{expected_hash}-{db_migrations.API_CONTRACT_VERSION}"
    )


def test_get_db_path_uses_settings():
    from app.config import settings

    # db_file_path 是 Settings 的派生 property（不可赋值），只验证口径一致性
    assert db_migrations.get_db_path() == settings.db_file_path


# ═══════════════════════════════════════════════════════════════
#  ensure_schema：补列 / 补索引 / 幂等
# ═══════════════════════════════════════════════════════════════


_LEGACY_SCHEMA = """
CREATE TABLE inspirations (id TEXT PRIMARY KEY, source_platform_id TEXT, deleted_at DATETIME);
CREATE TABLE scraper_tasks (id INTEGER PRIMARY KEY);
CREATE TABLE ai_analysis_log (id INTEGER PRIMARY KEY);
CREATE TABLE tags (id INTEGER PRIMARY KEY);
CREATE TABLE task_queue (id INTEGER PRIMARY KEY);
"""


def _make_legacy_db(path) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(_LEGACY_SCHEMA)
        con.commit()
    finally:
        con.close()


def _table_columns(path, table: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _index_names(path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    finally:
        con.close()


async def test_ensure_schema_adds_all_missing_columns_and_indexes(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    monkeypatch.setattr(db_migrations, "get_db_path", lambda: db)

    added = await db_migrations.ensure_schema()

    # 历史库已存在的列（id/source_platform_id/deleted_at）不会重复 ALTER
    expected = {
        f"{table}.{col}"
        for table, cols in db_migrations._SCHEMA_COLUMNS.items()
        for col, _def in cols
        if not (table == "inspirations" and col in {"deleted_at"})
    }
    assert set(added) == expected
    assert len(added) == len(expected)  # 无重复登记

    columns = _table_columns(db, "inspirations")
    assert {"deleted_at", "quality_status", "trash_reason", "content_hash"} <= columns
    assert "diagnostics" in _table_columns(db, "scraper_tasks")
    assert "prompt_version" in _table_columns(db, "ai_analysis_log")

    indexes = _index_names(db)
    for table_indexes in db_migrations._SCHEMA_INDEXES.values():
        for index_name, _column in table_indexes:
            assert index_name in indexes

    # 幂等：再跑一次没有任何新增
    assert await db_migrations.ensure_schema() == []
    assert _table_columns(db, "tags") == {"id", *_tags_columns()}


def _tags_columns() -> set[str]:
    return {col for col, _def in db_migrations._SCHEMA_COLUMNS["tags"]}


class _HookedConnection:
    """在真实 aiosqlite 连接上按 SQL 注入异常（模拟并发/锁冲突）。"""

    def __init__(self, real, hook) -> None:
        self._real = real
        self._hook = hook

    async def execute(self, sql: str, *args):
        action = self._hook(sql)
        if action == "raise_duplicate":
            raise aiosqlite.OperationalError("duplicate column name: description")
        if action == "raise_operational":
            raise aiosqlite.OperationalError("database is locked")
        return await self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _ConnectContext:
    def __init__(self, path, hook, real_connect) -> None:
        self._path = path
        self._hook = hook
        self._real = None
        self._real_connect = real_connect

    async def __aenter__(self):
        self._real = await self._real_connect(str(self._path))
        return _HookedConnection(self._real, self._hook)

    async def __aexit__(self, *_exc):
        await self._real.close()


async def test_ensure_schema_tolerates_concurrent_duplicate_column(tmp_path, monkeypatch):
    """多进程同时启动：ALTER 报 duplicate column 时跳过该列，不阻断其余迁移。"""
    db = tmp_path / "race.db"
    _make_legacy_db(db)
    monkeypatch.setattr(db_migrations, "get_db_path", lambda: db)

    fired: list[str] = []

    def _hook(sql: str) -> str | None:
        if "ADD COLUMN description " in sql and not fired:
            fired.append(sql)
            return "raise_duplicate"
        return None

    real_connect = aiosqlite.connect  # 先抓真实入口，再打桩（monkeypatch 改的是同一模块）
    monkeypatch.setattr(
        db_migrations.aiosqlite,
        "connect",
        lambda path: _ConnectContext(path, _hook, real_connect),
    )

    added = await db_migrations.ensure_schema()

    assert fired  # 确实命中了「并发已加列」分支
    assert "tags.description" not in added  # 被容忍跳过
    assert "tags.pinned" in added  # 其余列照常补齐
    # 第二进程下一轮仍会再尝试，说明没有把表整体跳过
    assert "tags.description" not in _table_columns(db, "tags")


async def test_ensure_schema_tolerates_index_rebuild_operational_error(tmp_path, monkeypatch):
    """重建部分索引时另一进程已持有锁：记 warning 后继续，不抛异常。"""
    db = tmp_path / "locked.db"
    _make_legacy_db(db)
    con = sqlite3.connect(db)
    try:
        # 历史库：错误的「全量唯一」索引（无 WHERE）
        con.execute(
            "CREATE UNIQUE INDEX ix_inspirations_source_platform_id "
            "ON inspirations (source_platform_id)"
        )
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr(db_migrations, "get_db_path", lambda: db)
    monkeypatch.setattr(db_migrations, "_SCHEMA_COLUMNS", {"inspirations": []})
    monkeypatch.setattr(db_migrations, "_SCHEMA_INDEXES", {})

    def _hook(sql: str) -> str | None:
        if sql.startswith("CREATE UNIQUE INDEX"):
            return "raise_operational"
        return None

    real_connect = aiosqlite.connect
    monkeypatch.setattr(
        db_migrations.aiosqlite,
        "connect",
        lambda path: _ConnectContext(path, _hook, real_connect),
    )

    # 不抛异常即通过；索引保持旧定义，下一轮 startup 会再查
    assert await db_migrations.ensure_schema() == []
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='ix_inspirations_source_platform_id'"
        ).fetchone()
    finally:
        con.close()
    # 重建失败被容忍：要么残留旧索引（无 WHERE），要么已被 DROP 掉等下一轮重建
    assert row is None or "WHERE" not in (row[0] or "").upper()


@pytest.mark.parametrize("where_condition", ["deleted_at IS NULL"])
def test_known_partial_unique_indexes_contract(where_condition):
    """已知部分唯一索引清单的结构契约（表/列/谓词齐全，ensure_schema 按它重建）。"""
    rows = db_migrations._KNOWN_PARTIAL_UNIQUE_INDEXES
    assert rows
    tables, index_names, columns, predicates = zip(*rows, strict=True)
    assert "inspirations" in tables
    assert "ix_inspirations_source_platform_id" in index_names
    assert "source_platform_id" in columns
    assert all(predicate.strip() for predicate in predicates)
    assert where_condition in predicates


async def test_ensure_schema_skips_rebuild_when_active_duplicates_exist(
    tmp_path, monkeypatch
):
    """存量重复值存在时不敢重建唯一索引：记错误并跳过，不抛异常。"""
    db = tmp_path / "dups.db"
    con = sqlite3.connect(db)
    try:
        con.executescript(
            """
            CREATE TABLE inspirations (
                id TEXT PRIMARY KEY,
                source_platform_id TEXT,
                deleted_at DATETIME
            );
            INSERT INTO inspirations VALUES ('a', 'dup-pid', NULL);
            INSERT INTO inspirations VALUES ('b', 'dup-pid', NULL);
            """
        )
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr(db_migrations, "get_db_path", lambda: db)
    monkeypatch.setattr(db_migrations, "_SCHEMA_COLUMNS", {"inspirations": []})
    monkeypatch.setattr(db_migrations, "_SCHEMA_INDEXES", {})

    assert await db_migrations.ensure_schema() == []

    index_names = _index_names(db)
    # 有活跃重复 → 本轮不建部分唯一索引（避免启动即失败），留待人工处理
    assert "ix_inspirations_source_platform_id" not in index_names


async def test_ensure_schema_reraises_non_duplicate_alter_errors(tmp_path, monkeypatch):
    """ALTER 报其它错误（非并发 duplicate column）：不能静默吞掉，必须向上抛。"""
    db = tmp_path / "fatal.db"
    _make_legacy_db(db)
    monkeypatch.setattr(db_migrations, "get_db_path", lambda: db)

    def _hook(sql: str) -> str | None:
        if sql.startswith("ALTER TABLE"):
            return "raise_fatal"
        return None

    class _FatalConnection(_HookedConnection):
        async def execute(self, sql: str, *args):
            if self._hook(sql) == "raise_fatal":
                raise ValueError("磁盘只读")
            return await super().execute(sql, *args)

    real_connect = aiosqlite.connect

    class _FatalContext(_ConnectContext):
        async def __aenter__(self):
            self._real = await real_connect(str(self._path))
            return _FatalConnection(self._real, self._hook)

    monkeypatch.setattr(
        db_migrations.aiosqlite,
        "connect",
        lambda path: _FatalContext(path, _hook, real_connect),
    )

    with pytest.raises(ValueError, match="磁盘只读"):
        await db_migrations.ensure_schema()
