"""收藏增强迁移的「带数据升级 + 可重入」检查（2026-11-19）。

验证 `c2d3e4f5a6b7`：
1. 在**迁移前的旧版本库**里插入「已收藏」与「未收藏」素材行；
2. upgrade 到该迁移后：
   - `favorited_at` 列/部分索引、`favorite_events` 表/索引都存在；
   - 已收藏行回填 `favorited_at = updated_at`（近似值），未收藏保持 NULL；
   - 已收藏行写一条 `source='legacy'` 的 favorite 事件，未收藏没有事件；
3. **可重入**：手动把 alembic 版本回退到父 revision（模拟迁移中断后重跑），
   再次 upgrade 不报错、不重复插事件、不覆盖已回填的时间。
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
MIGRATION_REV = "c2d3e4f5a6b7"  # 收藏增强迁移
PARENT_REV = "b1c2d3e4f5a6"  # 迁移前父 revision


def _alembic(*args: str, db_url: str) -> subprocess.CompletedProcess[str]:
    """在 backend 目录执行 alembic 子命令，指向临时库。"""
    env = {**os.environ, "ALEMBIC_DB_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        env=env,
    )


def _required_columns(con: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    """返回该表「NOT NULL 且无数据库默认值」的列（插入时必须给值）。"""
    out = []
    for _cid, name, ctype, notnull, default, pk in con.execute(
        f"PRAGMA table_info({table})"
    ):
        upper = (ctype or "").upper()
        if pk and "INT" in upper:  # 自增整数主键交给库
            continue
        if (pk or notnull) and default is None:
            out.append((name, upper))
    return out


def _insert_inspiration(
    con: sqlite3.Connection,
    insp_id: str,
    *,
    is_favorite: bool,
    created_at: str,
    updated_at: str,
) -> None:
    """按表结构插一条素材（必填列用类型安全的假值补齐）。"""
    overrides = {
        "id": insp_id,
        "is_favorite": 1 if is_favorite else 0,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    names: list[str] = []
    values: list[object] = []
    for name, ctype in _required_columns(con, "inspirations"):
        value = overrides.get(name)
        if value is None:
            if name in ("source_type", "file_path", "media_type", "quality_status"):
                value = "x" if name != "quality_status" else "approved"
            elif "INT" in ctype:
                value = 1
            elif "FLOAT" in ctype or "REAL" in ctype:
                value = 1.0
            else:
                value = "x"
        names.append(name)
        values.append(value)
    placeholders = ",".join("?" for _ in names)
    con.execute(
        f"INSERT INTO inspirations ({','.join(names)}) VALUES ({placeholders})",
        values,
    )
    con.commit()


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _index_sql(con: sqlite3.Connection, name: str) -> str:
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return (row[0] or "") if row else ""


@pytest.mark.slow
def test_favorite_migration_backfill_and_reentrant(tmp_path):
    db_file = tmp_path / "favorite_migration.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    # 1) 先升级到父 revision（模拟线上旧版本库）
    upgrade_parent = _alembic("upgrade", PARENT_REV, db_url=db_url)
    assert upgrade_parent.returncode == 0, upgrade_parent.stderr

    con = sqlite3.connect(str(db_file))
    try:
        _insert_inspiration(
            con,
            "fav-legacy",
            is_favorite=True,
            created_at="2026-01-01 00:00:00",
            updated_at="2026-06-01 12:00:00",
        )
        _insert_inspiration(
            con,
            "not-fav",
            is_favorite=False,
            created_at="2026-01-02 00:00:00",
            updated_at="2026-06-02 12:00:00",
        )
    finally:
        con.close()

    # 2) 升级到收藏增强迁移
    upgrade = _alembic("upgrade", MIGRATION_REV, db_url=db_url)
    assert upgrade.returncode == 0, upgrade.stderr

    con = sqlite3.connect(str(db_file))
    try:
        assert "favorited_at" in _columns(con, "inspirations")
        assert "favorite_events" in {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        # 回填：已收藏 = updated_at，未收藏保持 NULL
        fav_at = con.execute(
            "SELECT favorited_at FROM inspirations WHERE id='fav-legacy'"
        ).fetchone()[0]
        assert fav_at == "2026-06-01 12:00:00"
        assert (
            con.execute(
                "SELECT favorited_at FROM inspirations WHERE id='not-fav'"
            ).fetchone()[0]
            is None
        )

        # legacy 事件：只有已收藏行有，时间取回填值
        events = con.execute(
            "SELECT inspiration_id, action, source, created_at FROM favorite_events"
        ).fetchall()
        assert events == [
            ("fav-legacy", "favorite", "legacy", "2026-06-01 12:00:00")
        ]

        # 部分索引存在且带 WHERE 条件
        index_sql = _index_sql(con, "ix_inspirations_favorite_time")
        assert "favorited_at" in index_sql and "deleted_at" in index_sql
        assert "WHERE is_favorite = 1" in index_sql
        assert _index_sql(con, "ix_favorite_events_inspiration_time")

        # 3) 可重入：把版本回退到父 revision，再跑一次 upgrade
        con.execute(
            "UPDATE alembic_version SET version_num=?", (PARENT_REV,)
        )
        con.commit()
    finally:
        con.close()

    rerun = _alembic("upgrade", MIGRATION_REV, db_url=db_url)
    assert rerun.returncode == 0, rerun.stderr

    con = sqlite3.connect(str(db_file))
    try:
        # 事件不重复、时间不被覆盖
        assert (
            con.execute("SELECT COUNT(*) FROM favorite_events").fetchone()[0] == 1
        )
        assert (
            con.execute(
                "SELECT favorited_at FROM inspirations WHERE id='fav-legacy'"
            ).fetchone()[0]
            == "2026-06-01 12:00:00"
        )
        assert (
            con.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
            == "c2d3e4f5a6b7"
        )
    finally:
        con.close()
