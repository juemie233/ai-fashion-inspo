"""f2 单元测试杂项：路径与 ID 规范化、媒体解析、平台 ID 与来源链接。"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
import pytest
from f2_patch import patch_f2
from scripts import import_f2_downloads as f2
from f2_import_unit_helpers import (  # noqa: F401
    SEC,
    AWEME,
    _write,
    _fake_tree,
    _make_lib,
    _decisions,
    _jpeg,
    _fake_mp4,
    _import_lib,
    _f2_dir_with_authors,
    _f2_dir_with_video_db,
    _imported_fixture,
    _new_name,
)


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



@pytest.mark.parametrize(
    "raw, expected",
    [
        ("里香1√", "里香"),
        ("里香2√", "里香"),
        ("冬至淚1√", "冬至淚"),
        ("小猫___√", "小猫"),
        ("美羊羊桑_", "美羊羊桑"),
        # 抖音昵称常用波浪号做装饰；不清掉会让同一人被当成两个博主（实测踩点）
        ("美羊羊桑～", "美羊羊桑"),
        ("美羊羊桑~", "美羊羊桑"),
        ("美羊羊桑〜", "美羊羊桑"),
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



def test_download_tree_stats_counts_files_and_bytes(tmp_path):
    """实时进度统计：递归数文件与字节（供「我的喜欢」下载期展示「已落盘 N 个」）。"""
    _write(tmp_path / "我的账号" / "a_image_1.webp", b"12345")
    _write(tmp_path / "我的账号" / "a_image_2.webp", b"123")
    _write(tmp_path / "我的账号" / "子目录" / "b_video.mp4", b"1")

    stats = f2.download_tree_stats(tmp_path)

    assert stats == {"files": 3, "bytes": 9}



def test_download_tree_stats_missing_root_is_zero(tmp_path):
    """目录还不存在（首次「我的喜欢」）：返回 0 而不是抛错。"""
    assert f2.download_tree_stats(tmp_path / "不存在") == {"files": 0, "bytes": 0}



def test_load_dedup_index_splits_live_and_trash(tmp_path):
    """未删除与垃圾桶分别成集；垃圾桶也参与判重（见 load_dedup_index 的取舍说明）。"""
    index = f2.load_dedup_index(_make_lib(tmp_path))
    assert index.live_hashes == {"hash-a"}
    assert index.trash_hashes == {"hash-b"}  # 空哈希忽略
    assert index.live_platform_ids == {"f2:live#image1"}
    assert index.trash_platform_ids == {"f2:trash#image1"}



def test_load_douyin_bloggers_indexed_by_normalized_name(tmp_path):
    """同名多条（里香 / 里香2√）归到同一归一化键下，便于人工确认。"""
    bloggers = f2.load_douyin_bloggers(_make_lib(tmp_path))
    assert sorted(b["name"] for b in bloggers["里香"]) == ["里香", "里香2√"]
    assert "某小红书博主" not in str(bloggers)  # 非抖音博主不入索引



def test_load_functions_tolerate_missing_db(tmp_path):
    missing = tmp_path / "不存在.db"
    empty = f2.load_dedup_index(missing)
    assert empty.live_hashes == set() and empty.trash_hashes == set()
    assert empty.live_platform_ids == set() and empty.trash_platform_ids == set()
    assert f2.load_douyin_bloggers(missing) == {}



def test_platform_id_unique_per_file_and_groups_by_work():
    """同一作品的多张图必须各自唯一（否则撞唯一索引），但共享作品前缀。"""
    img1 = f2.parse_media_filename(Path("A/2025-01-01 10-00-00_标题_image_1.webp"), "A")
    img2 = f2.parse_media_filename(Path("A/2025-01-01 10-00-00_标题_image_2.webp"), "A")
    video = f2.parse_media_filename(Path("A/2025-02-02 11-00-00_另一条_video.mp4"), "A")

    ids = [f2.platform_id_for(x) for x in (img1, img2, video)]
    assert len(set(ids)) == 3
    assert f2.platform_id_for(img1) == f2.platform_id_for(img1)  # 稳定 → 天然幂等
    prefix = f"f2:{f2.work_hash(img1.work_key)}#"
    assert ids[0].startswith(prefix) and ids[1].startswith(prefix)
    assert not ids[2].startswith(prefix)



def test_platform_id_image_and_live_index_do_not_collide():
    """回归（试跑实测缺陷）：同一作品的静态图与 live 分段都用序号 1，
    只用数字会让两者平台 ID 相同而撞唯一索引，导致 live 分段整批写入失败。"""
    img1 = f2.parse_media_filename(Path("A/2025-01-01 10-00-00_#jk_image_1.webp"), "A")
    live1 = f2.parse_media_filename(Path("A/2025-01-01 10-00-00_#jk_live_1.mp4"), "A")
    assert img1.work_key == live1.work_key  # 同一作品
    assert f2.platform_id_for(img1) != f2.platform_id_for(live1)



def test_insert_sql_columns_match_values():
    """回归：列清单 / 值 / 占位符必须一一对应（历史事故：漏占位符导致静默不落库）。"""
    sql = f2.INSERT_F2_SQL
    columns = sql.split("(", 1)[1].split(")", 1)[0].count(",") + 1
    values = sql.split("VALUES", 1)[1].strip().rstrip(";")
    values = values[values.index("(") + 1 : values.rindex(")")]
    # 逗号分隔的顶层值数量（本 SQL 的值里不含函数括号，逗号即分隔符）
    value_count = len([v for v in values.split(",") if v.strip()])
    placeholders = values.count("?")
    assert columns == value_count, f"列 {columns} 个但值 {value_count} 个"
    assert placeholders == 12  # 其余为 NULL/0/'pending' 字面量



def test_load_douyin_bloggers_carries_sec_user_id(tmp_path):
    """博主清单要带上 platform_user_id（sec_user_id）：它是 f2 账号 ↔ 博主的权威对应。"""
    db = tmp_path / "lib.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE bloggers (id INTEGER PRIMARY KEY, name TEXT, platform TEXT, "
        "platform_user_id TEXT)"
    )
    conn.execute("INSERT INTO bloggers VALUES (1, '里香.', 'douyin', 'MS4x_lixiang')")
    conn.execute("INSERT INTO bloggers VALUES (2, '某书博主', 'xiaohongshu', 'XHS1')")
    conn.commit()
    conn.close()

    bloggers = f2.load_douyin_bloggers(db)

    assert bloggers["里香"][0]["platform_user_id"] == "MS4x_lixiang"
    assert "某书博主" not in bloggers  # 非抖音平台不参与匹配



def test_select_known_authors_matches_by_sec_user_id_then_name():
    """回归：f2 用户库里的官方号（网易第五人格）必须被判为「未登记」。

    判定口径：sec_user_id 命中博主 platform_user_id（权威）或归一化昵称命中博主名。
    """
    authors = [
        {"sec_user_id": "MS4x_u1", "nickname": "夕木_", "aweme_count": 1},
        {"sec_user_id": "MS4x_u2", "nickname": "改名了也行", "aweme_count": 1},
        {"sec_user_id": "MS4x_u3", "nickname": "网易第五人格", "aweme_count": 1},
    ]
    bloggers = {
        # 昵称口径命中（博主没回填 sec_user_id）
        "夕木": [{"id": 304, "name": "夕木.", "platform_user_id": None}],
        # 昵称对不上但 sec_user_id 对得上 → 仍算已登记
        "某某": [{"id": 9, "name": "某某", "platform_user_id": "MS4x_u2"}],
    }

    known, unknown = f2.select_known_authors(authors, bloggers)

    assert [a["nickname"] for a in known] == ["夕木_", "改名了也行"]
    assert [a["nickname"] for a in unknown] == ["网易第五人格"]



def test_select_known_authors_empty_bloggers_keeps_everyone():
    """库里一个抖音博主都没有时没有白名单依据：调用方据此退回旧口径（见 run_fetch）。"""
    authors = [{"sec_user_id": "MS4x_u3", "nickname": "网易第五人格", "aweme_count": 1}]
    known, unknown = f2.select_known_authors(authors, {})
    assert known == [] and len(unknown) == 1



def test_load_douyin_bloggers_excludes_auto_registered(tmp_path, monkeypatch):
    """「自动登记」的博主（我的喜欢来源作者）不算已登记博主：不进下载白名单。

    include_auto=True 才返回它们（博主管理页确认后改回 manual 即视为已登记）。
    """
    db = tmp_path / "lib.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE bloggers (id INTEGER PRIMARY KEY, name TEXT, platform TEXT, "
        "platform_user_id TEXT, source TEXT)"
    )
    conn.executemany(
        "INSERT INTO bloggers VALUES (?, ?, ?, ?, ?)",
        [
            (1, "里香", "douyin", "MS4x_lixiang", "manual"),
            (2, "点赞过的作者", "douyin", None, f2.AUTO_BLOGGER_SOURCE),
            (3, "老记录无 source", "douyin", None, None),
            (4, "小红书博主", "xiaohongshu", None, "manual"),
        ],
    )
    conn.commit()
    conn.close()
    patch_f2(monkeypatch, "library_db_path", lambda: db)

    default = f2.load_douyin_bloggers()
    assert set(default) == {"里香", "老记录无 source"}

    with_auto = f2.load_douyin_bloggers(include_auto=True)
    assert "点赞过的作者" in with_auto



def test_load_f2_profiles_strips_prefix(tmp_path):
    """f2 用户库资料读取：ip_location 剥掉「IP属地：」前缀，按 sec_user_id 索引。"""
    f2_dir = tmp_path / "f2proj"
    f2_dir.mkdir()
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, ip_location TEXT)"
    )
    conn.executemany(
        "INSERT INTO user_info_web VALUES (?, ?, ?)",
        [
            ("MS4x_a", "里香", "IP属地：浙江"),
            ("MS4x_b", "夕木", "IP属地: 广东"),  # 半角冒号同样识别
            ("MS4x_c", "没属地", ""),
            ("MS4x_d", "裸值不算", "浙江"),
            (None, "无 ID", "IP属地：江苏"),
        ],
    )
    conn.commit()
    conn.close()

    profiles = f2.load_f2_profiles(f2_dir)

    assert set(profiles) == {"MS4x_a", "MS4x_b", "MS4x_c", "MS4x_d"}
    assert profiles["MS4x_a"]["ip_location"] == "浙江"
    assert profiles["MS4x_b"]["ip_location"] == "广东"
    assert profiles["MS4x_c"]["ip_location"] == ""
    # 「裸值」不是带前缀的属地写法（避免把昵称误判成属地）
    assert profiles["MS4x_d"]["ip_location"] == ""



def test_load_f2_profiles_missing_db_or_column(tmp_path):
    """库不存在 / 没有 ip_location 列：返回空（调用方按「无可用资料」处理）。"""
    assert f2.load_f2_profiles(tmp_path / "不存在") == {}

    f2_dir = tmp_path / "old"
    f2_dir.mkdir()
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute("CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT)")
    conn.execute("INSERT INTO user_info_web VALUES ('MS4x_a', '里香')")
    conn.commit()
    conn.close()
    assert f2.load_f2_profiles(f2_dir) == {}



def test_hash_cache_reuses_digest_across_runs(tmp_path, monkeypatch):
    """回归：同一文件第二次运行不再读盘算哈希（首次 7.28 GB 约 81 秒的成本只付一次）。"""
    files = _fake_tree(tmp_path)
    db = tmp_path / "hash.db"
    calls = {"n": 0}
    real = f2.sha256_file

    def counting(path, chunk=1 << 20):
        calls["n"] += 1
        return real(path, chunk)

    patch_f2(monkeypatch, "sha256_file", counting)

    with f2.HashCache(db) as cache:
        for item in files:
            cache.digest(item.path)
    assert calls["n"] == len(files)
    assert db.exists()

    with f2.HashCache(db) as again:
        for item in files:
            again.digest(item.path)
        stats = again.stats()
    assert calls["n"] == len(files), "第二次运行不应再算哈希"
    assert stats["computed"] == 0
    assert stats["hit"] == len(files)
    assert stats["cached_rows"] == len(files)



def test_hash_cache_invalidates_on_change(tmp_path):
    """文件被改写（size/mtime 变化）时必须重算，绝不能返回过期摘要。"""
    path = tmp_path / "a.webp"
    path.write_bytes(b"first")
    db = tmp_path / "hash.db"

    with f2.HashCache(db) as cache:
        first = cache.digest(path)
    path.write_bytes(b"second-content")
    with f2.HashCache(db) as cache:
        second = cache.digest(path)
        assert cache.stats()["computed"] == 1
    assert first != second
    assert second == f2.sha256_file(path)



def test_hash_cache_tolerates_broken_file(tmp_path):
    """缓存文件损坏时退化為「不用缓存」，导入照常（缓存是纯优化，不能拖垮主流程）。"""
    files = _fake_tree(tmp_path)
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a sqlite file")

    cache, reason = f2.open_hash_cache(broken)
    assert cache is None and "哈希缓存不可用" in reason

    decisions, _skipped, _deferred, stats = f2.build_plan_with_cache(
        files, f2.DedupIndex(set(), set(), set(), set()), hash_cache_path=broken
    )
    assert sum(1 for d in decisions if d.action == "import") == 3  # 决策不受影响
    assert "哈希缓存不可用" in stats["error"]



def test_load_f2_authors(tmp_path):
    f2_dir = _f2_dir_with_authors(tmp_path)
    authors = f2.load_f2_authors(f2_dir)
    assert [a["nickname"] for a in authors] == ["里香1√", "娜娜瑜√"]
    assert authors[0]["sec_user_id"] == "MS4wLjABAAAAaaa"
    assert authors[0]["aweme_count"] == 171



def test_load_f2_authors_tolerates_missing_db_and_table(tmp_path):
    assert f2.load_f2_authors(tmp_path / "不存在") == []
    empty = tmp_path / "empty"
    empty.mkdir()
    sqlite3.connect(empty / f2.F2_AUTHOR_DB).close()  # 有库无表
    assert f2.load_f2_authors(empty) == []



def test_load_f2_video_authors_reads_mapping_and_tolerates_missing(tmp_path):
    """作品级映射可读；库/表缺失一律当空（实测本机长期没有这个库）。"""
    assert f2.load_f2_video_authors(tmp_path / "不存在") == {}

    f2_dir = _f2_dir_with_video_db(
        tmp_path, [("7624898240707091963", "MS4wLjABAAAAaaa", "1123木头人")]
    )
    assert f2.load_f2_video_authors(f2_dir) == {
        "7624898240707091963": {
            "sec_user_id": "MS4wLjABAAAAaaa",
            "nickname": "1123木头人",
        }
    }

    empty = tmp_path / "emptyvideos"
    empty.mkdir()
    sqlite3.connect(empty / f2.F2_VIDEO_DB).close()  # 有库无表
    assert f2.load_f2_video_authors(empty) == {}



def test_run_with_timeout_kills_long_command(tmp_path):
    """限时执行器：超时抛 ``TimeoutExpired``（子进程由 subprocess 杀掉，不留孤儿）。"""
    with pytest.raises(subprocess.TimeoutExpired):
        f2._run_with_timeout(
            [sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, 0.5
        )



def test_compute_fetch_interval_all_when_disabled():
    today = date(2026, 9, 10)
    assert f2.compute_fetch_interval(None, None, today) == "all"
    assert f2.compute_fetch_interval(0, None, today) == "all"
    assert f2.compute_fetch_interval(-1, datetime(2026, 9, 9), today) == "all"



def test_compute_fetch_interval_uses_minimum_window():
    """没有本地下载记录（新博主）时按最小窗口取；首次仍是增量而非全量重扫。"""
    today = date(2026, 9, 10)
    assert f2.compute_fetch_interval(14, None, today) == "2026-08-27|2026-09-10"
    # 昨天刚下过：仍保留 14 天最小宽度（覆盖上次列到但没下成功的作品）
    assert (
        f2.compute_fetch_interval(14, datetime(2026, 9, 9, 23, 50), today)
        == "2026-08-27|2026-09-10"
    )



def test_compute_fetch_interval_widens_to_last_download():
    """超过窗口天数没跑过时窗口自动放大到「上次下载日 - 1 天」，避免漏作品。"""
    today = date(2026, 9, 10)
    # 上次下载是 7-01 → 窗口起点取 6-30（比 14 天前更早）
    assert (
        f2.compute_fetch_interval(14, datetime(2026, 7, 1, 12, 0), today)
        == "2026-06-30|2026-09-10"
    )



def test_author_last_download_merges_variants_and_ignores_files(tmp_path):
    """作者目录 mtime = 上次为该作者下到东西的时间；同名变体（√）取最新。"""
    post_root = tmp_path / "post"
    (post_root / "不养羊").mkdir(parents=True)
    (post_root / "不养羊√").mkdir()
    (post_root / "随便一个文件.webp").write_bytes(b"x")  # 非目录忽略
    old, new = 1_700_000_000, 1_800_000_000
    os.utime(post_root / "不养羊", (old, old))
    os.utime(post_root / "不养羊√", (new, new))

    stamps = f2.author_last_download(post_root)

    assert set(stamps) == {"不养羊"}
    assert stamps["不养羊"] == datetime.fromtimestamp(new)
    assert f2.author_last_download(tmp_path / "不存在") == {}



def test_apply_rollback_removes_rows_files_and_links(tmp_path):
    db, storage, batch_file, ids = _imported_fixture(tmp_path)
    conn = sqlite3.connect(db)
    files = [r[0] for r in conn.execute("SELECT file_path FROM inspirations")]
    thumbs = [r[0] for r in conn.execute("SELECT thumbnail_path FROM inspirations")]
    conn.close()

    deletable, _kept = f2.plan_rollback(batch_file, db)
    result = f2.apply_rollback(deletable, db_path=db, storage_root=storage)

    assert result["deleted"] == 2 and result["failed"] == 0
    assert result["removed_files"] == 4  # 2 份素材文件 + 2 张缩略图
    assert all(not (storage / rel).exists() for rel in files + thumbs)

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM inspirations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM inspiration_bloggers").fetchone()[0] == 0
    conn.close()

    # 回滚后可重新导入（去重依据是库内是否还留有该内容）
    second, _skipped, _deferred = f2.build_import_plan(
        files=f2.scan_directory(tmp_path / "f2"),
        dedup=f2.load_dedup_index(db),
    )
    assert sum(1 for d in second if d.action == "import") == 2



def test_latest_batch_file(tmp_path):
    batch_dir = tmp_path / "batches"
    assert f2.latest_batch_file(batch_dir) is None  # 目录不存在
    batch_dir.mkdir()
    assert f2.latest_batch_file(batch_dir) is None  # 空目录
    (batch_dir / "f2-20260101-000000.json").write_text("{}", encoding="utf-8")
    (batch_dir / "f2-20260102-000000.json").write_text("{}", encoding="utf-8")
    assert f2.latest_batch_file(batch_dir).name == "f2-20260102-000000.json"



def test_batch_storage_root_reads_recorded_root(tmp_path):
    """修复（审查 M3）：回滚优先用清单记录的存储根。"""
    batch = tmp_path / "b.json"
    batch.write_text(
        json.dumps({"storage_root": "D:/other/storage", "imported": []}), encoding="utf-8"
    )
    assert f2.batch_storage_root(batch) == Path("D:/other/storage")

    batch.write_text("{}", encoding="utf-8")
    assert f2.batch_storage_root(batch) is None  # 未记录
    batch.write_text("不是 JSON", encoding="utf-8")
    assert f2.batch_storage_root(batch) is None  # 解析失败不抛



def test_rollback_deletes_row_before_file(tmp_path):
    """修复（审查 M2）：先删库再删文件——DB 删除失败时文件必须仍在。

    回归点：反过来的顺序会在 DB 失败后留下「记录还在、文件已丢」的悬空素材。
    """
    db, storage, batch_file, ids = _imported_fixture(tmp_path)
    conn = sqlite3.connect(db)
    rel = conn.execute("SELECT file_path FROM inspirations LIMIT 1").fetchone()[0]
    # 用触发器模拟「DB 删除失败」（plan 阶段的 SELECT 不受影响）
    conn.execute(
        "CREATE TRIGGER block_delete BEFORE DELETE ON inspirations "
        "BEGIN SELECT RAISE(ABORT, '删除被阻止'); END;"
    )
    conn.commit()
    conn.close()

    deletable, _kept = f2.plan_rollback(batch_file, db)
    assert deletable, "预览阶段应仍列出条目"
    result = f2.apply_rollback(deletable, db_path=db, storage_root=storage)

    assert result["deleted"] == 0 and result["failed"] == len(deletable)
    assert (storage / rel).exists(), "DB 删除失败时文件不得被提前删掉"



def test_parse_like_filename_keeps_original_author():
    """点赞产物在「我的昵称」目录下，原作者只能从文件名前缀还原。"""
    parsed = f2.parse_media_filename(
        Path("我的账号") / "不养羊_2026-09-14 10-31-14_下一站再见吧#地铁jk_#jk_image_1.webp",
        "我的账号",
    )

    assert parsed is not None
    assert parsed.author_dir == "不养羊"  # 不是目录名「我的账号」
    assert parsed.author_key == "不养羊"
    assert parsed.created == "2026-09-14 10-31-14"
    assert parsed.kind == "image" and parsed.index == 1
    assert parsed.caption == "下一站再见吧#地铁jk #jk"



def test_parse_like_filename_author_with_underscore():
    """作者名自带下划线（美羊羊桑_）时，前缀不能被切错。"""
    parsed = f2.parse_media_filename(
        Path("我的账号") / "美羊羊桑__2026-09-14 10-31-14_#jk_video.mp4", "我的账号"
    )

    assert parsed is not None
    assert parsed.author_dir == "美羊羊桑_"
    assert parsed.author_key == "美羊羊桑"  # 归一化去掉尾部下划线
    assert parsed.kind == "video"



def test_post_and_like_naming_share_work_key_and_platform_id():
    """同一作品从主页与「我的喜欢」两条路进来，作品键与平台 ID 必须一致（判重根基）。"""
    post = f2.parse_media_filename(
        Path("不养羊") / "2026-09-14 10-31-14_下一站再见吧#地铁jk_#jk_image_1.webp", "不养羊"
    )
    like = f2.parse_media_filename(
        Path("我的账号") / "不养羊_2026-09-14 10-31-14_下一站再见吧#地铁jk_#jk_image_1.webp",
        "我的账号",
    )

    assert post is not None and like is not None
    assert post.work_key == like.work_key
    assert f2.platform_id_for(post) == f2.platform_id_for(like)



def test_parse_media_filename_post_naming_unchanged():
    """发布模式命名（无作者前缀）行为不变：作者仍取自目录名。"""
    parsed = f2.parse_media_filename(
        Path("里香1√") / "2025-01-01 10-00-00_标题_image_2.webp", "里香1√"
    )

    assert parsed is not None
    assert parsed.author_dir == "里香1√"
    assert parsed.author_key == "里香"
    assert parsed.index == 2



def test_like_user_url_normalizes():
    assert f2.like_user_url("") == ""
    assert f2.like_user_url("  MS4wLjABAAAAxyz  ") == "https://www.douyin.com/user/MS4wLjABAAAAxyz"
    url = "https://www.douyin.com/user/MS4wLjABAAAAxyz"
    assert f2.like_user_url(url) == url



@pytest.mark.parametrize(
    "raw, expected",
    [
        (f"https://www.douyin.com/user/{SEC}", SEC),
        # 分享链接常带 query
        (f"https://www.douyin.com/user/{SEC}?from_tab_name=main", SEC),
        # 尾斜杠
        (f"https://www.douyin.com/user/{SEC}/", SEC),
        (SEC, SEC),
        (f"  {SEC}  ", SEC),
    ],
)
def test_profile_author_accepts_url_and_sec_id(raw, expected):
    parsed = f2.profile_author(raw)

    assert parsed is not None
    assert parsed["sec_user_id"] == expected
    assert parsed["nickname"] == ""  # 昵称要等 f2 登记后才知道



@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "72906514384",  # 抖音号：拼不出主页地址
        "v.douyin.com/iABCdef/",  # 短链：要先跟一次跳转
        "https://www.douyin.com/user/",
        "https://www.douyin.com/user/not-a-sec-uid",
    ],
)
def test_profile_author_rejects_unsupported_input(raw):
    """不支持的输入返回 None，由调用方明确报错——不拼一个必然失败的 URL。"""
    assert f2.profile_author(raw) is None



def test_profile_display_uses_sec_prefix_when_nickname_unknown():
    assert f2.profile_display({"sec_user_id": SEC, "nickname": "唐思瑶ya"}) == "唐思瑶ya"
    assert "MS4wLjABAAAA" in f2.profile_display({"sec_user_id": SEC, "nickname": ""})



def test_compute_profile_interval_first_time_is_all():
    """点名博主首次必须全量——否则「下她全部作品」只会拿到最近 N 天。"""
    today = date(2026, 9, 10)

    assert f2.compute_profile_interval(14, None, today) == "all"
    # 已经下过：退回窗口增量，且按「上次下载日 -1 天」放大（长时间不跑也不漏）
    assert (
        f2.compute_profile_interval(14, datetime(2026, 7, 1), today)
        == "2026-06-30|2026-09-10"
    )
    # 与批量增量的口径故意不同：后者对新博主也给最小窗口（避免一次翻几十个作者的历史）
    assert f2.compute_fetch_interval(14, None, today) == "2026-08-27|2026-09-10"



def test_resolve_profile_nicknames_matches_by_sec_id(tmp_path):
    """下载后按 sec_user_id 反查昵称——入库白名单按昵称匹配，缺它就是「下载成功、入库 0」。"""
    f2_dir = _f2_dir_with_authors(tmp_path, authors=[(SEC, "唐思瑶ya", 1582)])

    assert f2.resolve_profile_nicknames(f2_dir, [SEC]) == {SEC: "唐思瑶ya"}
    assert f2.resolve_profile_nicknames(f2_dir, ["MS4wLjABAAAAmissing"]) == {}
    assert f2.resolve_profile_nicknames(f2_dir, []) == {}



def test_parse_new_naming_extracts_aweme_id():
    parsed = f2.parse_media_filename(Path("A") / _new_name("标题"), "A")

    assert parsed is not None
    assert parsed.aweme_id == AWEME
    assert parsed.body == "标题"           # 作品 ID 不能混进正文
    assert parsed.kind == "image"
    assert parsed.index == 1
    assert parsed.created == "2025-01-01 10-00-00"
    assert parsed.author_dir == "A"        # 发布模式作者仍取自目录名



def test_parse_new_naming_with_author_prefix_and_underscore_in_body():
    """点赞模式：作者前缀 + 正文含下划线 + 正文含数字，都要切对。"""
    parsed = f2.parse_media_filename(
        Path("我的账号")
        / f"不养羊_2025-01-01 10-00-00_下一站再见吧_#jk_2024_{AWEME}_image_3.webp",
        "我的账号",
    )

    assert parsed is not None
    assert parsed.author_dir == "不养羊"
    assert parsed.aweme_id == AWEME
    assert parsed.body == "下一站再见吧_#jk_2024"
    assert parsed.index == 3



def test_parse_new_naming_with_empty_body():
    """正文为空的作品（f2 的 {desc} 可为空）必须仍按新命名解析。

    回归：此前新正则要求正文非空，`{create}__{aweme_id}_{kind}`（连续两个下划线）
    落回旧正则被当成「正文里含作品 ID 的旧文件」——作品 ID 与原帖链接静默丢失。
    """
    parsed = f2.parse_media_filename(
        Path("A") / f"2025-01-01 10-00-00__{AWEME}_video.mp4", "A"
    )

    assert parsed is not None
    assert parsed.aweme_id == AWEME
    assert parsed.body == ""
    assert f2.source_url_for(parsed) == f"https://www.douyin.com/video/{AWEME}"
    assert f2.platform_id_for(parsed) == f"f2:{AWEME}#video"



def test_old_naming_body_ending_with_digits_still_uses_old_regex():
    """正文与作品 ID 之间没有分隔下划线时不误判为新命名（放宽 body 的守界）。"""
    parsed = f2.parse_media_filename(
        Path("A") / f"2025-01-01 10-00-00_正文{AWEME}_image_1.webp", "A"
    )

    assert parsed is not None
    assert parsed.aweme_id == ""            # 数字紧贴正文 → 不是作品 ID
    assert parsed.body == f"正文{AWEME}"



def test_parse_old_naming_still_works():
    """旧命名的历史文件必须照旧能解析（Download/ 目录新旧混放）。"""
    parsed = f2.parse_media_filename(
        Path("里香1√") / "2025-01-01 10-00-00_标题_image_2.webp", "里香1√"
    )

    assert parsed is not None
    assert parsed.aweme_id == ""           # 旧命名没有作品 ID
    assert parsed.body == "标题"
    assert parsed.index == 2
    assert parsed.author_key == "里香"



def test_new_regex_wins_over_old():
    """回归：新命名若被旧正则解析，作品 ID 会混进正文 → 同一作品两个作品键。

    这是「先新后旧」尝试顺序的理由，必须锁死。
    """
    parsed = f2.parse_media_filename(Path("A") / _new_name("标题"), "A")

    assert parsed.aweme_id == AWEME
    assert AWEME not in parsed.body



def test_platform_id_uses_real_aweme_id():
    """新口径：身份来自作品本身，与文件名无关。"""
    parsed = f2.parse_media_filename(Path("A") / _new_name("标题", "image_1"), "A")
    video = f2.parse_media_filename(
        Path("A") / _new_name("标题", "video").replace(".webp", ".mp4"), "A"
    )

    assert f2.platform_id_for(parsed) == f"f2:{AWEME}#image1"
    assert f2.platform_id_for(video) == f"f2:{AWEME}#video"
    assert f2.platform_id_for(parsed) != f2.platform_id_for(video)



def test_platform_id_survives_rename():
    """新口径的关键收益：改名/移动不再改变平台 ID（旧口径会变）。"""
    a = f2.parse_media_filename(Path("A") / _new_name("标题"), "A")
    b = f2.parse_media_filename(Path("A") / _new_name("标题"), "A")
    assert f2.platform_id_for(a) == f2.platform_id_for(b)

    old_a = f2.parse_media_filename(Path("A") / "2025-01-01 10-00-00_标题_image_1.webp", "A")
    old_b = f2.parse_media_filename(Path("A") / "2025-01-01 10-00-00_改过的标题_image_1.webp", "A")
    assert f2.platform_id_for(old_a) != f2.platform_id_for(old_b)



def test_platform_ids_for_new_file_returns_both_schemes():
    """新文件要同时认「作品 ID」与「旧哈希 ID」两套（库里历史素材存的是后者）。"""
    parsed = f2.parse_media_filename(Path("A") / _new_name("标题"), "A")

    ids = f2.platform_ids_for(parsed)
    assert ids[0] == f"f2:{AWEME}#image1"
    assert len(ids) == 2
    assert ids[1] == f2.legacy_platform_id_for(parsed)
    assert ids[1].startswith("f2:") and ids[1].endswith("#image1")



def test_platform_ids_for_old_file_single_scheme():
    """旧文件只有一套 ID（拿不到作品 ID），去重集合不应重复。"""
    parsed = f2.parse_media_filename(Path("A") / "2025-01-01 10-00-00_标题_image_1.webp", "A")

    ids = f2.platform_ids_for(parsed)
    assert len(ids) == 1
    assert ids[0] == f2.platform_id_for(parsed) == f2.legacy_platform_id_for(parsed)



@pytest.mark.parametrize(
    "kind, suffix, expected_path",
    [
        ("image_1", ".webp", "note"),
        ("live_1", ".mp4", "note"),   # 图集里的 live 分段仍属图集作品
        ("video", ".mp4", "video"),
    ],
)
def test_source_url_by_work_kind(kind, suffix, expected_path):
    """原帖链接按**作品类型**判断，不是 media_type（live 入库是 video 但走 /note/）。"""
    name = _new_name("标题", kind).replace(".webp", suffix)
    parsed = f2.parse_media_filename(Path("A") / name, "A")

    assert f2.source_url_for(parsed) == f"https://www.douyin.com/{expected_path}/{AWEME}"



@pytest.mark.parametrize("kind, expected", [("cover", "note"), ("image_1", "note")])
def test_source_url_cover_and_image_are_note(kind, expected):
    parsed = f2.parse_media_filename(Path("A") / _new_name("标题", kind), "A")
    assert f2.source_url_for(parsed) == f"https://www.douyin.com/{expected}/{AWEME}"



def test_source_url_none_without_aweme_id():
    """没有作品 ID 就留空——绝不造一个打不开的伪链接。"""
    parsed = f2.parse_media_filename(Path("A") / "2025-01-01 10-00-00_标题_image_1.webp", "A")
    assert f2.source_url_for(parsed) is None



def test_f2_accepts_our_naming_templates():
    """回归（P0）：模板若被 f2 的校验器拒绝，整条下载命令直接失败。

    f2 只允许 {nickname}/{create}/{aweme_id}/{desc}/{uid} + 分隔符 `-`/`_`，
    改模板后必须过这一关（实测 invalid=[]）。
    """
    pytest.importorskip("f2", reason="需要 f2 本体校验命名模板契约")
    from f2.apps.douyin.cli import check_invalid_naming

    allowed = ["{nickname}", "{create}", "{aweme_id}", "{desc}", "{uid}"]
    separators = ["-", "_"]

    assert check_invalid_naming(f2.POST_NAMING_TEMPLATE, allowed, separators) == []
    assert check_invalid_naming(f2.LIKE_NAMING_TEMPLATE, allowed, separators) == []



@pytest.mark.parametrize("kind_suffix", ["_image_1.webp", "_video.mp4", "_live_1.mp4"])
def test_f2_generated_filename_round_trips(kind_suffix):
    """回归（P0）：模板 → f2 生成文件名 → 我方解析，必须拿回真实作品 ID。

    此前只用「手写的假文件名」测正则，没验证过 **f2 真实产物**；这里调 f2 自己的
    format_file_name 生成文件名，再走 parse_media_filename，锁死真实契约。
    """
    pytest.importorskip("f2", reason="需要 f2 本体生成真实产物名")
    from f2.apps.douyin.utils import format_file_name
    from f2.utils.utils import replaceT

    created = "2026-09-14 10-31-14"
    aweme = "7412345678901234567"
    raw_desc = "#jk 穿搭 分享"

    stem = format_file_name(
        f2.POST_NAMING_TEMPLATE,
        {"create_time": created, "aweme_id": aweme, "desc": replaceT(raw_desc)},
    )
    # f2 在模板结果后面自己拼 `_{类型}[_{序号}]`（见 f2/apps/douyin/dl.py）
    parsed = f2.parse_media_filename(Path("A") / f"{stem}{kind_suffix}", "A")

    assert parsed is not None
    assert parsed.aweme_id == aweme
    assert parsed.created == created
    assert parsed.body == "#jk_穿搭_分享"  # 作品 ID 没混进正文
    assert f2.platform_id_for(parsed).startswith(f"f2:{aweme}#")



def test_f2_generated_like_filename_round_trips():
    """点赞模式同理（带作者前缀）。"""
    pytest.importorskip("f2", reason="需要 f2 本体生成真实产物名")
    from f2.apps.douyin.utils import format_file_name
    from f2.utils.utils import replaceT

    created = "2026-09-14 10-31-14"
    aweme = "7412345678901234567"
    stem = format_file_name(
        f2.LIKE_NAMING_TEMPLATE,
        {
            "create_time": created,
            "aweme_id": aweme,
            "desc": replaceT("#jk 穿搭"),
            "nickname": "不养羊",
        },
    )
    parsed = f2.parse_media_filename(
        Path("我的账号") / f"{stem}_image_1.webp", "我的账号"
    )

    assert parsed is not None
    assert parsed.author_dir == "不养羊"  # 作者来自文件名前缀，不是目录名
    assert parsed.aweme_id == aweme
    assert parsed.body == "#jk_穿搭"



def test_source_url_cover_of_video_work_goes_to_video():
    """回归：视频作品的封面（kind=cover）必须走 /video/，只按 kind 会写成 /note/ 打不开。"""
    cover = f2.parse_media_filename(
        Path("A") / _new_name("标题", "cover").replace(".webp", ".jpg"), "A"
    )
    assert cover.kind == "cover"

    # 不传作品类型：退化为按 kind 判断（/note/）
    assert f2.source_url_for(cover) == f"https://www.douyin.com/note/{AWEME}"
    # 传了「同作品有 video 文件」：纠正为 /video/
    assert (
        f2.source_url_for(cover, is_video_work=True)
        == f"https://www.douyin.com/video/{AWEME}"
    )

