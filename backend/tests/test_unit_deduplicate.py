"""智能去重（deduplicate）评分与删除逻辑单测。

覆盖 `_score_groups`（保留建议 + 评分规则 + 平局 + 保留文件缺失兜底）
与 `_delete_files`（物理删除 + 释放空间统计）。均为纯函数，无需数据库。
"""

from datetime import datetime, timedelta, timezone

from app.services.task_runners.deduplicate import _delete_files, _score_groups


def _file(
    fid: str,
    *,
    path: str = "img/a.jpg",
    size: int = 1000,
    favorite: bool = False,
    thumb: str | None = None,
    created: datetime | None = None,
    source_url: str | None = None,
) -> dict:
    """构造 build_hash_map(include_meta=True) 产出的文件条目结构。"""
    return {
        "id": fid,
        "file_path": path,
        "size_bytes": size,
        "thumbnail_path": thumb,
        "is_favorite": favorite,
        "created_at": created or datetime.now(timezone.utc).replace(tzinfo=None),
        "source_url": source_url,
    }


def test_score_groups_keeps_highest_scored(tmp_path):
    """评分最高者保留：有标签(+100) + AI 已分析(+30)。"""
    created = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    (tmp_path / "img").mkdir(parents=True, exist_ok=True)
    (tmp_path / "img" / "plain.jpg").write_bytes(b"x")
    (tmp_path / "img" / "rich.jpg").write_bytes(b"x")
    group = [(
        "h1",
        [
            _file("plain", path="img/plain.jpg", created=created),
            _file("rich", path="img/rich.jpg", created=created),
        ],
    )]

    details, ids_to_delete, files_to_delete = _score_groups(
        group, tagged_ids={"rich"}, analyzed_ids={"rich"}, storage_root=tmp_path
    )

    assert ids_to_delete == ["plain"]
    assert files_to_delete == [("img/plain.jpg", None)]
    assert details[0]["kept"]["id"] == "rich"
    assert details[0]["kept"]["score"] == 130
    assert "有标签" in details[0]["kept"]["reasons"]


def test_score_groups_prefers_copy_with_source_url(tmp_path):
    """两条完全相同的副本评分打平时：保留有来源链接（真实作品 ID）的那条。

    回归（2026-09 f2 点赞重复入库的存量清理）：同一素材被入库两次，一条带真实作品 ID
    与原帖链接、一条是文件名合成 ID 且无链接。两者标签/收藏/分析/缩略图完全相同，
    旧规则按「创建更早 → ID 更小」决定，可能保留没有链接的那条 —— 清理重复就把
    能点回抖音原帖的入口一起删掉了。
    """
    (tmp_path / "img").mkdir(parents=True, exist_ok=True)
    (tmp_path / "img" / "linked.jpg").write_bytes(b"x")
    (tmp_path / "img" / "plain.jpg").write_bytes(b"x")
    newer = datetime.now(timezone.utc).replace(tzinfo=None)
    older = newer - timedelta(days=1)
    group = [(
        "h1",
        [
            # 有链接的反而更晚创建：规则必须靠得来源链接的加分，而不是创建时间
            _file("linked", path="img/linked.jpg", created=newer, source_url="https://www.douyin.com/note/1"),
            _file("plain", path="img/plain.jpg", created=older),
        ],
    )]

    details, ids_to_delete, _ = _score_groups(group, set(), set(), tmp_path)

    assert ids_to_delete == ["plain"]
    assert details[0]["kept"]["id"] == "linked"
    assert "有来源链接" in details[0]["kept"]["reasons"]


def test_score_groups_tiebreak_earlier_created(tmp_path):
    """评分相同（均为 0）时，创建更早者保留。"""
    early = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    late = datetime.now(timezone.utc).replace(tzinfo=None)
    (tmp_path / "img").mkdir(parents=True, exist_ok=True)
    (tmp_path / "img" / "old.jpg").write_bytes(b"x")
    (tmp_path / "img" / "new.jpg").write_bytes(b"x")
    group = [(
        "h1",
        [
            _file("old", path="img/old.jpg", created=early),
            _file("new", path="img/new.jpg", created=late),
        ],
    )]

    details, ids_to_delete, _ = _score_groups(group, set(), set(), tmp_path)

    assert details[0]["kept"]["id"] == "old"
    assert ids_to_delete == ["new"]


def test_score_groups_fallback_when_keeper_missing(tmp_path):
    """保留文件磁盘缺失时，改选组内另一个磁盘存在的副本，避免全删。"""
    early = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    late = datetime.now(timezone.utc).replace(tzinfo=None)
    (tmp_path / "img").mkdir(parents=True, exist_ok=True)
    (tmp_path / "img" / "late.jpg").write_bytes(b"x")

    group = [(
        "h1",
        [
            _file("old", path="img/missing.jpg", created=early),  # 磁盘缺失
            _file("new", path="img/late.jpg", created=late),      # 磁盘存在
        ],
    )]

    details, ids_to_delete, _ = _score_groups(group, set(), set(), tmp_path)

    assert details[0]["kept"]["id"] == "new"
    assert ids_to_delete == ["old"]


def test_delete_files_frees_and_skips_missing(tmp_path):
    """删除主文件 + 缩略图并统计释放空间；缺失文件静默跳过。"""
    (tmp_path / "a.jpg").write_bytes(b"12345")  # 5 字节
    (tmp_path / "t.jpg").write_bytes(b"12")     # 2 字节缩略图

    freed = _delete_files(
        [("a.jpg", "t.jpg"), ("missing.jpg", None)], tmp_path
    )

    assert freed == 7
    assert not (tmp_path / "a.jpg").exists()
    assert not (tmp_path / "t.jpg").exists()
