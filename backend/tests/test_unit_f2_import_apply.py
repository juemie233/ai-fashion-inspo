"""f2 入库侧单元测试：导入计划（跳过/回滚）、报告生成、落库。"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
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



def test_plan_skips_same_work_segment_downloaded_twice(tmp_path):
    """同一作品的同一分段被下载成两个文件（历史命名无 aweme_id + 新命名带 aweme_id）只入一次。

    回归（用户反馈 2026-09-21 任务 #365「抖音我的喜欢」重复入库）：同组内所有文件的
    跳过原因被一次性算完，此刻第一条还没登记进 seen_hashes，于是两条都判为可导入。
    """
    _write(tmp_path / "作者" / "2025-01-01 10-00-00_标题_image_1.webp", b"same")
    _write(
        tmp_path / "作者" / "2025-01-01 10-00-00_标题_7405539058576903424_image_1.webp",
        b"same",
    )
    files = f2.scan_directory(tmp_path)
    assert len(files) == 2

    decisions, skipped, _ = _decisions(files)
    imported = [d for d in decisions if d.action == "import"]
    assert len(imported) == 1, f"同作品同分段重复入库：{[d.item.path.name for d in imported]}"
    # 留下带真实作品 ID 的那份（platform_id / source_url 更完整）
    assert imported[0].item.aweme_id == "7405539058576903424"
    assert skipped["批次内重复（同内容已处理）"] == 1



def test_plan_skips_same_work_segment_with_different_bytes(tmp_path):
    """同一作品同一分段但字节不同（重新下载/转码）：仍视为同一素材，只入一次。

    内容哈希挡不住（字节不同）、平台 ID 只比对库内（本批还没入）——必须按
    「作品 + 分段」判重，否则同一作品会被下两次、重复入库两次。
    """
    _write(tmp_path / "作者" / "2025-01-01 10-00-00_标题_image_1.webp", b"old-bytes")
    _write(
        tmp_path / "作者" / "2025-01-01 10-00-00_标题_7405539058576903424_image_1.webp",
        b"new-bytes",
    )
    files = f2.scan_directory(tmp_path)

    decisions, skipped, _ = _decisions(files)
    imported = [d for d in decisions if d.action == "import"]
    assert len(imported) == 1, f"同作品同分段重复入库：{[d.item.path.name for d in imported]}"
    assert imported[0].item.aweme_id == "7405539058576903424"
    assert skipped["批次内重复（同作品同分段已处理）"] == 1



def test_plan_skips_when_platform_id_exists(tmp_path):
    """幂等兜底：内容哈希口径变化时，平台 ID 命中也能挡住重复入库。"""
    files = _fake_tree(tmp_path)
    ids = {f2.platform_id_for(f) for f in files}
    decisions, skipped, _ = _decisions(files, platform_ids=ids)
    assert all(d.action == "skip" for d in decisions)
    assert skipped["已在库（平台 ID 命中）"] == 3



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

    patch_f2(monkeypatch, "extract_video_thumbnail_sync", _fake_video_thumb)
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
    patch_f2(monkeypatch, "utcnow", lambda: fixed)

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



def test_apply_import_cover_of_video_work_uses_video_url(tmp_path):
    """端到端：同一作品下同时有 video 与 cover 时，cover 也要写 /video/。"""
    root = tmp_path / "f2"
    _fake_mp4(root / "A" / _new_name("标题", "video").replace(".webp", ".mp4"))
    _fake_mp4(
        root / "A" / _new_name("标题", "cover").replace(".webp", ".jpg"),
        b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41cover",
    )
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



# ── --prune-downloads（入库后清理 f2 源文件，默认关）──


def test_apply_import_prune_downloads_default_off(tmp_path):
    """默认不删源文件：入库是 copy，f2 下载目录保持原样（防误删唯一副本）。"""
    root = tmp_path / "f2"
    img = root / "里香1√" / "2025-01-01 10-00-00_#jk_image_1.jpg"
    _jpeg(img)
    files = f2.scan_directory(root)
    db = _import_lib(tmp_path)
    storage = tmp_path / "storage"

    decisions, _, _ = _decisions(files, bloggers={"里香": [{"id": 302, "name": "里香"}]})
    result = f2.apply_import(
        [d for d in decisions if d.action == "import"],
        db_path=db,
        storage_root=storage,
    )

    assert result["imported"] == 1
    assert result["pruned"] == 0
    assert img.exists()  # 源文件仍在
    assert list((storage / "images").rglob("*.jpg"))  # 入库副本也在


def test_apply_import_prune_downloads_deletes_only_imported_sources(tmp_path):
    """prune=True：只删本批入库成功的源文件；被跳过的不动；重扫后只剩跳过的那份。"""
    root = tmp_path / "f2"
    imported_img = root / "里香1√" / "2025-01-01 10-00-00_#jk_image_1.jpg"
    skipped_img = root / "里香1√" / "2025-01-01 10-00-00_#jk_image_2.jpg"
    _jpeg(imported_img)
    _jpeg(skipped_img, "blue")
    files = f2.scan_directory(root)
    db = _import_lib(tmp_path)
    storage = tmp_path / "storage"

    # skipped_img 的内容哈希已在库 → 计划里是 skip，不会进 apply，也不该被删
    decisions, skipped, _ = _decisions(
        files,
        library_hashes={f2.sha256_file(skipped_img)},
        bloggers={"里香": [{"id": 302, "name": "里香"}]},
    )
    assert skipped  # 有跳过原因
    result = f2.apply_import(
        [d for d in decisions if d.action == "import"],
        db_path=db,
        storage_root=storage,
        prune_downloads=True,
    )

    assert result["imported"] == 1
    assert result["pruned"] == 1
    assert not imported_img.exists()   # 已入库 → 源文件清理
    assert skipped_img.exists()        # 未入库 → 原样保留
    # 幂等：只清成功的那份，重扫不会凭空多出文件
    remaining = {p.path for p in f2.scan_directory(root)}
    assert remaining == {skipped_img}
