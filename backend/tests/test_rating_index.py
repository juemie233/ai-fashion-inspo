"""评分排序复合索引与查询计划（2026-11-12 评分方案 §8.3）。"""

from __future__ import annotations

import sqlite3

from app.config import settings


def test_rating_index_exists_and_used_for_rating_sort():
    con = sqlite3.connect(str(settings.db_file_path))
    try:
        index = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='ix_inspirations_active_rating_created'"
        ).fetchone()
        assert index is not None
        assert "(deleted_at, rating, created_at, id)" in index[0]

        plan = con.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT id FROM inspirations WHERE deleted_at IS NULL "
            "ORDER BY rating DESC, created_at DESC, id DESC LIMIT 50"
        ).fetchall()
        detail = " ".join(str(row[-1]) for row in plan)
        assert "ix_inspirations_active_rating_created" in detail, detail
        assert "TEMP B-TREE" not in detail.upper(), detail
    finally:
        con.close()
