"""话题存档（scraper_hashtags）单元测试——回归「静默失效」缺陷。

背景：真实库的 scraper_hashtags 由 Alembic 建表，``first_seen_at`` 是
NOT NULL 且无默认值；而 save_hashtags 的 INSERT 漏了该列，导致每次写入都
IntegrityError，异常又被 ``except: pass`` 吞掉——话题存档**长期 0 行**却无人
发现（脚本侧兜底建表 DDL 当时写成了「可空 + 默认值」，进一步掩盖了差异）。

本文件用**真实 schema** 建表，锁住该回归。
"""

import sqlite3

import pytest

from scripts import scraper_download as sd


# 与 Alembic 建出的真实表一致的 DDL（关键点：first_seen_at NOT NULL 无默认值）
_REAL_SCHEMA = (
    "CREATE TABLE scraper_hashtags ("
    "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
    "name VARCHAR(64) NOT NULL, "
    "seen_count INTEGER NOT NULL, "
    "first_seen_at DATETIME NOT NULL, "
    "last_seen_at DATETIME NOT NULL, "
    "source_kind VARCHAR(16) NOT NULL, "
    "source_id INTEGER, "
    "note_url TEXT, "
    "source_meta TEXT)"
)


@pytest.fixture(autouse=True)
def _reset_counter(monkeypatch):
    """话题计数为模块级列表，逐用例重置避免相互影响。"""
    monkeypatch.setattr(sd, "_HASHTAG_SAVED_COUNT", [0])


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.execute(_REAL_SCHEMA)
    connection.commit()
    yield connection
    connection.close()


def _meta(tags, blogger_id=None):
    return {"tags": tags, "blogger_id": blogger_id, "hashtags_saved": False}


def test_save_hashtags_writes_row_on_real_schema(conn):
    """回归：真实 schema（first_seen_at NOT NULL）下必须写入成功。"""
    saved = sd.save_hashtags(conn, _meta(["jk", "甜妹"]), "https://x/note/1")
    conn.commit()

    assert saved == 2
    rows = conn.execute(
        "SELECT name, seen_count, first_seen_at, last_seen_at, source_kind, note_url "
        "FROM scraper_hashtags ORDER BY name"
    ).fetchall()
    assert [r[0] for r in rows] == ["jk", "甜妹"]
    for name, seen, first_seen, last_seen, kind, note_url in rows:
        assert seen == 1
        assert first_seen  # 曾经缺失导致整条 INSERT 失败
        assert last_seen
        assert kind == "blogger"  # meta 未指定 source_kind 时的默认值
        assert note_url == "https://x/note/1"


def test_save_hashtags_accumulates_count_and_source_meta(conn):
    """第二次同名话题：计数累加、来源明细追加。"""
    sd.save_hashtags(conn, _meta(["jk"]), "https://x/note/1")
    conn.commit()
    sd.save_hashtags(conn, _meta(["jk"]), "https://x/note/2")
    conn.commit()

    seen_count, source_meta = conn.execute(
        "SELECT seen_count, source_meta FROM scraper_hashtags WHERE name = 'jk'"
    ).fetchone()
    assert seen_count == 2
    assert "note/1" in source_meta and "note/2" in source_meta


def test_save_hashtags_is_idempotent_per_note(conn):
    """同一笔记重复处理：meta 标记生效，返回 0 且不重复累加。"""
    meta = _meta(["jk"])
    assert sd.save_hashtags(conn, meta, "https://x/note/1") == 1
    assert sd.save_hashtags(conn, meta, "https://x/note/1") == 0
    conn.commit()
    assert conn.execute("SELECT seen_count FROM scraper_hashtags").fetchone()[0] == 1


def test_save_hashtags_ignores_empty_and_overlong_names(conn):
    """空话题与超长话题（>64）跳过；# 前缀会被剥掉。"""
    saved = sd.save_hashtags(conn, _meta(["#jk", "", "   ", "x" * 65]), "u")
    conn.commit()
    assert saved == 1
    assert conn.execute("SELECT name FROM scraper_hashtags").fetchone()[0] == "jk"


def test_save_hashtags_no_tags_returns_zero(conn):
    assert sd.save_hashtags(conn, _meta([]), "u") == 0
    assert sd.save_hashtags(conn, None, "u") == 0


def test_save_hashtags_caps_per_note(conn):
    """单笔记话题数上限（防脏数据）。"""
    tags = [f"tag{i}" for i in range(30)]
    saved = sd.save_hashtags(conn, _meta(tags), "u")
    conn.commit()
    assert saved == sd._HASHTAG_PER_NOTE_MAX


def test_ensure_hashtag_table_matches_real_schema():
    """脚本侧兜底建表 DDL 必须与真实 schema 对齐（尤其 NOT NULL 约束）。

    两套 DDL 不一致正是上面那个缺陷能长期藏住的原因。
    """
    connection = sqlite3.connect(":memory:")
    try:
        sd.ensure_hashtag_table(connection)
        columns = {
            row[1]: row[3] for row in connection.execute("PRAGMA table_info(scraper_hashtags)")
        }
    finally:
        connection.close()
    assert columns["first_seen_at"] == 1  # notnull
    assert columns["last_seen_at"] == 1
    assert columns["source_kind"] == 1
    assert columns["name"] == 1
