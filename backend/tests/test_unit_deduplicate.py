"""智能去重（deduplicate）评分与删除逻辑单测。

覆盖 `_score_groups`（保留建议 + 评分规则 + 平局 + 保留文件缺失兜底）
与 `_delete_files`（物理删除 + 释放空间统计）。均为纯函数，无需数据库。
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

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


# ── 「手工投入」维度（2026-10-20 审查补：合集归属 / 人工审核 / 用户评分）──


def test_score_groups_keeps_copy_in_collection(tmp_path):
    """在合集里的副本必须优先保留：删了它，collection_items 会随 CASCADE 一起消失，
    合集静默少一张图（用户只会觉得「我明明加过」）。"""
    early = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    late = datetime.now(timezone.utc).replace(tzinfo=None)
    (tmp_path / "img").mkdir(parents=True, exist_ok=True)
    (tmp_path / "img" / "in_coll.jpg").write_bytes(b"x")
    (tmp_path / "img" / "plain.jpg").write_bytes(b"x")
    group = [(
        "h1",
        [
            # 在合集里但创建更晚、无标签：必须靠 +60 赢过「更早创建」的平局规则
            _file("in_coll", path="img/in_coll.jpg", created=late),
            _file("plain", path="img/plain.jpg", created=early),
        ],
    )]

    details, ids_to_delete, _ = _score_groups(
        group, set(), set(), tmp_path, in_collection={"in_coll"}
    )

    assert details[0]["kept"]["id"] == "in_coll"
    assert "在合集里" in details[0]["kept"]["reasons"]
    assert ids_to_delete == ["plain"]


def test_score_groups_keeps_human_reviewed_copy(tmp_path):
    """人工审核过的副本优先保留：那是人给的判定，删了不可恢复。"""
    (tmp_path / "img").mkdir(parents=True, exist_ok=True)
    (tmp_path / "img" / "reviewed.jpg").write_bytes(b"x")
    (tmp_path / "img" / "plain.jpg").write_bytes(b"x")
    same = datetime.now(timezone.utc).replace(tzinfo=None)
    group = [(
        "h1",
        [
            _file("plain", path="img/plain.jpg", created=same),
            _file("reviewed", path="img/reviewed.jpg", created=same),
        ],
    )]

    details, ids_to_delete, _ = _score_groups(
        group, set(), set(), tmp_path, human_reviewed={"reviewed"}
    )

    assert details[0]["kept"]["id"] == "reviewed"
    assert "人工审核过" in details[0]["kept"]["reasons"]
    assert ids_to_delete == ["plain"]


def test_score_groups_keeps_rated_copy_and_combines(tmp_path):
    """用户评分 +20；多个「手工投入」维度可叠加（合集 60 + 评分 20 + 缩略图 10）。"""
    (tmp_path / "img").mkdir(parents=True, exist_ok=True)
    (tmp_path / "img" / "rich.jpg").write_bytes(b"x")
    (tmp_path / "img" / "plain.jpg").write_bytes(b"x")
    same = datetime.now(timezone.utc).replace(tzinfo=None)
    group = [(
        "h1",
        [
            _file("rich", path="img/rich.jpg", created=same, thumb="img/rich_t.jpg"),
            _file("plain", path="img/plain.jpg", created=same),
        ],
    )]

    details, ids_to_delete, _ = _score_groups(
        group, set(), set(), tmp_path, in_collection={"rich"}, rated={"rich"}
    )

    kept = details[0]["kept"]
    assert kept["id"] == "rich"
    assert kept["score"] == 60 + 20 + 10
    assert set(kept["reasons"]) == {"在合集里", "用户评分", "有缩略图"}
    assert ids_to_delete == ["plain"]


def test_score_groups_tag_beats_collection(tmp_path):
    """权重顺序：有标签(100) > 在合集里(60)——标签是 AI+人工的共同产物，优先度最高。"""
    (tmp_path / "img").mkdir(parents=True, exist_ok=True)
    (tmp_path / "img" / "tagged.jpg").write_bytes(b"x")
    (tmp_path / "img" / "in_coll.jpg").write_bytes(b"x")
    same = datetime.now(timezone.utc).replace(tzinfo=None)
    group = [(
        "h1",
        [
            _file("tagged", path="img/tagged.jpg", created=same),
            _file("in_coll", path="img/in_coll.jpg", created=same),
        ],
    )]

    details, ids_to_delete, _ = _score_groups(
        group, {"tagged"}, set(), tmp_path, in_collection={"in_coll"}
    )

    assert details[0]["kept"]["id"] == "tagged"
    assert ids_to_delete == ["in_coll"]


async def test_execute_deduplicate_prefers_manual_investment(client):
    """端到端：两条同内容素材，保留「在合集里 / 人工审核过 / 有评分」的那条。

    回归 2026-10-20 审查发现：这三项手工投入此前完全没进评分。构造一组「旧规则会删错、
    新规则才对」的数据——A 有标签(100)、创建更早；B 在合集(60)+人工审核(45)+评分(20)=125。
    旧规则留 A 删 B（合集静默少一张、人工判定丢失），新规则留 B 删 A。
    """
    from app.config import settings
    from app.database import async_session
    from app.models.collection import Collection, CollectionItem
    from app.models.inspiration import Inspiration
    from app.models.tag import InspirationTag, Tag
    from app.services.task_runners.deduplicate import (
        create_deduplicate_task,
        execute_deduplicate,
    )

    root = settings.storage_root
    (root / "images").mkdir(parents=True, exist_ok=True)
    data = b"\xff\xd8\xff" + b"identical-bytes" * 64   # 两个文件字节完全一致 -> 同一 MD5 组
    (root / "images" / "tagged.jpg").write_bytes(data)
    (root / "images" / "invested.jpg").write_bytes(data)

    from datetime import datetime, timedelta

    async with async_session() as db:
        tagged = Inspiration(
            id="d-tagged", source_type="manual_upload", file_path="images/tagged.jpg",
            content_hash="same-hash", media_type="image", quality_status="pending",
            created_at=datetime(2026, 1, 1),
        )
        invested = Inspiration(
            id="d-invested", source_type="manual_upload", file_path="images/invested.jpg",
            content_hash="same-hash", media_type="image", quality_status="approved",
            quality_reviewed_by="human", rating=5, created_at=datetime(2026, 6, 1),
        )
        db.add_all([tagged, invested])
        await db.flush()

        tag = Tag(name="去重回归标签", category="free")
        db.add(tag)
        await db.flush()
        db.add(InspirationTag(inspiration_id="d-tagged", tag_id=tag.id, confidence=1.0, source="manual"))

        coll = Collection(name="去重回归合集")
        db.add(coll)
        await db.flush()
        db.add(CollectionItem(collection_id=coll.id, inspiration_id="d-invested", position=0))
        await db.commit()

    async with async_session() as db:
        task = await create_deduplicate_task(db)
        await execute_deduplicate(db, task)

    async with async_session() as db:
        left = (await db.execute(select(Inspiration.id))).scalars().all()
        # 合集成员行跟着保留者一起存活（CASCADE 不会误删）
        items = (await db.execute(select(CollectionItem.inspiration_id))).scalars().all()
        # 被删副本的标签关联随 CASCADE 清理，不留孤儿
        links = (await db.execute(select(InspirationTag.inspiration_id))).scalars().all()

    assert left == ["d-invested"]
    assert items == ["d-invested"]
    assert links == []
    # 磁盘：保留者文件在、被删者文件已删
    assert (root / "images" / "invested.jpg").exists()
    assert not (root / "images" / "tagged.jpg").exists()
    # 结果里能看到「为什么保留这条」
    assert task.result["files_deleted"] == 1
    assert task.result["groups_processed"] == 1
    kept = task.result["details"][0]["kept"]
    assert kept["id"] == "d-invested"
    assert {"在合集里", "人工审核过", "用户评分"} <= set(kept["reasons"])
