"""素材查询层边界测试：日期上界、评分排序、预加载、主色调统计、智能合集条件与列表排序。

`inspiration_query.py` 是素材库列表 / 搜索 / 智能合集的公共查询层：
- `build_filters_from_query_json` 的字段口径一旦漂移，智能合集内容会悄悄变多/变少；
- `list_inspirations` 的排序分支（largest / tag_count / favorite_time / 评分）此前没有单测，
  翻页顺序、tie-breaker 与「垃圾桶排除」都只靠人工验证；
- `inclusive_date_upper_bound` 是「截止日含当天」的唯一实现，回归会整体少一天。

本文件直接调服务层 + 临时库造数，锁住上述行为。
"""

import json
from datetime import datetime

from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    analysis_log_filter,
    latest_analysis_log_subquery,
)
from app.models.tag import InspirationTag, Tag
from app.services.inspiration_query import (
    RATING_SORTS,
    build_filters_from_query_json,
    inclusive_date_upper_bound,
    list_dominant_colors,
    list_inspirations,
    load_inspiration_full,
    load_live_inspiration,
    rating_sort_clauses,
    rating_sort_filter,
)

_BASE = datetime(2026, 1, 10, 9, 0, 0)


async def _insp(
    insp_id: str,
    *,
    created: datetime | None = None,
    updated: datetime | None = None,
    source: str = "douyin",
    media: str = "image",
    author: str | None = None,
    file_path: str | None = None,
    favorite: bool = False,
    favorited_at: datetime | None = None,
    rating: int = 0,
    dominant_colors: str | None = None,
    deleted: bool = False,
    ai_generated: bool = False,
) -> str:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type=source,
                media_type=media,
                source_author=author,
                file_path=file_path or f"images/q/{insp_id}.jpg",
                quality_status="approved",
                is_favorite=favorite,
                favorited_at=favorited_at,
                rating=rating,
                dominant_colors=dominant_colors,
                is_ai_generated=ai_generated,
                created_at=created or _BASE,
                updated_at=updated or created or _BASE,
                deleted_at=(created or _BASE) if deleted else None,
            )
        )
        await db.commit()
    return insp_id


async def _tag(insp_id: str, name: str) -> int:
    async with async_session() as db:
        tag = Tag(name=name)
        db.add(tag)
        await db.flush()
        db.add(InspirationTag(inspiration_id=insp_id, tag_id=tag.id, source="manual"))
        await db.commit()
        return tag.id


async def _log(insp_id: str, *, error: str | None = None, log_type: str = "analysis") -> None:
    async with async_session() as db:
        db.add(
            AIAnalysisLog(
                inspiration_id=insp_id,
                model_name="qwen3-vl:8b",
                log_type=log_type,
                raw_response="{}",
                error=error,
            )
        )
        await db.commit()


async def _match(query_json: dict) -> set[str]:
    """把智能合集条件套在真实查询上，返回命中的素材 ID 集合。"""
    async with async_session() as db:
        rows = (
            await db.execute(
                select(Inspiration).where(*build_filters_from_query_json(query_json))
            )
        ).scalars()
        return {row.id for row in rows.all()}


# ── inclusive_date_upper_bound ──


def test_inclusive_date_upper_bound_adds_one_day():
    assert inclusive_date_upper_bound("2026-01-15") == "2026-01-16"
    assert inclusive_date_upper_bound("2026-01-31") == "2026-02-01"
    assert inclusive_date_upper_bound("2026-12-31") == "2027-01-01"


def test_inclusive_date_upper_bound_passthrough_for_datetime_and_invalid():
    assert inclusive_date_upper_bound("2026-01-15 10:00:00") == "2026-01-15 10:00:00"
    # 非法日期（正则匹配但 date.fromisoformat 失败）原样返回：空结果好过 500
    assert inclusive_date_upper_bound("2026-13-40") == "2026-13-40"
    assert inclusive_date_upper_bound("") == ""


# ── 评分排序子句 ──


def test_rating_sort_clauses_cover_all_branches():
    high = str(rating_sort_clauses("rating")[0])
    low = str(rating_sort_clauses("rating_asc")[0])
    unrated = str(rating_sort_clauses("unrated_first")[0])

    assert "rating DESC" in high
    assert "rating ASC" in low
    assert "CASE" in unrated.upper()
    assert rating_sort_clauses("newest") == []
    for sort in RATING_SORTS:
        assert len(rating_sort_clauses(sort)) >= 3  # 排序键 + created_at/id tie-breaker


def test_rating_sort_filter_only_rating_asc():
    assert rating_sort_filter("rating_asc") is not None
    assert rating_sort_filter("rating") is None
    assert rating_sort_filter("unrated_first") is None
    assert rating_sort_filter("newest") is None


# ── 预加载：full vs live ──


async def test_load_inspiration_full_and_live_deleted_semantics(client):
    await _insp("q-live", created=_BASE)
    await _insp("q-deleted", created=_BASE, deleted=True)
    tag_id = await _tag("q-live", "通勤")

    async with async_session() as db:
        alive = await load_inspiration_full(db, "q-live")
        deleted = await load_inspiration_full(db, "q-deleted")
        live_deleted = await load_live_inspiration(db, "q-deleted")
        missing = await load_live_inspiration(db, "q-missing")
        assert alive is not None
        # 链式预加载：不预加载 tag 内层实体时这里会抛 InvalidRequestError
        assert alive.tags[0].tag.name == "通勤"
        assert alive.tags[0].tag_id == tag_id
        assert deleted is not None
        assert live_deleted is None
        assert missing is None


# ── list_dominant_colors ──


async def test_list_dominant_colors_counts_and_skips_invalid(client):
    await _insp("dc-1", dominant_colors=json.dumps(["#FFFFFF", "#000000"]))
    await _insp("dc-2", dominant_colors=json.dumps(["#000000"]))
    await _insp("dc-3", dominant_colors="not-json")
    await _insp("dc-4", dominant_colors=json.dumps({"a": 1}))  # 非数组，忽略
    await _insp("dc-5", dominant_colors=None)
    await _insp("dc-deleted", dominant_colors=json.dumps(["#FFFFFF"]), deleted=True)

    async with async_session() as db:
        colors = await list_dominant_colors(db)

    assert colors == [
        {"color": "#000000", "count": 2},
        {"color": "#FFFFFF", "count": 1},  # 垃圾桶素材不计
    ]

    async with async_session() as db:
        assert await list_dominant_colors(db, limit=1) == [
            {"color": "#000000", "count": 2}
        ]


async def test_list_dominant_colors_empty_library(client):
    async with async_session() as db:
        assert await list_dominant_colors(db) == []


# ── build_filters_from_query_json：字段口径 ──


async def _seed_filter_rows() -> dict[str, int]:
    """过滤条件用的四行素材（另一行在垃圾桶），返回两个标签 id。"""
    await _insp(
        "f-a",
        created=datetime(2026, 1, 10, 9, 0, 0),
        author="Alice",
        file_path="images/f/a.jpg",
        favorite=True,
        rating=5,
        dominant_colors=json.dumps(["#112233"]),
    )
    await _insp(
        "f-b",
        created=datetime(2026, 1, 15, 23, 30, 0),
        source="xiaohongshu",
        media="video",
        author="Bob",
        file_path="images/f/b.jpg",
        rating=3,
    )
    await _insp(
        "f-c",
        created=datetime(2026, 2, 1, 8, 0, 0),
        author="Cara",
        file_path="images/f/c.jpg",
    )
    await _insp("f-deleted", created=datetime(2026, 1, 20, 0, 0, 0), deleted=True)
    return {
        "通勤": await _tag("f-a", "通勤"),
        "休闲": await _tag("f-b", "休闲"),
    }


async def test_build_filters_without_conditions_excludes_trash(client):
    await _seed_filter_rows()
    assert await _match({}) == {"f-a", "f-b", "f-c"}


async def test_build_filters_non_dict_returns_only_not_deleted(client):
    await _seed_filter_rows()
    assert await _match(["not-a-dict"]) == {"f-a", "f-b", "f-c"}


async def test_build_filters_keyword_matches_author_file_and_tag_name(client):
    await _seed_filter_rows()
    assert await _match({"keyword": "Alice"}) == {"f-a"}
    assert await _match({"keyword": "c.jpg"}) == {"f-c"}
    assert await _match({"keyword": "通勤"}) == {"f-a"}
    # 空白/非字符串关键词忽略
    assert await _match({"keyword": "   "}) == {"f-a", "f-b", "f-c"}
    assert await _match({"keyword": 123}) == {"f-a", "f-b", "f-c"}


async def test_build_filters_tag_and_or_semantics(client):
    tags = await _seed_filter_rows()
    commute, casual = tags["通勤"], tags["休闲"]

    assert await _match({"tag_ids": [commute]}) == {"f-a"}
    assert await _match({"tag_ids": [commute, casual], "tag_mode": "or"}) == {"f-a", "f-b"}
    # AND 语义下没有任何素材同时有两个标签
    assert await _match({"tag_ids": [commute, casual], "tag_mode": "and"}) == set()
    # 非法 tag_mode 回退 AND；非 int 元素被过滤
    assert await _match({"tag_ids": [commute, casual], "tag_mode": "xor"}) == set()
    assert await _match({"tag_ids": [commute, "x"]}) == {"f-a"}


async def test_build_filters_source_media_favorite_rating(client):
    await _seed_filter_rows()

    assert await _match({"source_types": ["douyin"]}) == {"f-a", "f-c"}
    assert await _match({"source_types": ["douyin", 123, ""]}) == {"f-a", "f-c"}
    assert await _match({"source_types": []}) == {"f-a", "f-b", "f-c"}
    assert await _match({"media_type": "video"}) == {"f-b"}
    assert await _match({"is_favorite": True}) == {"f-a"}
    assert await _match({"is_favorite": False}) == {"f-b", "f-c"}
    assert await _match({"is_favorite": None}) == {"f-a", "f-b", "f-c"}
    assert await _match({"min_rating": 4}) == {"f-a"}
    # 0 / 6 / bool 都属于非法或无效下限，忽略
    assert await _match({"min_rating": 0}) == {"f-a", "f-b", "f-c"}
    assert await _match({"min_rating": 6}) == {"f-a", "f-b", "f-c"}
    assert await _match({"min_rating": True}) == {"f-a", "f-b", "f-c"}


async def test_build_filters_date_range_includes_end_day(client):
    await _seed_filter_rows()

    assert await _match({"start_date": "2026-01-15"}) == {"f-b", "f-c"}
    # 截止日含当天：f-b 在 15 日 23:30 也必须命中（回归「少一天」）
    assert await _match({"end_date": "2026-01-15"}) == {"f-a", "f-b"}
    assert await _match({"end_date": "2026-01-10"}) == {"f-a"}
    assert await _match({"start_date": "2026-01-10", "end_date": "2026-01-10"}) == {"f-a"}


async def test_build_filters_unknown_keys_ignored(client):
    await _seed_filter_rows()
    assert await _match({"unknown_key": "x", "tag_mode": "and"}) == {"f-a", "f-b", "f-c"}


# ── list_inspirations：筛选之外排序/分页分支 ──


async def _seed_sort_rows() -> None:
    await _insp(
        "s-new",
        created=datetime(2026, 3, 1),
        updated=datetime(2026, 3, 5),
        rating=5,
        favorite=True,
        favorited_at=datetime(2026, 3, 6),
    )
    await _insp(
        "s-mid",
        created=datetime(2026, 2, 1),
        updated=datetime(2026, 3, 10),
        rating=3,
        favorite=True,
        favorited_at=datetime(2026, 3, 9),
    )
    await _insp(
        "s-old",
        created=datetime(2026, 1, 1),
        updated=datetime(2026, 2, 1),
        rating=0,
    )
    await _insp("s-deleted", created=datetime(2026, 4, 1), deleted=True)
    # 标签数量：s-new 2 个、s-mid 1 个、s-old 0 个
    await _tag("s-new", "通勤")
    await _tag("s-new", "法式")
    await _tag("s-mid", "休闲")


async def _list_ids(**kwargs) -> tuple[list[str], int]:
    async with async_session() as db:
        rows, total = await list_inspirations(db, **kwargs)
        return [row.id for row in rows], total


async def test_list_inspirations_newest_and_unknown_sort(client):
    await _seed_sort_rows()

    ids, total = await _list_ids()
    assert ids == ["s-new", "s-mid", "s-old"]
    assert total == 3
    # 未识别的排序回退 created_at DESC
    assert (await _list_ids(sort="weird"))[0] == ["s-new", "s-mid", "s-old"]


async def test_list_inspirations_rating_sorts(client):
    await _seed_sort_rows()

    assert (await _list_ids(sort="rating"))[0] == ["s-new", "s-mid", "s-old"]
    # rating_asc 只排已评分（rating > 0）
    rated, rated_total = await _list_ids(sort="rating_asc")
    assert rated == ["s-mid", "s-new"]
    assert rated_total == 2
    # unrated_first：0 分在前
    assert (await _list_ids(sort="unrated_first"))[0][0] == "s-old"


async def test_list_inspirations_tag_count_and_favorite_time(client):
    await _seed_sort_rows()

    assert (await _list_ids(sort="tag_count"))[0] == ["s-new", "s-mid", "s-old"]
    # favorite_time：最近收藏在前，未收藏排最后
    favorites, _ = await _list_ids(sort="favorite_time")
    assert favorites == ["s-mid", "s-new", "s-old"]


async def test_list_inspirations_largest_sorts_by_real_file_size(client):
    await _insp("sz-large", created=datetime(2026, 1, 3), file_path="images/q/sz-large.jpg")
    await _insp("sz-small", created=datetime(2026, 1, 2), file_path="images/q/sz-small.jpg")
    await _insp("sz-missing", created=datetime(2026, 1, 1), file_path="images/q/sz-missing.jpg")
    root = settings.storage_root
    paths = {
        "sz-large": root / "images/q/sz-large.jpg",
        "sz-small": root / "images/q/sz-small.jpg",
    }
    paths["sz-large"].parent.mkdir(parents=True, exist_ok=True)
    paths["sz-large"].write_bytes(b"x" * 300)
    paths["sz-small"].write_bytes(b"x" * 50)
    try:
        page1, total = await _list_ids(sort="largest", page=1, size=2)
        page2, _ = await _list_ids(sort="largest", page=2, size=2)
        assert total == 3
        assert page1 == ["sz-large", "sz-small"]
        # 文件缺失按 0 字节处理，排最后（不抛异常）
        assert page2 == ["sz-missing"]
    finally:
        for path in paths.values():
            path.unlink(missing_ok=True)


async def test_list_inspirations_pagination_is_disjoint(client):
    await _seed_sort_rows()

    page1, total = await _list_ids(page=1, size=2)
    page2, _ = await _list_ids(page=2, size=2)

    assert total == 3
    assert page1 == ["s-new", "s-mid"]
    assert page2 == ["s-old"]
    assert set(page1).isdisjoint(page2)


async def test_list_inspirations_random_returns_all_rows(client):
    await _seed_sort_rows()

    ids, total = await _list_ids(sort="random")
    assert total == 3
    assert set(ids) == {"s-new", "s-mid", "s-old"}


async def test_list_inspirations_tag_status_filter(client):
    await _seed_sort_rows()

    tagged, tagged_total = await _list_ids(tag_status="tagged")
    untagged, untagged_total = await _list_ids(tag_status="untagged")

    assert set(tagged) == {"s-new", "s-mid"}
    assert tagged_total == 2
    assert untagged == ["s-old"]
    assert untagged_total == 1


async def test_list_inspirations_analysis_status_uses_latest_log(client):
    await _insp("an-ok", created=datetime(2026, 1, 1))
    await _insp("an-error", created=datetime(2026, 1, 2))
    await _insp("an-pending", created=datetime(2026, 1, 3))
    # 旧日志失败、最新一条成功：不应再判成 error（状态以最新日志为准）
    await _log("an-ok", error="旧失败")
    await _log("an-ok", error=None)
    await _log("an-error", error="模型超时")

    done, done_total = await _list_ids(analysis_status="done")
    assert set(done) == {"an-ok"}
    assert done_total == 1
    assert set((await _list_ids(analysis_status="error"))[0]) == {"an-error"}
    pending = set((await _list_ids(analysis_status="pending"))[0])
    assert pending == {"an-pending"}


async def test_analysis_log_filter_excludes_quality_check_logs(client):
    await _insp("qc-1", created=datetime(2026, 1, 1))
    await _log("qc-1", log_type="quality_check")
    async with async_session() as db:
        analyzed = (
            await db.execute(
                select(AIAnalysisLog.inspiration_id).where(analysis_log_filter())
            )
        ).all()
        latest = (
            await db.execute(select(latest_analysis_log_subquery().c.inspiration_id))
        ).all()
    assert analyzed == []
    assert latest == []


async def test_list_inspirations_filters_compose_with_sort(client):
    await _seed_sort_rows()

    ids, total = await _list_ids(is_favorite=True, sort="rating")
    assert ids == ["s-new", "s-mid"]
    assert total == 2

    ids, total = await _list_ids(source_type="douyin", page=1, size=50)
    assert {"s-new", "s-mid", "s-old"} <= set(ids)
    assert total == 3
