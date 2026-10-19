"""test_unit_f2_import_* 共用构造助手与常量（C-6）。"""

import sqlite3
from pathlib import Path
from scripts import import_f2_downloads as f2

SEC = "MS4wLjABAAAACyG6qmWLGt5BbCvwkAfMpEf3nhGwlQqSG1MjwDIGokuUHJnIkwJzxDPu-1RRrfvk"


AWEME = "7412345678901234567"



def _write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path



def _fake_tree(tmp_path: Path) -> list[f2.ParsedFile]:
    _write(tmp_path / "里香1√" / "2025-01-01 10-00-00_#jk_#穿搭_image_1.webp", b"img1")
    _write(tmp_path / "里香1√" / "2025-01-01 10-00-00_#jk_#穿搭_image_2.webp", b"img2")
    _write(tmp_path / "未知账号" / "2025-02-02 11-00-00_#通勤_video.mp4", b"vid")
    return f2.scan_directory(tmp_path)



def _make_lib(tmp_path: Path) -> Path:
    db = tmp_path / "fashion_inspo.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE inspirations (id TEXT, content_hash TEXT, deleted_at TEXT, "
        "source_platform_id TEXT)"
    )
    conn.execute("CREATE TABLE bloggers (id INTEGER, name TEXT, platform TEXT)")
    conn.execute("INSERT INTO inspirations VALUES ('a', 'hash-a', NULL, 'f2:live#image1')")
    # 垃圾桶：内容与平台 ID 都参与判重，故两类都要断言
    conn.execute("INSERT INTO inspirations VALUES ('b', 'hash-b', '2026-01-01', 'f2:trash#image1')")
    conn.execute("INSERT INTO inspirations VALUES ('c', NULL, NULL, NULL)")
    conn.execute("INSERT INTO bloggers VALUES (302, '里香', 'douyin')")
    conn.execute("INSERT INTO bloggers VALUES (303, '里香2√', 'douyin')")
    conn.execute("INSERT INTO bloggers VALUES (1, '某小红书博主', 'xiaohongshu')")
    conn.commit()
    conn.close()
    return db



def _decisions(
    files,
    library_hashes=None,
    platform_ids=None,
    trash_hashes=None,
    trash_platform_ids=None,
    **kwargs,
):
    return f2.build_import_plan(
        files=files,
        dedup=f2.DedupIndex(
            live_hashes=library_hashes or set(),
            trash_hashes=trash_hashes or set(),
            live_platform_ids=platform_ids or set(),
            trash_platform_ids=trash_platform_ids or set(),
        ),
        **kwargs,
    )



def _jpeg(path: Path, color: str = "red") -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 48), color).save(path, "JPEG")
    return path



def _fake_mp4(path: Path, content: bytes | None = None) -> Path:
    """带 ftyp 魔数的最小 mp4 头：够过 validate_media 的类型粗检（不解码）。

    ``content`` 可覆盖默认字节：同一作品下的 video 与其封面 cover 要给**不同内容**，
    否则会被「批次内同内容」判重跳过（真实素材字节本来就不同，同内容是测试假象）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    default = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    path.write_bytes(content or default)
    return path



def _import_lib(tmp_path: Path) -> Path:
    """建最小素材库表结构（列与 INSERT_F2_SQL 对齐）。

    按方案 A，f2 导入**不写话题存档表**，故这里不建 scraper_hashtags：
    #话题 随 caption 落库（见 test_apply_import_writes_material_files_and_rows）。
    """
    db = tmp_path / "lib.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE inspirations ("
        "id TEXT PRIMARY KEY, source_type TEXT, source_url TEXT, source_author TEXT, "
        "source_platform_id TEXT UNIQUE, file_path TEXT, thumbnail_path TEXT, "
        "media_type TEXT, dominant_colors TEXT, is_favorite INTEGER, quality_status TEXT, "
        "rating INTEGER, is_ai_generated INTEGER, content_hash TEXT, caption TEXT, "
        "scraper_task_id INTEGER, created_at TEXT, updated_at TEXT, deleted_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE inspiration_bloggers (inspiration_id TEXT, blogger_id INTEGER, "
        "confidence REAL, UNIQUE(inspiration_id, blogger_id))"
    )
    # 回滚的「已改动」判据要查标签关联，故按真实库建出该表（内容留空）
    conn.execute(
        "CREATE TABLE inspiration_tags (inspiration_id TEXT, tag_id INTEGER, source TEXT)"
    )
    conn.commit()
    conn.close()
    return db



def _f2_dir_with_authors(tmp_path: Path, authors=()) -> Path:
    """造一个 f2 工作目录：含 douyin_users.db 的 user_info_web 表。"""
    f2_dir = tmp_path / "f2proj"
    f2_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    for sec, nickname, count in authors or [
        ("MS4wLjABAAAAaaa", "里香1√", 171),
        ("MS4wLjABAAAAbbb", "娜娜瑜√", 232),
    ]:
        conn.execute("INSERT INTO user_info_web VALUES (?, ?, ?)", (sec, nickname, count))
    conn.commit()
    conn.close()
    return f2_dir



def _f2_dir_with_video_db(tmp_path: Path, rows=()) -> Path:
    """造一个 f2 工作目录：含 douyin_videos.db 的 video_info 表（作品 → 作者映射）。"""
    f2_dir = tmp_path / "f2videos"
    f2_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f2_dir / f2.F2_VIDEO_DB)
    conn.execute(
        "CREATE TABLE video_info (aweme_id TEXT, sec_user_id TEXT, nickname TEXT)"
    )
    conn.executemany("INSERT INTO video_info VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return f2_dir



def _imported_fixture(tmp_path: Path):
    """跑一次真导入，返回 (db, storage, batch_file, 入库的 id 列表)。"""
    root = tmp_path / "f2"
    _jpeg(root / "A" / "2025-01-01 10-00-00_#jk_标题_image_1.jpg")
    _jpeg(root / "A" / "2025-01-01 10-00-00_#jk_标题_image_2.jpg", "blue")
    db = _import_lib(tmp_path)
    storage = tmp_path / "storage"
    decisions, _, _ = _decisions(f2.scan_directory(root))
    result = f2.apply_import(
        [d for d in decisions if d.action == "import"],
        db_path=db,
        storage_root=storage,
    )
    return db, storage, Path(result["batch_file"]), result["ids"]



def _new_name(body: str = "标题", kind: str = "image_1") -> str:
    """新模板产物名：{create}_{desc}_{aweme_id}_{kind}。"""
    return f"2025-01-01 10-00-00_{body}_{AWEME}_{kind}.webp"

