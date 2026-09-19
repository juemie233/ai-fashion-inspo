"""历史 f2 素材回填真实作品 ID 的单元测试（scripts/backfill_f2_aweme_ids.py）。

全部离线：报告、磁盘文件、素材库都用临时目录造假，不发网络请求。
重点锁死**安全性**：只改预期行、不 INSERT/DELETE、撞唯一索引时降级为只补链接、
重复运行幂等、可按清单回滚。
"""

import json
import sqlite3
from dataclasses import replace

import pytest

from scripts import backfill_f2_aweme_ids as bf
from scripts import import_f2_downloads as f2

AWEME = "7412345678901234567"
AWEME2 = "7412345678901234568"

"""新命名的文件名（带真实作品 ID），用来造「同一作品被重复下载」的场景。"""
NEW_NAME = f"2026-09-14 10-31-14_标题_{AWEME}_image_1.webp"
LEGACY_NAME = "2026-09-14 10-31-14_标题_image_1.webp"


def _write(path, content=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _item(name=LEGACY_NAME, author_dir="A"):
    """解析出一个 legacy 命名的磁盘项（历史文件形态）。"""
    return f2.parse_media_filename(__import__("pathlib").Path(author_dir) / name, author_dir)


def _enrich(item, aweme=AWEME):
    """给磁盘项补上真实作品 ID（模拟「已查到对齐」）。"""
    return replace(item, aweme_id=aweme)


def _lib(tmp_path, rows):
    """建最小素材库并插入给定行 [(id, platform_id, source_url, deleted_at)]。"""
    db = tmp_path / "lib.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE inspirations (id TEXT PRIMARY KEY, source_platform_id TEXT, "
        "source_url TEXT, updated_at TEXT, deleted_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO inspirations VALUES (?, ?, ?, NULL, ?)",
        [(r[0], r[1], r[2], r[3]) for r in rows],
    )
    conn.commit()
    conn.close()
    return db


def _rows(db):
    conn = sqlite3.connect(db)
    out = conn.execute(
        "SELECT id, source_platform_id, source_url FROM inspirations ORDER BY id"
    ).fetchall()
    conn.close()
    return out


# ═══════════════════════════════════════════════════════════════
#  对齐报告读取
# ═══════════════════════════════════════════════════════════════


def test_load_alignment_reads_only_unique(tmp_path):
    """只取「唯一对齐」——多义与未命中绝不能进回填计划。"""
    report = tmp_path / "r.json"
    report.write_text(
        json.dumps(
            {
                "authors": {
                    "里香": {
                        "unique": [["k1", "111"], ["k2", "222"]],
                        "ambiguous": [["k3", ["333", "444"]]],
                        "unmatched": ["k4"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    alignment = bf.load_alignment(report)

    assert alignment == {"里香": {"k1": "111", "k2": "222"}}


def test_load_alignment_missing_report_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="找不到对齐报告"):
        bf.load_alignment(tmp_path / "没有.json")


# ═══════════════════════════════════════════════════════════════
#  计划构造（纯函数）
# ═══════════════════════════════════════════════════════════════


def test_build_plan_updates_id_and_url():
    item = _item()
    alignment = {"A": {"2026-09-14 10-31-14_标题": AWEME}}
    legacy = f2.legacy_platform_id_for(item)
    plan, skipped = bf.build_plan([item], alignment, [("insp1", legacy, None)])

    assert skipped == {}
    assert len(plan) == 1
    entry = plan[0]
    assert entry["kind"] == "full"
    assert entry["old_platform_id"] == legacy
    assert entry["new_platform_id"] == f"f2:{AWEME}#image1"
    assert entry["new_source_url"] == f"https://www.douyin.com/note/{AWEME}"


def test_build_plan_skips_work_not_in_alignment():
    """未命中/多义的作品不在报告里 → 一行都不许动。"""
    item = _item()
    plan, skipped = bf.build_plan([item], {"A": {}}, [])

    assert plan == []
    assert skipped["不在报告的唯一对齐里（多义/未命中/非报告作者）"] == 1


def test_build_plan_skips_row_missing_from_library():
    item = _item()
    alignment = {"A": {"2026-09-14 10-31-14_标题": AWEME}}
    plan, skipped = bf.build_plan([item], alignment, [])

    assert plan == []
    assert skipped["库内无对应行（未导入该文件 / 已删除）"] == 1


def test_build_plan_downgrades_duplicate_work_to_url_only():
    """同一作品被重复下载成两行：新 ID 只能给一行，另一行只补链接。

    回归点：两行都改 ID 会撞 ix_inspirations_source_platform_id（部分唯一索引）。
    """
    # 同名作品落在两个作者目录（`不养羊` / `不养羊√`）→ 归一化作者相同、原始名不同
    # → 两个不同的 legacy ID，但 stem 相同 → 映射到同一个新 ID
    a = _item("2026-09-14 10-31-14_标题_image_1.webp", "不养羊")
    b = _item("2026-09-14 10-31-14_标题_image_1.webp", "不养羊√")
    assert f2.legacy_platform_id_for(a) != f2.legacy_platform_id_for(b)

    alignment = {"不养羊": {"2026-09-14 10-31-14_标题": AWEME}}
    rows = [
        ("insp_a", f2.legacy_platform_id_for(a), None),
        ("insp_b", f2.legacy_platform_id_for(b), None),
    ]
    plan, skipped = bf.build_plan([a, b], alignment, rows)

    kinds = sorted(e["kind"] for e in plan)
    assert kinds == ["full", "url_only"]
    assert skipped["同作品重复行（仅补链接，不改ID）"] == 1
    # url_only 那行必须保持原 ID 不变
    url_only = next(e for e in plan if e["kind"] == "url_only")
    assert url_only["new_platform_id"] == url_only["old_platform_id"]


def test_build_plan_dedupes_same_row_hit_twice():
    """同一行被两个文件命中（跨目录同作品、原始作者名一致）→ 只处理一次。"""
    name = "2026-09-14 10-31-14_标题_image_1.webp"
    a = _item(name, "不养羊")
    b = _item(name, "不养羊")
    alignment = {"不养羊": {"2026-09-14 10-31-14_标题": AWEME}}
    rows = [("insp_a", f2.legacy_platform_id_for(a), None)]

    plan, skipped = bf.build_plan([a, b], alignment, rows)

    assert len(plan) == 1
    assert skipped["同一行被多个文件命中（已去重）"] == 1


def test_build_plan_video_url_and_live_url():
    """视频作品走 /video/，图集里的 live 分段走 /note/。"""
    video = _item("2026-09-14 10-31-14_标题_video.mp4")
    live = _item("2026-09-14 10-31-14_另一条_live_1.mp4")
    alignment = {
        "A": {"2026-09-14 10-31-14_标题": AWEME, "2026-09-14 10-31-14_另一条": AWEME2}
    }
    rows = [
        ("insp_v", f2.legacy_platform_id_for(video), None),
        ("insp_l", f2.legacy_platform_id_for(live), None),
    ]

    plan, _ = bf.build_plan([video, live], alignment, rows)

    by_id = {e["inspiration_id"]: e for e in plan}
    assert by_id["insp_v"]["new_source_url"] == f"https://www.douyin.com/video/{AWEME}"
    assert by_id["insp_l"]["new_source_url"] == f"https://www.douyin.com/note/{AWEME2}"


# ═══════════════════════════════════════════════════════════════
#  写库与回滚
# ═══════════════════════════════════════════════════════════════


def test_apply_plan_writes_and_is_idempotent(tmp_path):
    """落库后再跑一次：无事可做（旧 ID 已不存在）→ 幂等。"""
    item = _item()
    legacy = f2.legacy_platform_id_for(item)
    db = _lib(tmp_path, [("insp1", legacy, None, None)])
    alignment = {"A": {"2026-09-14 10-31-14_标题": AWEME}}

    plan, _ = bf.build_plan([item], alignment, bf._load_rows(db))
    result = bf.apply_plan(plan, db, tmp_path / "batches")

    assert result["updated"] == 1 and result["url_only"] == 0
    assert _rows(db) == [
        ("insp1", f"f2:{AWEME}#image1", f"https://www.douyin.com/note/{AWEME}")
    ]

    # 第二次：旧 ID 已不存在 → 没有可改的行
    plan2, skipped2 = bf.build_plan([item], alignment, bf._load_rows(db))
    assert plan2 == []
    assert skipped2["库内无对应行（未导入该文件 / 已删除）"] == 1


def test_apply_plan_never_touches_trashed_rows(tmp_path):
    """垃圾桶素材不动（其链接无意义，且不该扰动垃圾桶状态）。"""
    item = _item()
    legacy = f2.legacy_platform_id_for(item)
    db = _lib(tmp_path, [("trash1", legacy, None, "2026-01-01 00:00:00")])
    alignment = {"A": {"2026-09-14 10-31-14_标题": AWEME}}

    rows = bf._load_rows(db)  # 只取 deleted_at IS NULL
    assert rows == []
    plan, _ = bf.build_plan([item], alignment, rows)
    assert plan == []
    assert _rows(db) == [("trash1", legacy, None)]  # 原样


def test_apply_plan_skips_row_changed_elsewhere(tmp_path):
    """WHERE 带原平台 ID：行被别处改过时不误伤（rowcount=0 则跳过）。"""
    item = _item()
    legacy = f2.legacy_platform_id_for(item)
    db = _lib(tmp_path, [("insp1", "f2:手动改过#image1", None, None)])
    # 计划里仍按旧 ID 构造（模拟「读到报告后、写库前有人改过」）
    plan = [
        {
            "inspiration_id": "insp1",
            "kind": "full",
            "old_platform_id": legacy,
            "new_platform_id": f"f2:{AWEME}#image1",
            "old_source_url": None,
            "new_source_url": f"https://www.douyin.com/note/{AWEME}",
        }
    ]

    result = bf.apply_plan(plan, db, tmp_path / "batches")

    assert result["updated"] == 0
    assert _rows(db) == [("insp1", "f2:手动改过#image1", None)]


def test_apply_plan_writes_manifest_and_rollback_restores(tmp_path):
    """落库落清单；按清单回滚能原样还原（ID 与链接都要回到旧值）。"""
    item = _item()
    legacy = f2.legacy_platform_id_for(item)
    db = _lib(tmp_path, [("insp1", legacy, "旧链接", None)])
    alignment = {"A": {"2026-09-14 10-31-14_标题": AWEME}}

    plan, _ = bf.build_plan([item], alignment, bf._load_rows(db))
    result = bf.apply_plan(plan, db, tmp_path / "batches")

    manifest = result["manifest"]
    saved = json.loads(open(manifest, encoding="utf-8").read())
    assert saved["applied"] == 1
    assert saved["planned"] == 1
    assert saved["entries"][0]["old_platform_id"] == legacy
    assert _rows(db)[0][2] == f"https://www.douyin.com/note/{AWEME}"

    back = bf.rollback(__import__("pathlib").Path(manifest), db)

    assert back == {"reverted": 1, "missing": 0}
    assert _rows(db) == [("insp1", legacy, "旧链接")]


def test_apply_plan_skips_unchanged_rows(tmp_path):
    """已是目标状态的行不重复写（省 IO，也让「无事可做」可观测）。

    「无变化」出现在 url_only 项上：同作品的重复行如果链接已经对了，就不该再写一次。
    """
    url = f"https://www.douyin.com/note/{AWEME}"
    db = _lib(tmp_path, [("insp1", "f2:legacy#image1", url, None)])
    plan = [
        {
            "inspiration_id": "insp1",
            "kind": "url_only",
            "old_platform_id": "f2:legacy#image1",
            "new_platform_id": "f2:legacy#image1",
            "old_source_url": url,
            "new_source_url": url,
        }
    ]

    result = bf.apply_plan(plan, db, tmp_path / "batches")

    assert result["updated"] == 0 and result["url_only"] == 0
    assert result["unchanged"] == 1
    assert _rows(db) == [("insp1", "f2:legacy#image1", url)]


# ═══════════════════════════════════════════════════════════════
#  磁盘扫描
# ═══════════════════════════════════════════════════════════════


def test_collect_items_scans_post_and_like(tmp_path):
    """发布与点赞两个目录都要扫（对齐键与模式无关）。"""
    _write(tmp_path / "Download/douyin/post/A/2026-01-01 10-00-00_甲_image_1.webp")
    _write(tmp_path / "Download/douyin/like/我的账号/不养羊_2026-01-02 10-00-00_乙_image_1.webp")

    items = bf.collect_items(tmp_path)

    assert len(items) == 2
    assert {i.author_key for i in items} == {"A", "不养羊"}


def test_collect_items_tolerates_missing_dirs(tmp_path):
    assert bf.collect_items(tmp_path) == []


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════


def test_main_preview_does_not_write(tmp_path, capsys, monkeypatch):
    """默认只读：不写库、但要打印计划与示例。"""
    _write(tmp_path / "Download/douyin/post/A/2026-09-14 10-31-14_标题_image_1.webp")
    item = _item()
    legacy = f2.legacy_platform_id_for(item)
    db = _lib(tmp_path, [("insp1", legacy, None, None)])
    report = tmp_path / "r.json"
    report.write_text(
        json.dumps({"authors": {"A": {"unique": [["2026-09-14 10-31-14_标题", AWEME]]}}}),
        encoding="utf-8",
    )

    code = bf.main(
        ["--f2-dir", str(tmp_path), "--db", str(db), "--report", str(report)]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "预览模式，未写库" in out
    assert "改 ID + 补链接 1 行" in out
    assert _rows(db) == [("insp1", legacy, None)]  # 没动


def test_main_apply_then_rollback(tmp_path, capsys, monkeypatch):
    _write(tmp_path / "Download/douyin/post/A/2026-09-14 10-31-14_标题_image_1.webp")
    item = _item()
    legacy = f2.legacy_platform_id_for(item)
    db = _lib(tmp_path, [("insp1", legacy, None, None)])
    report = tmp_path / "r.json"
    report.write_text(
        json.dumps({"authors": {"A": {"unique": [["2026-09-14 10-31-14_标题", AWEME]]}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bf.settings, "storage_root", tmp_path)

    code = bf.main(
        ["--f2-dir", str(tmp_path), "--db", str(db), "--report", str(report), "--apply"]
    )

    assert code == 0
    assert "已落库" in capsys.readouterr().out
    assert _rows(db)[0][1] == f"f2:{AWEME}#image1"
    manifests = list((tmp_path / "import_batches").glob("backfill_f2_aweme_*.json"))
    assert len(manifests) == 1

    code = bf.main(["--db", str(db), "--rollback", str(manifests[0])])
    assert code == 0
    assert _rows(db) == [("insp1", legacy, None)]
