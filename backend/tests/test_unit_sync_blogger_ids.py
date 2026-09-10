"""博主 sec_user_id 回填脚本测试（scripts/sync_blogger_ids.py）。

不触碰真实素材库：库用临时 sqlite 构造，只验证解析、匹配计划与写入行为。
"""

import sqlite3
from pathlib import Path

from scripts import sync_blogger_ids as sb

SEC_A = "MS4wLjABAAAADCfmS4DNh9Q4FeGE-sVvhJ3S3ZCbS_0BkpmZFdgZ91ZKpAQpGhCtrtbrZ5NbfzA9"
SEC_B = "MS4wLjABAAAAqJoAuYZ-KvlM_b1diq7c2DN7JGgMHaORDPJX5VZZFZo"
SEC_C = "MS4wLjABAAAAGnFo0UV0bCP9CeXKKLfnS2o9H6mtLP9C20EmCM36-X9I4EF76cIFIUInXEgKESES"


# ── 清单解析 ──


def test_parse_pairs_two_line_form():
    text = f"夕木.\n{SEC_A}\n\n# 注释行\n不养羊\n{SEC_B}\n"
    pairs, warnings = sb.parse_pairs(text)
    assert pairs == [("夕木.", SEC_A), ("不养羊", SEC_B)]
    assert warnings == []


def test_parse_pairs_single_line_forms():
    """也接受「名字<TAB>ID」与「名字,ID」单行形式。"""
    pairs, _ = sb.parse_pairs(f"夕木.\t{SEC_A}\n不养羊,{SEC_B}\n")
    assert pairs == [("夕木.", SEC_A), ("不养羊", SEC_B)]


def test_parse_pairs_tolerates_quotes_and_whitespace():
    """粘贴来的清单常带引号/尾随空白（真实数据里出现过行尾多一个引号）。"""
    pairs, _ = sb.parse_pairs(f'"夕木."\n  {SEC_C}"  \n')
    assert pairs == [("夕木.", SEC_C)]


def test_parse_pairs_warns_on_orphans_and_bad_ids():
    pairs, warnings = sb.parse_pairs("孤立名字\n" + SEC_A + "\n没有ID的名字\n另一个名字\n")
    assert pairs == [("孤立名字", SEC_A)]
    assert any("没有对应 ID" in w for w in warnings)

    # 直接以 ID 开头（没有名字）→ 警告并跳过
    pairs, warnings = sb.parse_pairs(f"{SEC_A}\n名字\n{SEC_B}\n")
    assert pairs == [("名字", SEC_B)]
    assert any("ID 前没有名字" in w for w in warnings)


# ── 计划构建 ──


def _lib(tmp_path: Path, rows: list[tuple]) -> Path:
    """建最小 bloggers 表并写入 (id, name, platform, platform_user_id)。"""
    db = tmp_path / "lib.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE bloggers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
        "platform TEXT NOT NULL, platform_user_id TEXT, xhs_id TEXT, ip_location TEXT, "
        "profile_url TEXT, avatar_path TEXT, bio TEXT, source TEXT NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, person_group_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE inspiration_bloggers (inspiration_id TEXT, blogger_id INTEGER, "
        "confidence REAL, UNIQUE(inspiration_id, blogger_id))"
    )
    conn.execute(
        "CREATE TABLE inspirations (id TEXT, source_author TEXT, source_platform_id TEXT, "
        "deleted_at TEXT)"
    )
    for row in rows:
        conn.execute(
            "INSERT INTO bloggers (id, name, platform, platform_user_id, source, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'manual', '2026-01-01 00:00:00', "
            "'2026-01-01 00:00:00')",
            row,
        )
    conn.commit()
    conn.close()
    return db


def test_build_plan_updates_existing_by_normalized_name(tmp_path):
    """库内「美羊羊桑_」与清单「美羊羊桑～」归一化后同名 → 视为同一博主更新。"""
    db = _lib(
        tmp_path,
        [(1, "美羊羊桑_", "douyin", None), (2, "里香", "douyin", None), (3, "某小红书", "xiaohongshu", None)],
    )
    plan = sb.build_sync_plan([("美羊羊桑～", SEC_A), ("里香", SEC_B)], db_path=db)

    assert [u["id"] for u in plan.updates] == [1, 2]
    assert plan.updates[0]["profile_url"] == sb.PROFILE_URL_FMT.format(uid=SEC_A)
    assert plan.creates == [] and plan.conflicts == [] and plan.missing == []


def test_build_plan_marks_already_synced(tmp_path):
    """同一 sec_user_id 已在库 → 不重复写（幂等）。"""
    db = _lib(tmp_path, [(1, "夕木.", "douyin", SEC_A)])
    plan = sb.build_sync_plan([("夕木.", SEC_A)], db_path=db)
    assert plan.updates == []
    assert plan.unchanged and plan.unchanged[0]["id"] == 1


def test_build_plan_conflict_when_same_name_multiple_bloggers(tmp_path):
    """同名多个博主（如两个「折乙」）→ 记为冲突交人工，不擅自挑一个。"""
    db = _lib(tmp_path, [(1, "折乙", "douyin", None), (2, "折乙", "douyin", None)])
    plan = sb.build_sync_plan([("折乙", SEC_A)], db_path=db)
    assert plan.updates == []
    assert len(plan.conflicts) == 1 and len(plan.conflicts[0]["candidates"]) == 2


def test_build_plan_missing_and_create(tmp_path):
    db = _lib(tmp_path, [(1, "里香", "douyin", None)])
    plan = sb.build_sync_plan([("油炸土豆条🍟", SEC_A)], db_path=db)
    assert plan.missing and plan.creates == []

    plan = sb.build_sync_plan([("油炸土豆条🍟", SEC_A)], db_path=db, create_missing=True)
    assert plan.creates == [
        {"name": "油炸土豆条🍟", "uid": SEC_A, "profile_url": sb.PROFILE_URL_FMT.format(uid=SEC_A)}
    ]


# ── 执行 ──


def test_apply_updates_and_creates_and_binds(tmp_path):
    """端到端：更新已有博主 + 新建缺失博主 + 按 source_author 补绑已入库素材。"""
    db = _lib(tmp_path, [(1, "里香", "douyin", None), (2, "已有", "douyin", SEC_C)])
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO inspirations VALUES ('i1', '里香', 'f2:aaa#image1', NULL)")
    conn.execute("INSERT INTO inspirations VALUES ('i2', '里香', 'f2:aaa#image2', NULL)")
    conn.execute("INSERT INTO inspirations VALUES ('i3', '别人', 'f2:bbb#image1', NULL)")
    conn.commit()
    conn.close()

    plan = sb.build_sync_plan(
        [("里香", SEC_A), ("油炸土豆条🍟", SEC_B), ("已有", SEC_C)],
        db_path=db,
        create_missing=True,
    )
    assert len(plan.updates) == 1 and len(plan.creates) == 1 and len(plan.unchanged) == 1

    result = sb.apply_sync_plan(plan, db_path=db, bind_materials=True)
    assert result["updated"] == 1 and result["created"] == 1
    assert result["bound"] == 2  # 只绑 source_author='里香' 的两条

    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT name, platform_user_id FROM bloggers"))
    assert rows["里香"] == SEC_A
    assert rows["油炸土豆条🍟"] == SEC_B
    assert conn.execute("SELECT profile_url FROM bloggers WHERE name='里香'").fetchone()[0] == (
        sb.PROFILE_URL_FMT.format(uid=SEC_A)
    )
    links = conn.execute(
        "SELECT blogger_id, COUNT(*) FROM inspiration_bloggers GROUP BY 1"
    ).fetchall()
    assert links == [(1, 2)]
    conn.close()

    # 幂等：第二次跑没有可更新项，补绑也不会重复插入
    second = sb.build_sync_plan(
        [("里香", SEC_A), ("油炸土豆条🍟", SEC_B), ("已有", SEC_C)], db_path=db
    )
    assert second.updates == [] and second.creates == []
    again = sb.apply_sync_plan(second, db_path=db, bind_materials=True)
    assert again["updated"] == 0 and again["created"] == 0 and again["bound"] == 0
