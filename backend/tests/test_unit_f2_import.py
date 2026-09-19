"""f2 下载目录扫描/报表单元测试（scripts/import_f2_downloads.py）。

不触碰真实素材库：库内哈希与博主用临时 sqlite 或直接注入，目录用 tmp_path 构造。
"""

import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from scripts import import_f2_downloads as f2
from scripts.scraper_common import utcnow


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


# ── 报表 ──


def _fake_tree(tmp_path: Path) -> list[f2.ParsedFile]:
    _write(tmp_path / "里香1√" / "2025-01-01 10-00-00_#jk_#穿搭_image_1.webp", b"img1")
    _write(tmp_path / "里香1√" / "2025-01-01 10-00-00_#jk_#穿搭_image_2.webp", b"img2")
    _write(tmp_path / "未知账号" / "2025-02-02 11-00-00_#通勤_video.mp4", b"vid")
    return f2.scan_directory(tmp_path)


def test_build_report_counts_new_vs_in_library(tmp_path):
    files = _fake_tree(tmp_path)
    img1 = next(f for f in files if f.path.name.endswith("image_1.webp"))
    # 只有第一张图已在库
    dedup = f2.DedupIndex(
        live_hashes={f2.sha256_file(img1.path)},
        trash_hashes=set(),
        live_platform_ids=set(),
        trash_platform_ids=set(),
    )

    report = f2.build_report(
        root=tmp_path,
        files=files,
        dedup=dedup,
        bloggers={"里香": [{"id": 302, "name": "里香"}]},
    )

    assert report["files_total"] == 3
    assert report["files_new"] == 2
    assert report["files_in_library"] == 1
    assert report["files_in_trash"] == 0
    assert report["works_with_new"] == 2  # 图集作品有净新增、视频作品也是新的
    assert report["works_all_in_library"] == 0
    assert report["kind_stat"]["image"] == {"净新增": 1, "已入库": 1}
    assert report["kind_stat"]["video"] == {"净新增": 1}
    # 图集作品含 2 张图 → 落在 2-4 张档
    assert report["gallery_dist"]["2-4 张"] == 1
    assert report["gallery_dist"]["纯视频/实况（无图）"] == 1


def test_build_report_separates_trash(tmp_path):
    """垃圾桶内容不计入「净新增」（否则报表口径与导入计划不一致）。"""
    files = _fake_tree(tmp_path)
    img1 = next(f for f in files if f.path.name.endswith("image_1.webp"))
    dedup = f2.DedupIndex(
        live_hashes=set(),
        trash_hashes={f2.sha256_file(img1.path)},
        live_platform_ids=set(),
        trash_platform_ids=set(),
    )

    report = f2.build_report(root=tmp_path, files=files, dedup=dedup, bloggers={})

    assert report["files_new"] == 2  # 剩下 2 个文件才是净新增
    assert report["files_in_trash"] == 1
    assert report["works_with_trash"] == 1
    assert report["kind_stat"]["image"] == {"净新增": 1, "已在垃圾桶": 1}
    row = next(a for a in report["authors"] if a["author_dir"] == "里香1√")
    assert row["trash"] == 1 and row["new"] == 1


def test_build_report_matches_blogger_and_lists_unmatched(tmp_path):
    files = _fake_tree(tmp_path)
    report = f2.build_report(
        root=tmp_path,
        files=files,
        dedup=f2.DedupIndex(set(), set(), set(), set()),
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
        dedup=f2.DedupIndex(set(), set(), set(), set()),
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
    f2.build_report(tmp_path, files, f2.DedupIndex(set(), set(), set(), set()), {}, cache)
    assert len(cache) == 3


# ── 素材库侧读取（临时 sqlite）──


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


# ── 平台 ID 合成（受 source_platform_id 全局唯一索引约束）──


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


# ── 导入计划：五层去重 ──


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


def test_plan_skips_content_already_in_library(tmp_path):
    files = _fake_tree(tmp_path)
    img1 = next(f for f in files if f.path.name.endswith("image_1.webp"))
    decisions, skipped, _ = _decisions(files, library_hashes={f2.sha256_file(img1.path)})
    by_name = {d.item.path.name: d for d in decisions}
    assert by_name[img1.path.name].action == "skip"
    assert by_name[img1.path.name].reason == "已在库（内容相同）"
    assert skipped["已在库（内容相同）"] == 1
    assert sum(1 for d in decisions if d.action == "import") == 2


def test_plan_skips_duplicate_content_within_batch(tmp_path):
    """同一内容在一次导入里出现两次（重复下载/多目录）只入一次。"""
    a = _write(tmp_path / "A" / "2025-01-01 10-00-00_标题_image_1.webp", b"same")
    _write(tmp_path / "B" / "2025-01-01 10-00-00_标题_image_1.webp", b"same")
    files = f2.scan_directory(tmp_path)
    decisions, skipped, _ = _decisions(files)
    imported = [d for d in decisions if d.action == "import"]
    assert len(imported) == 1
    assert skipped["批次内重复（同内容已处理）"] == 1
    assert a  # 两个来源目录都在扫描结果里
    assert {d.item.author_dir for d in decisions} == {"A", "B"}


def test_plan_skips_when_platform_id_exists(tmp_path):
    """幂等兜底：内容哈希口径变化时，平台 ID 命中也能挡住重复入库。"""
    files = _fake_tree(tmp_path)
    ids = {f2.platform_id_for(f) for f in files}
    decisions, skipped, _ = _decisions(files, platform_ids=ids)
    assert all(d.action == "skip" for d in decisions)
    assert skipped["已在库（平台 ID 命中）"] == 3


# ── 垃圾桶判重（P0：垃圾桶是负样本，不该被重新导入）──


def test_plan_skips_content_in_trash(tmp_path):
    """回归：用户丢进垃圾桶的内容，再次导入时必须跳过（不是重新入库）。

    背景：此前判重只看未删除素材（deleted_at IS NULL），垃圾桶内容会被原样搬回来。
    手动时代偶尔撞上，开了「每日自动获取」就是每天自动复活一次。
    """
    files = _fake_tree(tmp_path)
    img1 = next(f for f in files if f.path.name.endswith("image_1.webp"))

    decisions, skipped, _ = _decisions(
        files, trash_hashes={f2.sha256_file(img1.path)}
    )
    by_name = {d.item.path.name: d for d in decisions}
    assert by_name[img1.path.name].action == "skip"
    assert by_name[img1.path.name].reason == f2.TRASH_SKIP_REASON
    assert skipped[f2.TRASH_SKIP_REASON] == 1
    assert sum(1 for d in decisions if d.action == "import") == 2


def test_plan_skips_platform_id_in_trash(tmp_path):
    """垃圾桶里的平台 ID 命中同样跳过（哈希口径变化时的兜底）。"""
    files = _fake_tree(tmp_path)
    ids = {f2.platform_id_for(files[0])}
    decisions, skipped, _ = _decisions(files, trash_platform_ids=ids)
    assert skipped[f2.TRASH_SKIP_REASON] == 1
    assert sum(1 for d in decisions if d.action == "import") == 2


def test_plan_live_wins_over_trash(tmp_path):
    """同一内容既有在库记录又在垃圾桶时，按「已在库」计数（判据顺序固定）。"""
    files = _fake_tree(tmp_path)
    digest = f2.sha256_file(files[0].path)
    _d, skipped, _ = _decisions(files, library_hashes={digest}, trash_hashes={digest})
    assert skipped["已在库（内容相同）"] == 1
    assert f2.TRASH_SKIP_REASON not in skipped


def test_plan_tolerates_file_deleted_after_scan(tmp_path):
    """扫描后文件消失不能让整批计划崩溃：跳过该文件并单独计数。

    回归点：下载目录是「活的」（f2 在写、用户可能清理），原先哈希抛
    FileNotFoundError 会让整个 build_import_plan 失败 → 整次导入任务失败。
    """
    files = _fake_tree(tmp_path)
    gone = next(f for f in files if f.path.name.endswith("image_1.webp"))
    gone.path.unlink()

    decisions, skipped, _ = _decisions(files)

    assert skipped["文件读取失败（扫描后消失？）"] == 1
    assert all(d.item.path != gone.path for d in decisions)
    # 同作品的另一张图与另一作者的视频照常入库
    assert sum(1 for d in decisions if d.action == "import") == 2


def test_build_report_counts_read_failures(tmp_path):
    """报表同样容错：单文件读不出来只计数，不让整张报表崩掉。"""
    files = _fake_tree(tmp_path)
    next(f for f in files if f.path.name.endswith("video.mp4")).path.unlink()

    report = f2.build_report(
        root=tmp_path,
        files=files,
        dedup=f2.DedupIndex(
            live_hashes=set(),
            trash_hashes=set(),
            live_platform_ids=set(),
            trash_platform_ids=set(),
        ),
        bloggers={},
    )

    assert report["files_read_failed"] == 1
    assert report["files_new"] == 2
    assert report["files_total"] == 3


# ── 已登记博主白名单（f2 用户库混进无关账号时的过滤依据）──


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
    monkeypatch.setattr(f2, "library_db_path", lambda: db)

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


def test_run_fetch_skips_unregistered_authors(tmp_path, monkeypatch):
    """回归：一键/CLI 下载默认跳过未登记账号，不再把官方号的作品拉进下载目录。"""
    f2_dir = tmp_path / "f2proj"
    f2_dir.mkdir()
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.executemany(
        "INSERT INTO user_info_web VALUES (?, ?, 1)",
        [("MS4x_lixiang", "里香1√"), ("MS4x_game", "网易第五人格")],
    )
    conn.commit()
    conn.close()

    db = tmp_path / "lib.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE bloggers (id INTEGER PRIMARY KEY, name TEXT, platform TEXT, "
        "platform_user_id TEXT)"
    )
    conn.execute("INSERT INTO bloggers VALUES (1, '里香', 'douyin', 'MS4x_lixiang')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(f2, "library_db_path", lambda: db)

    commands: list[str] = []
    result = f2.run_fetch(
        f2_dir=f2_dir,
        download_root=tmp_path / "Download",
        runner=lambda cmd, cwd: (commands.append(cmd[-1]) or (0, "")),
    )

    assert result["total"] == 1  # 只下 里香1√
    assert result["skipped_authors"] == ["网易第五人格"]
    assert all("网易第五人格" not in cmd for cmd in commands)

    # 显式放开：两个账号都下
    commands.clear()
    forced = f2.run_fetch(
        f2_dir=f2_dir,
        download_root=tmp_path / "Download",
        runner=lambda cmd, cwd: (commands.append(cmd[-1]) or (0, "")),
        include_unknown=True,
    )
    assert forced["total"] == 2 and forced["skipped_authors"] == []


# ── 哈希缓存（P0：避免每次运行重算整棵下载树的 SHA-256）──


def test_hash_cache_reuses_digest_across_runs(tmp_path, monkeypatch):
    """回归：同一文件第二次运行不再读盘算哈希（首次 7.28 GB 约 81 秒的成本只付一次）。"""
    files = _fake_tree(tmp_path)
    db = tmp_path / "hash.db"
    calls = {"n": 0}
    real = f2.sha256_file

    def counting(path, chunk=1 << 20):
        calls["n"] += 1
        return real(path, chunk)

    monkeypatch.setattr(f2, "sha256_file", counting)

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


def test_plan_with_cache_reports_stats(tmp_path):
    """build_plan_with_cache：第一次实算，第二次全命中（任务结果里的哈希成本数字）。"""
    files = _fake_tree(tmp_path)
    db = tmp_path / "hash.db"
    empty = f2.DedupIndex(set(), set(), set(), set())

    _d1, _s1, _f1, stats1 = f2.build_plan_with_cache(files, empty, hash_cache_path=db)
    _d2, _s2, _f2, stats2 = f2.build_plan_with_cache(files, empty, hash_cache_path=db)

    assert stats1["computed"] == 3 and stats1["hit"] == 0
    assert stats2["computed"] == 0 and stats2["hit"] == 3
    # 关掉缓存时不做任何缓存读写
    _d3, _s3, _f3, stats3 = f2.build_plan_with_cache(
        files, empty, hash_cache_path=db, use_cache=False
    )
    assert stats3 == {}


def test_plan_skip_live_and_author_filter(tmp_path):
    _write(tmp_path / "里香1√" / "2025-01-01 10-00-00_#jk_live_1.mp4", b"live")
    _write(tmp_path / "里香1√" / "2025-01-01 10-00-00_#jk_image_1.webp", b"img")
    _write(tmp_path / "别的博主" / "2025-02-02 11-00-00_#通勤_video.mp4", b"vid")
    files = f2.scan_directory(tmp_path)

    decisions, skipped, _ = _decisions(files, skip_live=True)
    assert skipped["按 --skip-live 跳过 live 分段"] == 1

    decisions, skipped, _ = _decisions(files, authors={"里香"})
    imported = {d.item.author_dir for d in decisions if d.action == "import"}
    assert imported == {"里香1√"}
    assert skipped["作者不在指定范围（--authors / 已登记博主）"] == 1
    # 目录名直接传入同样生效
    decisions, _, _ = _decisions(files, authors={"别的博主"})
    assert {d.item.author_dir for d in decisions if d.action == "import"} == {"别的博主"}


def test_plan_limit_counts_works_not_files(tmp_path):
    """--limit 的配额按「作品」计：一个图集作品只吃一个配额。

    目录名用 A/Z 前缀保证扫描顺序确定（中文目录名的排序不直观）。
    """
    _write(tmp_path / "A账号" / "2025-01-01 10-00-00_#jk_image_1.webp", b"g1")
    _write(tmp_path / "A账号" / "2025-01-01 10-00-00_#jk_image_2.webp", b"g2")
    _write(tmp_path / "Z账号" / "2025-02-02 11-00-00_#通勤_video.mp4", b"v1")
    files = f2.scan_directory(tmp_path)

    decisions, skipped, deferred = _decisions(files, limit=1)
    assert sum(1 for d in decisions if d.action == "import") == 2  # 图集 2 张＝1 个配额
    assert deferred == 1
    assert skipped["超出 --limit 未处理"] == 1


def test_plan_binds_blogger_only_when_unique(tmp_path):
    files = _fake_tree(tmp_path)
    one = _decisions(files, bloggers={"里香": [{"id": 302, "name": "里香"}]})[0]
    assert {d.blogger_id for d in one if d.item.author_dir == "里香1√"} == {302}

    many = _decisions(
        files,
        bloggers={"里香": [{"id": 302, "name": "里香"}, {"id": 303, "name": "里香2√"}]},
    )[0]
    assert {d.blogger_id for d in many if d.item.author_dir == "里香1√"} == {None}


# ── 真导入（临时 storage + 临时库，图片用真实 JPEG 以通过类型校验）──


def _jpeg(path: Path, color: str = "red") -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 48), color).save(path, "JPEG")
    return path


def _fake_mp4(path: Path) -> Path:
    """带 ftyp 魔数的最小 mp4 头：够过 validate_media 的类型粗检（不解码）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41")
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


def test_apply_import_writes_material_files_and_rows(tmp_path):
    """端到端：复制文件 → 建行（含正文/来源/平台 ID）→ 缩略图 → 博主关联。

    按方案 A：不写话题存档表，#话题 随 caption 落库（可被文本向量/语义搜索命中）。
    """
    root = tmp_path / "f2"
    _jpeg(root / "里香1√" / "2025-01-01 10-00-00_#jk_#穿搭_image_1.jpg")
    _jpeg(root / "里香1√" / "2025-01-01 10-00-00_#jk_#穿搭_image_2.jpg", "blue")
    files = f2.scan_directory(root)
    db = _import_lib(tmp_path)
    storage = tmp_path / "storage"

    decisions, _, _ = _decisions(files, bloggers={"里香": [{"id": 302, "name": "里香"}]})
    result = f2.apply_import(
        [d for d in decisions if d.action == "import"],
        db_path=db,
        storage_root=storage,
    )

    assert result["imported"] == 2 and result["failed"] == 0
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT source_type, source_author, source_platform_id, file_path, "
        "thumbnail_path, media_type, quality_status, content_hash, caption, source_url "
        "FROM inspirations"
    ).fetchall()
    assert len(rows) == 2
    for row in rows:
        assert row[0] == "douyin" and row[1] == "里香" and row[5] == "image"
        assert row[6] == "pending"  # 采集类素材默认待质量审核
        assert row[7]  # content_hash 已写入（去重主判据）
        assert row[8] == "#jk #穿搭"  # 正文保留 #话题（不再单独写话题表）
        assert row[9] is None  # source_url 留空（f2 无 aweme_id，不造伪链接）
    assert len({r[2] for r in rows}) == 2  # 平台 ID 全局唯一
    assert all(r[3].startswith("images/") for r in rows)
    assert all(r[4] for r in rows)  # 缩略图已生成
    assert (storage / rows[0][3]).exists()

    assert conn.execute("SELECT COUNT(*) FROM inspiration_bloggers").fetchone()[0] == 2
    # 不写话题存档表（方案 A）
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "scraper_hashtags" not in tables
    conn.close()

    # 批次清单落盘（可审计/可回滚）
    assert result["batch_file"]
    batch = json.loads(Path(result["batch_file"]).read_text(encoding="utf-8"))
    assert len(batch["imported"]) == 2
    assert batch["imported"][0]["platform_id"].startswith("f2:")
    assert batch["imported"][0]["hashtags"] == ["jk", "穿搭"]  # 仅记录，不落表


def test_apply_import_is_idempotent(tmp_path):
    """重复运行：第二次全部被内容判重挡下，不产生新行。"""
    root = tmp_path / "f2"
    _jpeg(root / "A" / "2025-01-01 10-00-00_标题_image_1.jpg")
    db = _import_lib(tmp_path)
    storage = tmp_path / "storage"

    first, _, _ = _decisions(f2.scan_directory(root))
    f2.apply_import([d for d in first if d.action == "import"], db_path=db, storage_root=storage)

    # 用更新后的库状态重算计划
    second, skipped, _ = f2.build_import_plan(
        files=f2.scan_directory(root),
        dedup=f2.load_dedup_index(db),
    )
    assert [d for d in second if d.action == "import"] == []
    assert skipped["已在库（内容相同）"] == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM inspirations").fetchone()[0] == 1
    conn.close()


def test_apply_import_records_failure_without_breaking_batch(tmp_path):
    """非法文件（伪造扩展名）只跳过它自己，其余照常入库。"""
    root = tmp_path / "f2"
    _jpeg(root / "A" / "2025-01-01 10-00-00_好图_image_1.jpg")
    bad = root / "A" / "2025-01-02 10-00-00_坏图_image_1.jpg"
    bad.write_bytes(b"this is not an image")
    db = _import_lib(tmp_path)

    decisions, _, _ = _decisions(f2.scan_directory(root))
    result = f2.apply_import(
        [d for d in decisions if d.action == "import"],
        db_path=db,
        storage_root=tmp_path / "storage",
    )
    assert result["imported"] == 1
    assert result["failed"] == 1
    assert "坏图" in result["errors"][0]["source_file"]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM inspirations").fetchone()[0] == 1
    conn.close()


def test_apply_import_no_thumbnails(tmp_path):
    root = tmp_path / "f2"
    _jpeg(root / "A" / "2025-01-01 10-00-00_标题_image_1.jpg")
    db = _import_lib(tmp_path)
    decisions, _, _ = _decisions(f2.scan_directory(root))
    f2.apply_import(
        [d for d in decisions if d.action == "import"],
        db_path=db,
        storage_root=tmp_path / "storage",
        make_thumbnails=False,
    )
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT thumbnail_path FROM inspirations").fetchone()[0] is None
    conn.close()


# ── 调 f2 增量下载（--fetch；用注入的执行器，不真跑 f2）──


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


def test_build_f2_command_default_flags():
    """默认命令：主页作品 + 全部日期 + 指定下载根 + **带作品 ID 的命名模板**。"""
    author = {"sec_user_id": "MS4wLjABAAAAaaa", "nickname": "里香"}
    cmd = f2.build_f2_command(author, download_root=Path("D:/f2/Download"))

    assert cmd[1:4] == ["-m", "f2", "dy"]
    assert "-u" in cmd and cmd[cmd.index("-u") + 1] == "https://www.douyin.com/user/MS4wLjABAAAAaaa"
    assert cmd[cmd.index("-M") + 1] == "post"
    assert cmd[cmd.index("-i") + 1] == "all"
    assert cmd[cmd.index("-p") + 1] == str(Path("D:/f2/Download"))
    assert "--auto-cookie" not in cmd
    # 缺省必须显式传 -n 且含 {aweme_id}：不传时 f2 用配置里的 {create}_{desc}，
    # 新素材会继续丢失真实作品 ID（无法回填原帖链接）
    assert cmd[cmd.index("-n") + 1] == f2.POST_NAMING_TEMPLATE
    assert "{aweme_id}" in f2.POST_NAMING_TEMPLATE


def test_build_f2_command_optional_flags():
    author = {"sec_user_id": "sec1", "nickname": "A"}
    cmd = f2.build_f2_command(author, naming="{create}_{desc}", auto_cookie="chrome")
    assert cmd[cmd.index("-n") + 1] == "{create}_{desc}"
    assert cmd[cmd.index("--auto-cookie") + 1] == "chrome"


def test_build_f2_command_passes_date_window():
    """日期窗口必须真的进命令行：f2 在 `-i all` 时不会提前结束翻页。"""
    author = {"sec_user_id": "sec1", "nickname": "A"}
    cmd = f2.build_f2_command(author, interval="2026-08-27|2026-09-10")
    assert cmd[cmd.index("-i") + 1] == "2026-08-27|2026-09-10"
    # 空/缺省回落 all，避免拼出非法参数
    assert f2.build_f2_command(author, interval="")[cmd.index("-i") + 1] == "all"


# ── 日期窗口（P0 提速：`-i all` 会让 f2 把作者全部历史翻完，每页固定等 timeout）──


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


def test_run_fetch_passes_per_author_window(tmp_path):
    """每个作者按自己的目录 mtime 取窗口：新博主全量，久未更新的自动放大。"""
    f2_dir = _f2_dir_with_authors(
        tmp_path,
        authors=[
            ("MS4wLjABAAAAaaa", "里香1√", 171),
            ("MS4wLjABAAAAbbb", "娜娜瑜√", 232),
        ],
    )
    post_root = f2_dir / f2.F2_DOWNLOAD_SUBDIR
    (post_root / "里香1√").mkdir(parents=True)
    # 里香：上次下载是 60 天前 → 窗口应放大到那天之前；娜娜瑜：目录不存在 → 最小窗口
    stamp = (utcnow() - timedelta(days=60)).timestamp()
    os.utime(post_root / "里香1√", (stamp, stamp))

    seen: list[list[str]] = []
    runner = lambda cmd, cwd: (seen.append(cmd) or 0, "")  # noqa: E731

    result = f2.run_fetch(f2_dir, runner=runner, since_days=14)

    intervals = [cmd[cmd.index("-i") + 1] for cmd in seen]
    assert all("|" in value for value in intervals)  # 都是窗口，不再是 all
    widened = intervals[0]
    assert widened.startswith(f"{(utcnow() - timedelta(days=61)).date():%Y-%m-%d}")
    assert intervals[1] == f2.compute_fetch_interval(14, None)
    assert result["windows"] == {widened: 1, intervals[1]: 1}
    assert result["results"][0]["interval"] == widened

    # since_days=0 → 全历史（首次全量）
    seen.clear()
    f2.run_fetch(f2_dir, runner=runner, since_days=0)
    assert all(cmd[cmd.index("-i") + 1] == "all" for cmd in seen)


def test_run_fetch_invokes_f2_per_author_and_continues_on_failure(tmp_path):
    """逐作者串行调用；某个作者失败只记录并继续（cwd 必须是 f2 工作目录）。"""
    f2_dir = _f2_dir_with_authors(tmp_path)
    calls: list[tuple[list[str], Path]] = []

    def fake_runner(cmd, cwd):
        calls.append((cmd, cwd))
        return (1 if "娜娜瑜" in " ".join(cmd) or len(calls) == 2 else 0), ""

    result = f2.run_fetch(f2_dir, runner=fake_runner)

    assert result["total"] == 2 and result["ok"] == 1 and result["failed"] == 1
    assert len(calls) == 2  # 失败后继续跑下一个作者
    assert all(cwd == f2_dir for _cmd, cwd in calls)  # f2 的 cwd 必须是其工作目录
    assert [r["nickname"] for r in result["results"]] == ["里香1√", "娜娜瑜√"]
    assert result["results"][1]["rc"] == 1


def test_run_fetch_author_filter_and_limit(tmp_path):
    """--authors 复用归一化匹配（与导入阶段语义一致）；--fetch-limit 截断。"""
    f2_dir = _f2_dir_with_authors(tmp_path)
    seen: list[str] = []
    runner = lambda cmd, cwd: (seen.append(" ".join(cmd)) or 0, "")  # noqa: E731

    result = f2.run_fetch(f2_dir, authors={"里香"}, runner=runner)
    assert result["total"] == 1 and len(seen) == 1

    result = f2.run_fetch(f2_dir, limit=1, runner=runner)
    assert result["total"] == 1


def test_run_fetch_runner_exception_does_not_abort(tmp_path):
    """执行器抛异常（如 f2 崩溃）也只算该作者失败。"""
    f2_dir = _f2_dir_with_authors(tmp_path)

    def boom(cmd, cwd):
        raise OSError("f2 崩溃")

    result = f2.run_fetch(f2_dir, runner=boom)
    assert result["failed"] == 2 and result["ok"] == 0


def test_run_fetch_without_author_db_reports_error(tmp_path):
    result = f2.run_fetch(tmp_path / "不存在", runner=lambda cmd, cwd: (0, ""))
    assert result["total"] == 0 and result["error"]


# ── 按批次回滚（--rollback）──


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


def test_plan_rollback_marks_untouched_as_deletable(tmp_path):
    db, _storage, batch_file, ids = _imported_fixture(tmp_path)
    deletable, kept = f2.plan_rollback(batch_file, db)
    assert len(deletable) == 2 and kept == []
    assert {d["inspiration_id"] for d in deletable} == set(ids)


def test_plan_rollback_keeps_touched_material_unless_forced(tmp_path):
    """已改动（有标签关联/收藏/评分/非 pending）的素材默认保留，避免抹掉后续工作。"""
    db, _storage, batch_file, ids = _imported_fixture(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO inspiration_tags VALUES (?, 1, 'ai_generated')", (ids[0],))
    conn.execute("UPDATE inspirations SET is_favorite = 1 WHERE id = ?", (ids[1],))
    conn.commit()
    conn.close()

    deletable, kept = f2.plan_rollback(batch_file, db)
    assert deletable == []
    assert len(kept) == 2 and all(k["keep_reason"] for k in kept)

    deletable, kept = f2.plan_rollback(batch_file, db, force=True)
    assert len(deletable) == 2 and kept == []


def test_plan_rollback_handles_already_removed(tmp_path):
    db, storage, batch_file, ids = _imported_fixture(tmp_path)
    f2.apply_rollback(
        [{"inspiration_id": ids[0], "file_path": None, "thumbnail_path": None}],
        db_path=db,
        storage_root=storage,
    )
    deletable, kept = f2.plan_rollback(batch_file, db)
    assert len(deletable) == 1  # 另一条仍可删
    assert len(kept) == 1 and "已不存在" in kept[0]["keep_reason"]


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


# ── 代码审查修复项的回归测试 ──


def test_apply_import_passes_thumbs_dir_for_video(tmp_path, monkeypatch):
    """修复（审查 M1）：视频缩略图必须写进调用方的 storage 根。

    回归点：extract_video_thumbnail_sync 内部默认用 settings.thumbnails_dir，
    不传 thumbs_dir 时用 --storage-root 试跑会把缩略图落进真实存储。
    """
    root = tmp_path / "f2"
    _jpeg(root / "A" / "2025-01-01 10-00-00_标题_video.mp4".replace(".mp4", ".jpg"))
    files = f2.scan_directory(root)
    db = _import_lib(tmp_path)
    storage = tmp_path / "storage"
    captured: dict = {}

    def _fake_video_thumb(video_path, today, thumbs_dir=None):
        captured["thumbs_dir"] = thumbs_dir
        return None

    monkeypatch.setattr(f2, "extract_video_thumbnail_sync", _fake_video_thumb)
    decisions, _, _ = _decisions(files)
    # 该文件按类型推断是 image；强制改成 video 以走视频缩略图分支
    decisions = [
        f2.ImportDecision(
            item=f2.ParsedFile(
                path=d.item.path, author_dir=d.item.author_dir, author_key=d.item.author_key,
                work_key=d.item.work_key, created=d.item.created, body=d.item.body,
                kind="video", index=0, media_type="video",
            ),
            action=d.action, reason=d.reason, platform_id=d.platform_id,
            content_hash=d.content_hash,
        )
        for d in decisions
    ]
    f2.apply_import(
        [d for d in decisions if d.action == "import"],
        db_path=db,
        storage_root=storage,
    )

    assert captured.get("thumbs_dir") == storage / "thumbnails"


def test_apply_import_reports_batch_error_separately(tmp_path):
    """修复（审查 M5）：批次清单写失败不再混进「素材失败」计数，单列 batch_error。"""
    root = tmp_path / "f2"
    _jpeg(root / "A" / "2025-01-01 10-00-00_标题_image_1.jpg")
    db = _import_lib(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("占位文件，令 mkdir 失败", encoding="utf-8")

    decisions, _, _ = _decisions(f2.scan_directory(root))
    result = f2.apply_import(
        [d for d in decisions if d.action == "import"],
        db_path=db,
        storage_root=tmp_path / "storage",
        batch_dir=blocker / "import_batches",  # 父路径是文件 → 建目录必失败
    )

    assert result["imported"] == 1
    assert result["failed"] == 0  # 素材没有失败
    assert result["batch_error"]
    assert result["batch_file"] == ""


def test_apply_import_batch_id_unique_within_same_second(tmp_path, monkeypatch):
    """修复（审查 L2）：同秒内跑两批不能互相覆盖批次清单（否则那一批无法回滚）。

    回归点：batch_id 原先只到秒（f2-YYYYMMDD-HHMMSS），CLI 与任务队列撞车时
    后写的清单会覆盖先写的，被覆盖那批的 ids 就此丢失。
    """
    fixed = datetime(2026, 1, 2, 3, 4, 5)
    monkeypatch.setattr(f2, "utcnow", lambda: fixed)

    root = tmp_path / "f2"
    _jpeg(root / "A" / "2025-01-01 10-00-00_标题A_image_1.jpg")
    _jpeg(root / "B" / "2025-02-02 11-00-00_标题B_image_1.jpg", "blue")
    files = f2.scan_directory(root)
    db = _import_lib(tmp_path)
    storage = tmp_path / "storage"
    decisions, _, _ = _decisions(files)
    to_import = [d for d in decisions if d.action == "import"]
    assert len(to_import) == 2

    first = f2.apply_import(to_import[:1], db_path=db, storage_root=storage)
    second = f2.apply_import(to_import[1:], db_path=db, storage_root=storage)

    assert first["batch_file"] != second["batch_file"]
    assert Path(first["batch_file"]).exists() and Path(second["batch_file"]).exists()
    # 仍以秒级时间戳开头（人读顺序与 latest_batch_file 的字典序取值都不变）
    assert Path(second["batch_file"]).name.startswith("f2-20260102-030405-")
    for path, expected in ((first["batch_file"], 1), (second["batch_file"], 1)):
        assert len(json.loads(Path(path).read_text(encoding="utf-8"))["imported"]) == expected


def test_apply_import_failure_removes_thumbnail(tmp_path):
    """修复（审查 L1）：单条失败时，已生成的缩略图也要删掉（否则留孤儿文件）。"""
    root = tmp_path / "f2"
    _jpeg(root / "A" / "2025-01-01 10-00-00_标题_image_1.jpg")
    db = _import_lib(tmp_path)
    storage = tmp_path / "storage"
    decisions, _, _ = _decisions(f2.scan_directory(root))
    to_import = [d for d in decisions if d.action == "import"]

    # 先手插一条同平台 ID 的行 → 真导入撞唯一索引失败
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO inspirations (id, source_platform_id) VALUES ('pre', ?)",
        (to_import[0].platform_id,),
    )
    conn.commit()
    conn.close()

    result = f2.apply_import(to_import, db_path=db, storage_root=storage)

    assert result["imported"] == 0 and result["failed"] == 1
    assert list((storage / "images").rglob("*.jpg")) == []  # 主文件已清理
    thumbs = list((storage / "thumbnails").rglob("*.jpg")) if (storage / "thumbnails").exists() else []
    assert thumbs == []  # 缩略图也已清理


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


def test_plan_rollback_tolerates_missing_tag_table(tmp_path):
    """修复（审查 L3）：缺 inspiration_tags 表的库副本也能给出可读结果。"""
    root = tmp_path / "f2"
    _jpeg(root / "A" / "2025-01-01 10-00-00_标题_image_1.jpg")
    db = _import_lib(tmp_path)
    storage = tmp_path / "storage"
    decisions, _, _ = _decisions(f2.scan_directory(root))
    result = f2.apply_import(
        [d for d in decisions if d.action == "import"], db_path=db, storage_root=storage
    )

    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE inspiration_tags")
    conn.commit()
    conn.close()

    deletable, kept = f2.plan_rollback(Path(result["batch_file"]), db)
    assert len(deletable) == 1 and kept == []
    rolled = f2.apply_rollback(deletable, db_path=db, storage_root=storage)
    assert rolled["deleted"] == 1  # 缺表时跳过标签清理，不报错


# ── 进度回调与停止判据（供后台任务报告进度 / 响应暂停取消）──


def test_apply_import_reports_progress(tmp_path):
    """on_progress(done, total) 逐条回调，供后台任务写进度。"""
    root = tmp_path / "f2"
    for i, color in enumerate(("red", "blue", "green")):
        _jpeg(root / "A" / f"2025-01-0{i + 1} 10-00-00_标题{i}_image_1.jpg", color)
    db = _import_lib(tmp_path)
    decisions, _, _ = _decisions(f2.scan_directory(root))
    seen: list[tuple[int, int]] = []

    f2.apply_import(
        [d for d in decisions if d.action == "import"],
        db_path=db,
        storage_root=tmp_path / "storage",
        make_thumbnails=False,
        on_progress=lambda done, total: seen.append((done, total)),
    )

    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_apply_import_stops_when_should_stop(tmp_path):
    """should_stop 返回 True 时提前收尾：已入库部分保留，批次清单照常落盘。"""
    root = tmp_path / "f2"
    for i, color in enumerate(("red", "blue", "green")):
        _jpeg(root / "A" / f"2025-01-0{i + 1} 10-00-00_标题{i}_image_1.jpg", color)
    db = _import_lib(tmp_path)
    decisions, _, _ = _decisions(f2.scan_directory(root))
    calls = {"n": 0}

    def _stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # 第一条之后请求停止

    result = f2.apply_import(
        [d for d in decisions if d.action == "import"],
        db_path=db,
        storage_root=tmp_path / "storage",
        make_thumbnails=False,
        should_stop=_stop,
    )

    assert result["stopped"] is True
    assert result["imported"] == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM inspirations").fetchone()[0] == 1
    conn.close()
    batch = json.loads(Path(result["batch_file"]).read_text(encoding="utf-8"))
    assert len(batch["imported"]) == 1  # 部分成功也留痕，可回滚


# ── 「我的喜欢」（点赞模式）：命名带作者前缀、命令形状、去重口径 ──


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


def test_plan_dedups_like_file_against_post_import(tmp_path):
    """回归（去重要求）：已从博主主页入库过的作品，从「我的喜欢」再来一次必须跳过。"""
    write_ = _write
    write_(tmp_path / "不养羊" / "2026-09-14 10-31-14_标题_image_1.webp", b"img")
    post_files = f2.scan_directory(tmp_path)
    digest = f2.sha256_file(post_files[0].path)

    like_root = tmp_path / "like" / "我的账号"
    write_(like_root / "不养羊_2026-09-14 10-31-14_标题_image_1.webp", b"img")
    like_files = f2.scan_directory(tmp_path / "like")

    decisions, skipped, _ = _decisions(like_files, library_hashes={digest})

    assert all(d.action == "skip" for d in decisions)
    assert skipped["已在库（内容相同）"] == 1


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


def test_build_f2_like_command_shape():
    """点赞命令：-M like + 带 {nickname}/{aweme_id} 的命名模板 + **不传 `-i`**。

    原作者只存在于文件名里，作品 ID 同理（点赞列表跨作者，缺一不可）。

    回归点（2026-09 读 f2 源码更正）：f2 的点赞模式 `handle_user_like` /
    `fetch_user_like_videos` **都不读 `interval`**，`-i` 纯属无效参数——此前传
    `-i all` 并注释「f2 按发布时间过滤，窗口会漏掉最近点赞的老视频」，那描述的是
    一个不存在的过滤器，会误导后人以为能用 `-i` 收窄点赞增量。
    """
    cmd = f2.build_f2_like_command(
        "MS4wLjABAAAAme", download_root=Path("D:/f2/Download")
    )

    assert cmd[:4] == [sys.executable, "-m", "f2", "dy"]
    assert cmd[cmd.index("-u") + 1] == "https://www.douyin.com/user/MS4wLjABAAAAme"
    assert cmd[cmd.index("-M") + 1] == "like"
    assert "-i" not in cmd  # 点赞模式不读 interval，传了是无效参数
    assert cmd[cmd.index("-n") + 1] == f2.LIKE_NAMING_TEMPLATE
    assert f2.LIKE_NAMING_TEMPLATE == "{nickname}_{create}_{desc}_{aweme_id}"
    assert cmd[cmd.index("-p") + 1] == str(Path("D:/f2/Download"))
    assert "-o" not in cmd  # 缺省全量翻到底（行为与改造前一致）


def test_build_f2_like_command_incremental_adds_max_counts():
    """增量模式：`max_counts>0` 时传 `-o`（唯一能收窄 f2 点赞翻页量的参数）。

    为什么需要：f2 的点赞分页没有「遇到已下载就停」，每页还固定 sleep 一次 timeout；
    点赞列表最新在前，只翻最近 N 条即可覆盖新增。
    """
    cmd = f2.build_f2_like_command("MS4wLjABAAAAme", max_counts=150)

    assert cmd[cmd.index("-o") + 1] == "150"
    assert "-i" not in cmd

    # 0 / 负数都视为「全量」，不拼出非法参数
    assert "-o" not in f2.build_f2_like_command("sec", max_counts=0)
    assert "-o" not in f2.build_f2_like_command("sec", max_counts=-5)


def test_run_fetch_likes_reports_and_requires_user(tmp_path):
    """没配主页链接直接给出可操作错误；配了则跑一条命令并汇报退出码。"""
    missing = f2.run_fetch_likes(tmp_path, "")
    assert missing["total"] == 0 and "未配置" in missing["error"]

    calls: list[list[str]] = []
    ok = f2.run_fetch_likes(
        tmp_path, "MS4wLjABAAAAme", runner=lambda cmd, cwd: (calls.append(cmd) or (0, ""))
    )
    assert ok["total"] == 1 and ok["ok"] == 1 and ok["failed"] == 0
    assert calls and calls[0][calls[0].index("-M") + 1] == "like"

    bad = f2.run_fetch_likes(
        tmp_path, "MS4wLjABAAAAme", runner=lambda cmd, cwd: (1, "cookie 失效")
    )
    assert bad["failed"] == 1 and bad["ok"] == 0


def test_run_fetch_likes_passes_max_counts_and_reports_it(tmp_path):
    """增量条数要真的进命令行，并回显在结果里（任务结果据此显示「最近 N 条」）。"""
    seen: list[list[str]] = []
    result = f2.run_fetch_likes(
        tmp_path,
        "MS4wLjABAAAAme",
        max_counts=120,
        runner=lambda cmd, cwd: (seen.append(cmd) or (0, "")),
    )

    assert result["max_counts"] == 120
    assert result["results"][0]["max_counts"] == 120
    assert seen and seen[0][seen[0].index("-o") + 1] == "120"


def test_run_fetch_likes_defaults_to_full_pagination(tmp_path):
    """缺省 max_counts=0 → 不传 `-o`（全量，行为与改造前一致）。"""
    seen: list[list[str]] = []
    result = f2.run_fetch_likes(
        tmp_path,
        "MS4wLjABAAAAme",
        runner=lambda cmd, cwd: (seen.append(cmd) or (0, "")),
    )

    assert result["max_counts"] == 0
    assert seen and "-o" not in seen[0]


# ═══════════════════════════════════════════════════════════════
#  真实作品 ID（新命名模板）：解析 / 平台 ID / 原帖链接 / 两套口径并存
# ═══════════════════════════════════════════════════════════════

"""实测作品 ID 形态：19 位数字（f2 的 {aweme_id} 长度固定 19）。"""
AWEME = "7412345678901234567"


def _new_name(body: str = "标题", kind: str = "image_1") -> str:
    """新模板产物名：{create}_{desc}_{aweme_id}_{kind}。"""
    return f"2025-01-01 10-00-00_{body}_{AWEME}_{kind}.webp"


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


def test_plan_dedups_new_file_against_legacy_platform_id(tmp_path):
    """回归（两套口径并存）：同一作品先前用旧模板入库，改用新模板重下必须跳过。

    库里的历史素材存的是旧哈希 ID；只比对新作品 ID 会漏判 → 重复入库。
    这里刻意不传 library_hashes：内容判据失效时，平台 ID 这一层必须自己兜住。
    """
    legacy_path = tmp_path / "A" / "2025-01-01 10-00-00_标题_image_1.webp"
    _write(legacy_path, b"legacy")
    legacy_id = f2.platform_id_for(f2.parse_media_filename(legacy_path, "A"))
    assert legacy_id.startswith("f2:") and AWEME not in legacy_id

    new_path = tmp_path / "A" / _new_name("标题")
    _write(new_path, b"redownloaded")  # 内容与旧文件不同，内容判据不参与
    new_item = f2.parse_media_filename(new_path, "A")

    decisions, skipped, _ = _decisions([new_item], platform_ids={legacy_id})
    assert all(d.action == "skip" for d in decisions)
    assert skipped["已在库（平台 ID 命中）"] == 1


def test_plan_keeps_new_platform_id_as_primary(tmp_path):
    """落库用的是新口径 ID（作品 ID），不是去重时顺带查的旧哈希 ID。"""
    path = tmp_path / "A" / _new_name("标题")
    _write(path, b"img")
    item = f2.parse_media_filename(path, "A")

    decisions, _, _ = _decisions([item])

    assert decisions[0].action == "import"
    assert decisions[0].platform_id == f"f2:{AWEME}#image1"


def test_apply_import_writes_source_url(tmp_path):
    """新命名文件入库要写真原帖链接（本轮改动的交付点）。"""
    root = tmp_path / "f2"
    _jpeg(root / "A" / _new_name("标题").replace(".webp", ".jpg"))
    files = f2.scan_directory(root)
    db = _import_lib(tmp_path)

    result = f2.apply_import(
        [d for d in _decisions(files)[0] if d.action == "import"],
        db_path=db,
        storage_root=tmp_path / "storage",
        make_thumbnails=False,
    )
    assert result["imported"] == 1

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT source_url, source_platform_id FROM inspirations").fetchone()
    conn.close()
    assert row[0] == f"https://www.douyin.com/note/{AWEME}"
    assert row[1] == f"f2:{AWEME}#image1"


def test_apply_import_video_uses_video_url(tmp_path):
    """视频作品走 /video/（图集里的 live 分段仍走 /note/，见 source_url_for 用例）。"""
    root = tmp_path / "f2"
    _fake_mp4(root / "A" / _new_name("标题", "video").replace(".webp", ".mp4"))
    files = f2.scan_directory(root)
    db = _import_lib(tmp_path)

    f2.apply_import(
        [d for d in _decisions(files)[0] if d.action == "import"],
        db_path=db,
        storage_root=tmp_path / "storage",
        make_thumbnails=False,
    )

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT source_url FROM inspirations").fetchone()
    conn.close()
    assert row[0] == f"https://www.douyin.com/video/{AWEME}"


def test_apply_import_leaves_source_url_empty_for_legacy_naming(tmp_path):
    """旧命名文件（无作品 ID）入库仍留空——不造打不开的伪链接。"""
    root = tmp_path / "f2"
    _jpeg(root / "A" / "2025-01-01 10-00-00_标题_image_1.jpg")
    files = f2.scan_directory(root)
    db = _import_lib(tmp_path)

    f2.apply_import(
        [d for d in _decisions(files)[0] if d.action == "import"],
        db_path=db,
        storage_root=tmp_path / "storage",
        make_thumbnails=False,
    )

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT source_url FROM inspirations").fetchone()
    conn.close()
    assert row[0] is None


def test_run_fetch_passes_naming_with_aweme_id(tmp_path):
    """端到端：run_fetch 构造的 f2 命令必须带含 {aweme_id} 的命名模板。"""
    f2_dir = _f2_dir_with_authors(tmp_path)
    calls: list[list[str]] = []

    result = f2.run_fetch(
        f2_dir, runner=lambda cmd, cwd: (calls.append(cmd) or (0, ""))
    )

    assert result["ok"] == 2
    assert len(calls) == 2
    for cmd in calls:
        assert "{aweme_id}" in cmd[cmd.index("-n") + 1]


# ── 与 f2 本体的契约：模板必须被 f2 接受，且 f2 生成的产物必须能被解析回来 ──
#
# 守卫必须写在**每个用例内部**，不能写成模块级 `pytest.importorskip("f2")`：模块级
# 一次跳过会连带把本文件其余 120 多个与 f2 无关的用例（解析/判重/回滚/URL 构造）
# 一起跳过——CI 未装 f2 时 127 个用例只报「1 skipped」，等于整条 f2 链路没有 CI 覆盖
# （实测确认：模块级 importorskip 在缺依赖时把整个模块记为 1 个 skip）。


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


# ── 作品类型归属：封面属于视频作品 ──


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


def test_apply_import_cover_of_video_work_uses_video_url(tmp_path):
    """端到端：同一作品下同时有 video 与 cover 时，cover 也要写 /video/。"""
    root = tmp_path / "f2"
    _fake_mp4(root / "A" / _new_name("标题", "video").replace(".webp", ".mp4"))
    _fake_mp4(root / "A" / _new_name("标题", "cover").replace(".webp", ".jpg"))
    files = f2.scan_directory(root)
    db = _import_lib(tmp_path)

    f2.apply_import(
        [d for d in _decisions(files)[0] if d.action == "import"],
        db_path=db,
        storage_root=tmp_path / "storage",
        make_thumbnails=False,
    )

    conn = sqlite3.connect(db)
    urls = {row[0] for row in conn.execute("SELECT source_url FROM inspirations")}
    conn.close()
    assert urls == {f"https://www.douyin.com/video/{AWEME}"}  # 两条都必须是 /video/
