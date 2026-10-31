"""迁移链的**带数据升级**检查：先在旧版本插数据，再 upgrade head，数据必须完好。

现有门禁（`scripts/check_fresh_migrations.py`）只验「**空库** → upgrade head → 与 ORM 一致」。
它拦不住真实事故的那一类：

    给已有 3 万条数据的表加了一个 NOT NULL 列、忘了 server_default
    → 空库测试全绿（空表没有行要填默认值），真实库 upgrade 时直接失败/清空

这类迁移事故一旦发生就是「上线迁移炸库」，而 SQLite 上没有回滚余地。本用例补的是
**「有数据的库升级」**这条路：

1. 建临时库 → `alembic upgrade <head 的父 revision>`（模拟线上旧版本库）；
2. 往几张核心表插入样例数据（素材/标签/关联/生成历史）；
3. `alembic upgrade head` → 必须成功；
4. 断言：行数与关键字段值原样保留、`alembic_version` 到 head、`integrity_check` ok、
   新增列对**老行**取到了合法默认值（不是 NULL，也不是 0 覆盖原值）。

用例自己算「head 的父 revision」（读 alembic ScriptDirectory），所以以后加新迁移不用改这里。
"""

import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent


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


def _head_and_parent() -> tuple[str, str | None]:
    """读迁移链：返回 (head revision, head 的父 revision)。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    assert head, "迁移链没有 head，检查 alembic/versions"
    rev = script.get_revision(head)
    down = rev.down_revision
    if isinstance(down, tuple):  # 多父合并迁移：取第一个作为基线（够用）
        down = down[0]
    return head, down


@pytest.fixture
def temp_db(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'migrate_with_data.db').as_posix()}"


def _required_columns(con: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    """返回该表「NOT NULL 且无默认值」的列（(列名, 类型)），这些列插入时必须给值。"""
    out = []
    for _cid, name, ctype, notnull, default, pk in con.execute(
        f"PRAGMA table_info({table})"
    ):
        upper = (ctype or "").upper()
        # 自增整数主键交给库；UUID/字符串主键必须显式给值（inspirations 就是这种）
        if pk and "INT" in upper:
            continue
        if (pk or notnull) and default is None:
            out.append((name, upper))
    return out


def _value_for(name: str, ctype: str, insp_id: str):
    """按列名/类型给一个合法样例值。

    只对**旧版本库里已存在的 NOT NULL 列**负责；迁移后新加的列不在这里处理
    （老行由迁移的 server_default 负责，这正是本用例要验的东西）。
    """
    known = {
        "id": insp_id,
        "name": "迁移标签",
        "source_type": "douyin",
        "file_path": "images/2026-10/mig.jpg",
        "media_type": "image",
        "quality_status": "approved",
        "source": "manual",
        "category": "单品",
    }
    if name in known:
        return known[name]
    if "INT" in ctype or "BOOL" in ctype:
        return 0
    if any(t in ctype for t in ("REAL", "FLOAT", "DOUBLE", "NUMERIC")):
        return 0.0
    if "BLOB" in ctype:
        return b""
    return ""


def _insert_row(con: sqlite3.Connection, table: str, insp_id: str, **overrides) -> int:
    """按目标库的实际列插入一行（只用旧版本存在的列，不用写死列名）。"""
    cols = {name: ctype for name, ctype in _required_columns(con, table)}
    values = {name: _value_for(name, ctype, insp_id) for name, ctype in cols.items()}
    values.update(overrides)
    # 有 DEFAULT / 可空的列不显式插入，交给库自己填（与真实写入路径一致）
    placeholders = ",".join("?" * len(values))
    col_sql = ",".join(values)
    cur = con.execute(
        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})", list(values.values())
    )
    return cur.lastrowid


def _insert_sample_rows(tmp_path: Path) -> dict:
    """在旧版本库上写入样例数据（列名按该版本库的实际结构取，不写死）。"""
    db_file = tmp_path / "migrate_with_data.db"
    insp_id = str(uuid.uuid4())
    con = sqlite3.connect(db_file)
    try:
        _insert_row(con, "inspirations", insp_id)
        tag_id = _insert_row(con, "tags", insp_id, name="迁移标签")
        con.execute(
            "INSERT INTO inspiration_tags (inspiration_id, tag_id, confidence, source)"
            " VALUES (?, ?, 0.87, 'manual')",
            (insp_id, tag_id),
        )
        con.commit()
        return {"insp_id": insp_id, "tag_id": tag_id}
    finally:
        con.close()


def test_upgrade_head_preserves_existing_data(temp_db):
    """旧版本库（带数据）→ upgrade head：数据不丢、结构到 head、新增列有合法默认值。"""
    head, parent = _head_and_parent()
    assert parent, "这是首个迁移，没有父版本可模拟旧库"

    # 1) 建到「head 的父版本」——等同线上还没升级的库
    res = _alembic("upgrade", parent, db_url=temp_db)
    assert res.returncode == 0, f"升级到 {parent} 失败:\n{res.stdout}\n{res.stderr}"

    # 2) 写入样例数据
    db_file = Path(temp_db.replace("sqlite:///", ""))
    sample = _insert_sample_rows(db_file.parent)

    # 3) 升到 head（这一步就是「上线迁移」）
    res = _alembic("upgrade", "head", db_url=temp_db)
    assert res.returncode == 0, (
        "带数据升级 head 失败——真实库上线时会炸（常见原因：新增 NOT NULL 列"
        f"没给 server_default）:\n{res.stdout}\n{res.stderr}"
    )

    # 4) 数据完好 + 结构正确
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        ver = con.execute("SELECT version_num FROM alembic_version").fetchone()
        assert ver and ver[0] == head

        row = con.execute(
            "SELECT source_type, file_path, is_favorite FROM inspirations WHERE id=?",
            (sample["insp_id"],),
        ).fetchone()
        assert row == ("douyin", "images/2026-10/mig.jpg", 0), "升级把老行改坏了"

        conf, source = con.execute(
            "SELECT confidence, source FROM inspiration_tags WHERE inspiration_id=?",
            (sample["insp_id"],),
        ).fetchone()
        assert (conf, source) == (0.87, "manual"), "关联表的置信度/来源被迁移覆盖"

        # 升级后**新加的 NOT NULL 列**对老行必须有合法值：
        # 迁移漏了 server_default 时，这里会是 NULL 或直接升级失败
        for col in ("quality_status", "is_ai_generated"):
            cols = [r[1] for r in con.execute("PRAGMA table_info(inspirations)")]
            if col not in cols:
                continue
            value = con.execute(
                f"SELECT {col} FROM inspirations WHERE id=?", (sample["insp_id"],)
            ).fetchone()[0]
            assert value is not None, f"新增列 {col} 对老行是 NULL（迁移缺 server_default）"
    finally:
        con.close()

    # 5) 升级后的库与 ORM 一致（等价于对「刚升级的库」跑 alembic check）
    check = _alembic("check", db_url=temp_db)
    output = (check.stdout or "") + (check.stderr or "")
    assert "No new upgrade operations detected" in output, (
        "带数据升级后的结构与 ORM 不一致：\n" + output
    )
