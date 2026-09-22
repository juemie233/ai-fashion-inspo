"""校验脚本（scripts/verify_storage_move.py）核心逻辑单测。

它是迁移时「切换是否安全」的证据工具——判错就等于假绿，所以逐项锁死：
目录统计、路径列发现、库引用核对（含缺失判定）、源/目标对比。
"""

import sqlite3

from scripts import verify_storage_move as vsm


def test_dir_stats_counts_nested_files(tmp_path):
    (tmp_path / "images" / "2026-08").mkdir(parents=True)
    (tmp_path / "images" / "2026-08" / "a.jpg").write_bytes(b"x" * 10)
    (tmp_path / "images" / "b.jpg").write_bytes(b"y" * 5)
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "c.mp4").write_bytes(b"z" * 100)

    files, total = vsm.dir_stats(tmp_path)

    assert files == 3
    assert total == 115


def test_path_columns_finds_only_path_suffix(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE inspirations (id TEXT, file_path TEXT, thumbnail_path TEXT, caption TEXT)")
    conn.execute("CREATE TABLE other (url TEXT)")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db)
    try:
        found = sorted(vsm.path_columns(conn))
    finally:
        conn.close()

    assert found == [("inspirations", "file_path"), ("inspirations", "thumbnail_path")]


def test_check_db_references_reports_missing(tmp_path):
    root = tmp_path / "root"
    (root / "images").mkdir(parents=True)
    (root / "images" / "ok.jpg").write_bytes(b"x")

    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE inspirations (id TEXT, file_path TEXT, thumbnail_path TEXT)")
    conn.execute("INSERT INTO inspirations VALUES ('1', 'images/ok.jpg', 'thumbnails/gone.jpg')")
    conn.commit()
    conn.close()

    checked, missing, samples = vsm.check_db_references(db, root)

    assert checked == 2
    assert missing == 1                     # 缩略图不存在
    assert samples and "thumbnails/gone.jpg" in samples[0]


def test_check_db_references_does_not_modify_the_db(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE inspirations (id TEXT, file_path TEXT)")
    conn.execute("INSERT INTO inspirations VALUES ('1', 'images/missing.jpg')")
    conn.commit()
    conn.close()
    before = db.read_bytes()

    vsm.check_db_references(db, root)

    assert db.read_bytes() == before        # 只读：连 journal/WAL 都不该动主库


def test_compare_dirs_detects_mismatch(tmp_path):
    source = tmp_path / "src"
    target = tmp_path / "dst"
    for base, names in ((source, ("a.jpg", "b.jpg")), (target, ("a.jpg",))):
        (base / "images").mkdir(parents=True)
        for name in names:
            (base / "images" / name).write_bytes(b"x" * 10)

    problems = vsm.compare_dirs(source, target)

    assert problems, "文件数不同必须报出来"
    assert any("images" in line for line in problems)


def test_compare_dirs_passes_when_identical(tmp_path):
    source = tmp_path / "src"
    target = tmp_path / "dst"
    for base in (source, target):
        (base / "images").mkdir(parents=True)
        (base / "images" / "a.jpg").write_bytes(b"x" * 10)

    assert vsm.compare_dirs(source, target) == []
