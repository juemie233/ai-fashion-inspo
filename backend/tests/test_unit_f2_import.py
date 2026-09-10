"""f2 下载目录扫描/报表单元测试（scripts/import_f2_downloads.py）。

不触碰真实素材库：库内哈希与博主用临时 sqlite 或直接注入，目录用 tmp_path 构造。
"""

import json
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


# ── 导入计划：四层去重 ──


def _decisions(files, library_hashes=None, platform_ids=None, **kwargs):
    return f2.build_import_plan(
        files=files,
        library_hashes=library_hashes or set(),
        existing_platform_ids=platform_ids or set(),
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
    assert skipped["作者不在 --authors 范围"] == 1
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
        library_hashes=f2.load_library_hashes(db),
        existing_platform_ids=f2.load_library_platform_ids(db),
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
    """默认命令：主页作品 + 全部日期 + 指定下载根；不擅自改命名模板。"""
    author = {"sec_user_id": "MS4wLjABAAAAaaa", "nickname": "里香"}
    cmd = f2.build_f2_command(author, download_root=Path("D:/f2/Download"))

    assert cmd[1:4] == ["-m", "f2", "dy"]
    assert "-u" in cmd and cmd[cmd.index("-u") + 1] == "https://www.douyin.com/user/MS4wLjABAAAAaaa"
    assert cmd[cmd.index("-M") + 1] == "post"
    assert cmd[cmd.index("-i") + 1] == "all"
    assert cmd[cmd.index("-p") + 1] == str(Path("D:/f2/Download"))
    assert "-n" not in cmd  # 缺省沿用 f2 配置的命名模板（解析依赖其形状）
    assert "--auto-cookie" not in cmd


def test_build_f2_command_optional_flags():
    author = {"sec_user_id": "sec1", "nickname": "A"}
    cmd = f2.build_f2_command(author, naming="{create}_{desc}", auto_cookie="chrome")
    assert cmd[cmd.index("-n") + 1] == "{create}_{desc}"
    assert cmd[cmd.index("--auto-cookie") + 1] == "chrome"


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
        library_hashes=f2.load_library_hashes(db),
        existing_platform_ids=f2.load_library_platform_ids(db),
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
