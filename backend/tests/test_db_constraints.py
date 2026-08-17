"""数据库级唯一约束验证（方案 C）：锁定「重复执行」的数据库兜底。

背景：
- 逻辑层查重存在并发竞态窗口（两个请求同时查重都通过后双双入库）；
  数据库唯一约束把「检查」变成强制，是防重复的最后一道防线。
- 本文件直接以 SQL 验证约束行为，防止未来迁移误删/改坏约束。

验证对象：
1. `inspirations.source_platform_id` 部分唯一索引（仅 deleted_at IS NULL）：
   - 未删除素材之间平台 ID 唯一（重复插入抛 IntegrityError）
   - 垃圾桶素材释放平台 ID（软删除后同 ID 可重新入库）
2. `scraper_seen_urls.source_url` 主键唯一：重复墓碑 URL 抛 IntegrityError
"""

import sqlite3

import pytest

from app.config import settings

# inspirations 最小必填列（ORM 默认值在 Python 层，直连 SQL 需显式提供）
_INSERT_INSP = (
    "INSERT INTO inspirations (id, source_type, file_path, media_type, "
    "source_platform_id, is_favorite, quality_status, is_ai_generated, "
    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, 'pending', 0, "
    "datetime('now'), datetime('now'))"
)


def _conn() -> sqlite3.Connection:
    """直连测试库（同步 sqlite3，与 test_trash.py 同一模式）。"""
    db_path = settings.storage_root.parent / "fashion_inspo.db"
    return sqlite3.connect(str(db_path))


def test_partial_unique_index_exists(client):
    """部分唯一索引存在且 unique=1（防未来迁移误删约束）。"""
    conn = _conn()
    try:
        rows = conn.execute("PRAGMA index_list('inspirations')").fetchall()
        idx = next((r for r in rows if r[1] == "ix_inspirations_source_platform_id"), None)
        assert idx is not None, "缺少 ix_inspirations_source_platform_id 索引"
        assert idx[2] == 1  # unique 标志
    finally:
        conn.close()


def test_duplicate_platform_id_rejected_for_active(client, upload):
    """数据库兜底：未删除素材重复平台 ID 被拒绝（并发竞态的最终防线）。"""
    insp = upload().json()["id"]
    platform_id = "plat-dup-active"

    conn = _conn()
    try:
        # 第一条正常（逻辑层查重路径之外的直接写入，模拟竞态窗口）
        conn.execute(
            _INSERT_INSP,
            ("id-a", "scraper", "images/x.jpg", "image", platform_id),
        )
        conn.commit()

        # 第二条同平台 ID 且未删除 → 数据库拒绝
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                _INSERT_INSP,
                ("id-b", "scraper", "images/y.jpg", "image", platform_id),
            )
            conn.commit()
        conn.rollback()
    finally:
        conn.close()
    assert insp  # 上传素材本身正常（避免未使用告警）


def test_platform_id_released_when_trashed(client, upload):
    """垃圾桶释放平台 ID：软删除素材不参与唯一约束，同 ID 可重新入库。"""
    platform_id = "plat-released"

    conn = _conn()
    try:
        # 模拟：素材 A 入库后进垃圾桶（deleted_at 非空）
        conn.execute(
            _INSERT_INSP,
            ("id-c", "scraper", "images/a.jpg", "image", platform_id),
        )
        conn.execute(
            "UPDATE inspirations SET deleted_at = datetime('now'), "
            "trash_reason = '不喜欢', trash_source = 'manual' WHERE id = 'id-c'"
        )
        conn.commit()

        # 同平台 ID 重新入库（垃圾桶素材释放了该 ID）→ 允许
        conn.execute(
            _INSERT_INSP,
            ("id-d", "scraper", "images/b.jpg", "image", platform_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_seen_urls_primary_key_unique(client, upload):
    """墓碑主键兜底：重复 URL 墓碑被数据库拒绝（防采集重复闭环的最终防线）。"""
    url = "https://example.com/seen-dup.jpg"

    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO scraper_seen_urls (source_url) VALUES (?)", (url,)
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO scraper_seen_urls (source_url) VALUES (?)", (url,)
            )
            conn.commit()
        conn.rollback()
    finally:
        conn.close()
