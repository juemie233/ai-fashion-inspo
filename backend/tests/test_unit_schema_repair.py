"""历史库索引对账：run_migrations stamp 路径的兜底修复（2026-11-06 P1-1）。"""

from __future__ import annotations

import sqlite3

import app.db_migrations as db_migrations


async def test_ensure_schema_rebuilds_full_unique_platform_index(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE inspirations (
            id TEXT PRIMARY KEY,
            source_platform_id TEXT,
            deleted_at DATETIME
        );
        CREATE UNIQUE INDEX ix_inspirations_source_platform_id
            ON inspirations (source_platform_id);
        INSERT INTO inspirations VALUES ('trashed', 'pid-1', '2026-01-01');
        """
    )
    con.commit()
    con.close()

    monkeypatch.setattr(db_migrations, "get_db_path", lambda: db)
    monkeypatch.setattr(db_migrations, "_SCHEMA_COLUMNS", {"inspirations": []})
    monkeypatch.setattr(db_migrations, "_SCHEMA_INDEXES", {})

    await db_migrations.ensure_schema()

    con = sqlite3.connect(db)
    sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='ix_inspirations_source_platform_id'"
    ).fetchone()[0]
    assert "WHERE" in sql.upper()
    # 垃圾桶已释放该平台 ID：active 行可以插入
    con.execute(
        "INSERT INTO inspirations VALUES ('active', 'pid-1', NULL)"
    )
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM inspirations").fetchone()[0] == 2
    con.close()
