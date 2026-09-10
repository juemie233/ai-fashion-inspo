"""f2 下载目录扫描/报表单元测试（scripts/import_f2_downloads.py）。

不触碰真实素材库：库内哈希与博主用临时 sqlite 或直接注入，目录用 tmp_path 构造。
"""

import sqlite3
from pathlib import Path

import pytest

from scripts import import_f2_downloads as f2


# ── 文件名解析 ──


@pytest.mark.parametrize(
    "name, kind, media_type, index",
    [
        ("2025-04-12 18-57-19_好可爱#jk_#甜妹_image_1.webp", "image", "image", 1),
        ("2025-04-12 18-57-19_好可爱#jk_#甜妹_image_12.webp", "image", "image", 12),
        ("2024-03-01 12-25-13_好困_#怦怦心跳手势舞_video.mp4", "video", "video", 0),
        ("2025-04-13 18-07-38_我这么可爱_#春_live_2.mp4", "live", "video", 2),
    ],
)
def test_parse_media_filename(name, kind, media_type, index):
    parsed = f2.parse_media_filename(Path("作者目录") / name, "作者目录")
    assert parsed is not None
    assert (parsed.kind, parsed.media_type, parsed.index) == (kind, media_type, index)
    assert parsed.created == name[:19]


def test_parse_media_filename_skips_tmp_and_unknown():
    """未下载完的残file与无法识别命名的文件必须跳过（否则会导入半截文件）。"""
    assert f2.parse_media_filename(Path("a/2025-01-01 00-00-00_标题_image_5.tmp")) is None
    assert f2.parse_media_filename(Path("a/随机名字.webp")) is None
    assert f2.parse_media_filename(Path("a/2025-01-01 00-00-00_标题_music.mp3")) is None


def test_work_key_groups_gallery_images():
    """同一作品的多张图共享作品键（图集聚合的基础）。"""
    a = f2.parse_media_filename(Path("A/2025-04-12 18-57-19_标题_image_1.webp"), "A")
    b = f2.parse_media_filename(Path("A/2025-04-12 18-57-19_标题_image_2.webp"), "A")
    c = f2.parse_media_filename(Path("A/2025-04-13 18-07-38_另一条_image_1.webp"), "A")
    assert a.work_key == b.work_key
    assert a.work_key != c.work_key


def test_caption_and_hashtags():
    parsed = f2.parse_media_filename(
        Path("A/2025-04-12 18-57-19_你根本不给我道歉_算了_好可爱#jk_#甜妹_image_1.webp"),
        "A",
    )
    assert parsed.caption == "你根本不给我道歉 算了 好可爱#jk #甜妹"
    assert parsed.hashtags == ["jk", "甜妹"]


def test_hashtags_dedup_and_order():
    parsed = f2.parse_media_filename(
        Path("A/2025-01-01 00-00-00_#jk_正文_#jk_#通勤_video.mp4"), "A"
    )
    assert parsed.hashtags == ["jk", "通勤"]


# ── 作者名归一化 ──


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("里香1√", "里香"),
        ("里香2√", "里香"),
        ("冬至淚1√", "冬至淚"),
        ("小猫___√", "小猫"),
        ("美羊羊桑_", "美羊羊桑"),
        ("Nana.", "Nana"),
        ("不养羊√", "不养羊"),
        ("Kitty觉觉", "Kitty觉觉"),
        ("", ""),
    ],
)
def test_normalize_author(raw, expected):
    assert f2.normalize_author(raw) == expected


def test_normalize_author_merges_split_dirs():
    """f2 遇到同名账号会拆出 `里香1√` / `里香2√`，归一化后应合并成同一作者。"""
    assert f2.normalize_author("里香1√") == f2.normalize_author("里香2√")


# ── 目录扫描与分组 ──


def _write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_scan_directory_skips_tmp_and_collects(tmp_path):
    _write(tmp_path / "里香1√" / "2025-01-01 10-00-00_标题A_image_1.webp", b"a")
    _write(tmp_path / "里香1√" / "2025-01-01 10-00-00_标题A_image_2.webp", b"b")
    _write(tmp_path / "里香1√" / "2025-01-01 10-00-00_标题A_image_3.tmp", b"c")
    _write(tmp_path / "里香1√" / "notes.txt", b"x")
    _write(tmp_path / "Jade7" / "2025-02-02 11-00-00_标题B_video.mp4", b"d")

    files = f2.scan_directory(tmp_path)
    assert len(files) == 3
    # 归一化会去掉结尾数字（同 `里香1√`→`里香`）——两侧同样归一化，故仍能匹配；
    # 若库内真存在两个仅结尾数字不同的账号，报表会以「多候选」形式提示人工确认
    assert {f.author_key for f in files} == {"里香", "Jade"}

    works = f2.group_works(files)
    assert len(works) == 2
    gallery = next(v for k, v in works.items() if "标题A" in k)
    assert [f.index for f in gallery] == [1, 2]  # 组内按序号排序，首图稳定


def test_scan_directory_missing_root(tmp_path):
    assert f2.scan_directory(tmp_path / "不存在") == []


# ── 报表 ──


def _fake_tree(tmp_path: Path) -> list[f2.ParsedFile]:
    _write(tmp_path / "里香1√" / "2025-01-01 10-00-00_#jk_#穿搭_image_1.webp", b"img1")
    _write(tmp_path / "里香1√" / "2025-01-01 10-00-00_#jk_#穿搭_image_2.webp", b"img2")
    _write(tmp_path / "未知账号" / "2025-02-02 11-00-00_#通勤_video.mp4", b"vid")
    return f2.scan_directory(tmp_path)


def test_build_report_counts_new_vs_in_library(tmp_path):
    files = _fake_tree(tmp_path)
    img1 = next(f for f in files if f.path.name.endswith("image_1.webp"))
    library_hashes = {f2.sha256_file(img1.path)}  # 只有第一张图已在库

    report = f2.build_report(
        root=tmp_path,
        files=files,
        library_hashes=library_hashes,
        bloggers={"里香": [{"id": 302, "name": "里香"}]},
    )

    assert report["files_total"] == 3
    assert report["files_new"] == 2
    assert report["files_in_library"] == 1
    assert report["works_with_new"] == 2  # 图集作品有净新增、视频作品也是新的
    assert report["works_all_in_library"] == 0
    assert report["kind_stat"]["image"] == {"净新增": 1, "已入库": 1}
    assert report["kind_stat"]["video"] == {"净新增": 1}
    # 图集作品含 2 张图 → 落在 2-4 张档
    assert report["gallery_dist"]["2-4 张"] == 1
    assert report["gallery_dist"]["纯视频/实况（无图）"] == 1


def test_build_report_matches_blogger_and_lists_unmatched(tmp_path):
    files = _fake_tree(tmp_path)
    report = f2.build_report(
        root=tmp_path,
        files=files,
        library_hashes=set(),
        bloggers={"里香": [{"id": 302, "name": "里香"}]},
    )
    by_dir = {a["author_dir"]: a for a in report["authors"]}
    assert by_dir["里香1√"]["blogger_matched"] == ["里香"]
    assert by_dir["未知账号"]["blogger_candidates"] == 0
    assert report["unmatched_authors"] == ["未知账号"]


def test_build_report_hashtags_and_cost(tmp_path):
    files = _fake_tree(tmp_path)
    report = f2.build_report(
        root=tmp_path,
        files=files,
        library_hashes=set(),
        bloggers={},
        sec_per_tag=10.0,
    )
    tags = dict(report["top_hashtags"])
    assert tags["jk"] == 2 and tags["穿搭"] == 2 and tags["通勤"] == 1
    cost = report["tag_cost"]
    assert cost["per_file_count"] == 3
    assert cost["per_file_hours"] == 0.0  # 3 × 10s ≈ 0.008 小时 → 四舍五入 0.0
    assert cost["per_work_count"] == 2


def test_build_report_reuses_hash_cache(tmp_path):
    """重复调用复用哈希缓存，避免重复读盘（大目录扫描的关键优化）。"""
    files = _fake_tree(tmp_path)
    cache: dict = {}
    f2.build_report(tmp_path, files, set(), {}, hash_cache=cache)
    assert len(cache) == 3


# ── 素材库侧读取（临时 sqlite）──


def _make_lib(tmp_path: Path) -> Path:
    db = tmp_path / "fashion_inspo.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE inspirations (id TEXT, content_hash TEXT, deleted_at TEXT)"
    )
    conn.execute("CREATE TABLE bloggers (id INTEGER, name TEXT, platform TEXT)")
    conn.execute("INSERT INTO inspirations VALUES ('a', 'hash-a', NULL)")
    conn.execute("INSERT INTO inspirations VALUES ('b', 'hash-b', '2026-01-01')")  # 垃圾桶
    conn.execute("INSERT INTO inspirations VALUES ('c', NULL, NULL)")
    conn.execute("INSERT INTO bloggers VALUES (302, '里香', 'douyin')")
    conn.execute("INSERT INTO bloggers VALUES (303, '里香2√', 'douyin')")
    conn.execute("INSERT INTO bloggers VALUES (1, '某小红书博主', 'xiaohongshu')")
    conn.commit()
    conn.close()
    return db


def test_load_library_hashes_excludes_trash_and_null(tmp_path):
    """垃圾桶素材视为可重新入库、空哈希忽略（与 find_duplicate_by_hash 同口径）。"""
    assert f2.load_library_hashes(_make_lib(tmp_path)) == {"hash-a"}


def test_load_douyin_bloggers_indexed_by_normalized_name(tmp_path):
    """同名多条（里香 / 里香2√）归到同一归一化键下，便于人工确认。"""
    bloggers = f2.load_douyin_bloggers(_make_lib(tmp_path))
    assert sorted(b["name"] for b in bloggers["里香"]) == ["里香", "里香2√"]
    assert "某小红书博主" not in str(bloggers)  # 非抖音博主不入索引


def test_load_functions_tolerate_missing_db(tmp_path):
    missing = tmp_path / "不存在.db"
    assert f2.load_library_hashes(missing) == set()
    assert f2.load_douyin_bloggers(missing) == {}
