"""搜索路由（`routers/search.py`）的筛选分支与向量检索接口测试。

`search_inspirations` 是全站最复杂的查询组合（17 个参数），原覆盖 45%；没测到的都是
**筛选静默失效**的分支：日期范围、收藏、标签 AND/OR/排除、排序口径、分析状态。
向量/相似/建议接口此前完全没覆盖——它们依赖可选的 LanceDB/CLIP，失败时要给可读 503
而不是 500，上传临时图还要保证「用完即删、超限清理」。

另外本文件覆盖一个真实缺陷的回归：`sort=match_score` 但没传标签时，`include_ids`
未初始化会 NameError（修复后此处必须正常返回）。
"""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.models.tag import InspirationTag, Tag
from app.routers import search as search_router
from app.services import vector_store


async def _insp(**fields) -> str:
    inspiration_id = str(uuid.uuid4())
    payload = {
        "source_type": "manual_upload",
        "file_path": f"images/{inspiration_id}.jpg",
        "media_type": "image",
        # 统一默认时间，避免「最新在前」排序在同秒内不稳定
        "created_at": datetime(2026, 6, 1, 12, 0, 0),
    }
    payload.update(fields)
    async with async_session() as db:
        db.add(Inspiration(id=inspiration_id, **payload))
        await db.commit()
    return inspiration_id


async def _tag(name: str) -> int:
    async with async_session() as db:
        tag = Tag(name=name, category="free")
        db.add(tag)
        await db.commit()
        return tag.id


async def _link(inspiration_id: str, *tag_ids: int) -> None:
    async with async_session() as db:
        for tag_id in tag_ids:
            db.add(InspirationTag(inspiration_id=inspiration_id, tag_id=tag_id))
        await db.commit()


async def _log(inspiration_id: str, *, error: str | None = None, log_type: str = "analysis") -> None:
    async with async_session() as db:
        db.add(
            AIAnalysisLog(
                inspiration_id=inspiration_id,
                model_name="test-model",
                log_type=log_type,
                raw_response="{}",
                error=error,
            )
        )
        await db.commit()


PARAM_DEFAULTS = {
    "include_tags": None,
    "exclude_tags": None,
    "keyword": None,
    "dominant_color": None,
    "source_type": None,
    "media_type": None,
    "is_favorite": None,
    "analysis_status": None,
    "tag_status": None,
    "date_from": None,
    "date_to": None,
    "rating_min": None,
    "sort": "newest",
    "combine": "AND",
    "page": 1,
    "size": 50,
}


async def _search(**overrides):
    params = dict(PARAM_DEFAULTS)
    params.update(overrides)
    async with async_session() as db:
        return await search_router.search_inspirations(db=db, **params)


def _ids(out) -> set[str]:
    return {item.id for item in out.items}


class TestSearchFilters:
    async def test_media_and_favorite(self, client):
        image = await _insp(media_type="image")
        video = await _insp(media_type="video", file_path="videos/a.mp4")
        favorite = await _insp(is_favorite=True)

        assert _ids(await _search(media_type="video")) == {video}
        assert _ids(await _search(is_favorite=True)) == {favorite}
        assert _ids(await _search(is_favorite=False)) == {image, video}

    async def test_date_range(self, client):
        old = await _insp(created_at=datetime(2026, 1, 1, 8, 0, 0))
        new = await _insp(created_at=datetime(2026, 6, 1, 8, 0, 0))

        assert _ids(await _search(date_from="2026-03-01")) == {new}
        assert _ids(await _search(date_to="2026-03-01")) == {old}
        assert _ids(await _search(date_from="2026-01-01", date_to="2026-12-31")) == {old, new}

    async def test_dominant_color_and_rating(self, client):
        red = await _insp(dominant_colors='["红色", "米色"]', rating=5)
        blue = await _insp(dominant_colors='["蓝色"]', rating=2)

        assert _ids(await _search(dominant_color="红色")) == {red}
        assert _ids(await _search(rating_min=4)) == {red}
        assert blue not in _ids(await _search(rating_min=4))

    async def test_include_tags_and_or(self, client):
        tag_a = await _tag("法式")
        tag_b = await _tag("通勤")
        both = await _insp()
        only_a = await _insp()
        await _link(both, tag_a, tag_b)
        await _link(only_a, tag_a)

        assert _ids(await _search(include_tags="法式,通勤", combine="AND")) == {both}
        assert _ids(await _search(include_tags="法式,通勤", combine="OR")) == {both, only_a}
        assert _ids(await _search(include_tags="不存在的标签")) == set()  # 早退空结果

    async def test_exclude_tags(self, client):
        keep = await _insp()
        drop = await _insp()
        await _link(drop, await _tag("旅行"))

        assert _ids(await _search(exclude_tags="旅行")) == {keep}
        # 不存在的排除标签 = 不排除
        assert _ids(await _search(exclude_tags="不存在")) == {keep, drop}

    async def test_tag_status(self, client):
        tagged = await _insp()
        untagged = await _insp()
        await _link(tagged, await _tag("法式"))

        assert _ids(await _search(tag_status="tagged")) == {tagged}
        assert _ids(await _search(tag_status="untagged")) == {untagged}

    async def test_keyword_matches_author_and_file_and_tag(self, client):
        by_author = await _insp(source_author="穿搭博主小美")
        by_file = await _insp(file_path="images/法式风衣.jpg")
        by_tag = await _insp()
        await _link(by_tag, await _tag("复古港风"))

        assert _ids(await _search(keyword="小美")) == {by_author}
        assert _ids(await _search(keyword="法式风衣")) == {by_file}
        assert _ids(await _search(keyword="复古港风")) == {by_tag}

    async def test_analysis_status_latest_log_wins(self, client):
        done = await _insp()
        await _log(done)  # 成功日志
        failed = await _insp()
        await _log(failed, error="模型超时")
        never = await _insp()
        quality_only = await _insp()
        await _log(quality_only, log_type="quality_check")  # 质量审核不算「已分析」

        assert _ids(await _search(analysis_status="done")) == {done}
        assert _ids(await _search(analysis_status="error")) == {failed}
        pending = _ids(await _search(analysis_status="pending"))
        assert {never, quality_only} <= pending and done not in pending

    async def test_sort_tag_count_and_rating_asc(self, client):
        rich = await _insp(rating=3)
        await _insp()  # 无标签的对照行
        await _link(rich, await _tag("法式"), await _tag("通勤"))

        tag_sorted = await _search(sort="tag_count")
        assert tag_sorted.items[0].id == rich

        rated = await _search(sort="rating_asc")
        assert _ids(rated) == {rich}  # 未评分不参与 rating_asc

    async def test_sort_match_score_prefers_more_matched_tags(self, client):
        tag_a = await _tag("法式")
        tag_b = await _tag("通勤")
        two = await _insp()
        one = await _insp()
        await _link(two, tag_a, tag_b)
        await _link(one, tag_a)

        out = await _search(include_tags="法式,通勤", combine="OR", sort="match_score")
        assert [item.id for item in out.items] == [two, one]

    async def test_sort_match_score_without_tags_is_regression(self, client):
        """回归：sort=match_score 但没传 include_tags 时不能 NameError。"""
        await _insp()

        out = await _search(sort="match_score")

        assert out.total >= 1

    async def test_pagination(self, client):
        ids = [await _insp() for _ in range(3)]

        page1 = await _search(page=1, size=2)
        page2 = await _search(page=2, size=2)

        assert len(page1.items) == 2 and page1.total == 3
        assert len(page2.items) == 1
        assert set(i.id for i in page2.items) <= set(ids)


class TestVectorSearch:
    async def _row(self) -> str:
        return await _insp()

    async def test_validation_and_missing_dependency(self, client):
        with pytest.raises(HTTPException) as exc_info:
            await search_router.vector_search(text=None, file=None, top_k=20, db=None)
        assert exc_info.value.status_code == 400

        with pytest.raises(HTTPException) as exc_info:
            await search_router.vector_search(
                text="x", file=SimpleNamespace(), top_k=20, db=None
            )
        assert exc_info.value.status_code == 400

        orig = vector_store.is_lancedb_available
        vector_store.is_lancedb_available = lambda: False
        try:
            with pytest.raises(HTTPException) as exc_info:
                await search_router.vector_search(text="x", file=None, top_k=20, db=None)
            assert exc_info.value.status_code == 503
        finally:
            vector_store.is_lancedb_available = orig

    async def test_text_search_hits_and_skips_missing(self, client, monkeypatch):
        real = await self._row()

        async def _embed(_text):
            return [0.1, 0.2]

        async def _search_vectors(kind, _vec, top_k):
            assert kind == "text" and top_k == 5
            return [
                {"inspiration_id": real, "score": 0.9},
                {"inspiration_id": "已删除的素材", "score": 0.8},  # 不在线 → 跳过
            ]

        monkeypatch.setattr(search_router, "generate_text_embedding", _embed)
        monkeypatch.setattr(vector_store, "search_vectors", _search_vectors)
        monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)

        async with async_session() as db:
            out = await search_router.vector_search(text="法式风衣", file=None, top_k=5, db=db)

        assert out.query_type == "text"
        assert out.query_text == "法式风衣"
        assert out.total == 1
        assert out.items[0].inspiration.id == real

    async def test_text_embedding_unavailable_503(self, client, monkeypatch):
        async def _embed(_text):
            return None

        monkeypatch.setattr(search_router, "generate_text_embedding", _embed)
        monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)

        with pytest.raises(HTTPException) as exc_info:
            async with async_session() as db:
                await search_router.vector_search(text="x", file=None, top_k=5, db=db)
        assert exc_info.value.status_code == 503
        assert "文本向量不可用" in exc_info.value.detail

    async def test_image_search_uses_temp_file_and_cleans_it(self, client, monkeypatch, tmp_path):
        real = await self._row()
        temp_file = tmp_path / "upload.img"
        temp_file.write_bytes(b"fake-image")
        checked: dict = {}

        async def _save(_file):
            return temp_file

        async def _image_embed(*, file_path):
            checked["file_path"] = file_path
            return [0.3]

        async def _search_vectors(kind, _vec, _top_k):
            assert kind == "image"
            return [{"inspiration_id": real, "score": 0.7}]

        monkeypatch.setattr(search_router, "_save_temp_image", _save)
        monkeypatch.setattr(search_router, "generate_image_embedding", _image_embed)
        monkeypatch.setattr(vector_store, "search_vectors", _search_vectors)
        monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)

        async with async_session() as db:
            out = await search_router.vector_search(text=None, file=object(), top_k=5, db=db)

        assert out.query_type == "image"
        assert checked["file_path"] == str(temp_file)
        assert not temp_file.exists()  # 临时文件用完即删

    async def test_image_embedding_unavailable_503(self, client, monkeypatch, tmp_path):
        temp_file = tmp_path / "upload.img"
        temp_file.write_bytes(b"x")

        async def _save(_file):
            return temp_file

        async def _image_embed(**_kwargs):
            return None

        monkeypatch.setattr(search_router, "_save_temp_image", _save)
        monkeypatch.setattr(search_router, "generate_image_embedding", _image_embed)
        monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)

        with pytest.raises(HTTPException) as exc_info:
            async with async_session() as db:
                await search_router.vector_search(text=None, file=object(), top_k=5, db=db)
        assert exc_info.value.status_code == 503
        assert "图像向量不可用" in exc_info.value.detail

    async def test_vector_status_counts(self, client, monkeypatch):
        monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: False)
        monkeypatch.setattr(search_router, "get_text_embedding_status", lambda: {"available": False})
        monkeypatch.setattr(search_router, "get_image_embedding_status", lambda: {"available": False})

        async with async_session() as db:
            out = await search_router.vector_search_status(db=db)
        assert out.text_vector_count == 0 and out.image_vector_count == 0

        async def _count(kind):
            return 5 if kind == "text" else 3

        monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)
        monkeypatch.setattr(vector_store, "count_vectors", _count)
        async with async_session() as db:
            out = await search_router.vector_search_status(db=db)
        assert out.lancedb_available is True
        assert out.text_vector_count == 5
        assert out.image_vector_count == 3


class TestSimilarAndSuggest:
    async def test_similar_404_and_mapping(self, client, monkeypatch):
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await search_router.similar_inspirations("missing", 10, db)
        assert exc_info.value.status_code == 404

        source_id = await _insp()
        other_id = await _insp()

        async def _hybrid(_db, _source, _top_k):
            async with async_session() as inner:
                other = await inner.get(Inspiration, other_id)
            return [
                {
                    "inspiration": other,
                    "similarity": 0.88,
                    "shared_tags": 2,
                    "match_source": "hybrid",
                }
            ]

        monkeypatch.setattr(search_router, "find_similar_hybrid", _hybrid)
        async with async_session() as db:
            out = await search_router.similar_inspirations(source_id, 5, db)

        assert out.source.id == source_id
        assert out.similar[0].inspiration.id == other_id
        assert out.similar[0].similarity == 0.88

    async def test_suggestions_and_cooccurrence_delegate(self, client, monkeypatch):
        calls: list = []

        async def _suggest(_db, q, limit):
            calls.append(("suggest", q, limit))
            return [{"name": "法式"}]

        async def _cooccurrence(_db, tag_name, limit):
            calls.append(("co", tag_name, limit))
            return {"items": []}

        monkeypatch.setattr(search_router.search_suggest_service, "search_suggestions", _suggest)
        monkeypatch.setattr(search_router.search_suggest_service, "tag_cooccurrence", _cooccurrence)

        async with async_session() as db:
            assert await search_router.search_suggestions("法", 8, db) == [{"name": "法式"}]
            assert await search_router.tag_cooccurrence("法式", 10, db) == {"items": []}

        assert calls == [("suggest", "法", 8), ("co", "法式", 10)]


class _FakeUpload:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def read(self, _size: int = -1):
        return self._chunks.pop(0) if self._chunks else b""


class TestSaveTempImage:
    async def test_writes_chunks_to_temp_dir(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(search_router.settings, "storage_root", tmp_path)
        monkeypatch.setattr(search_router.settings, "max_image_upload_mb", 1)

        path = await search_router._save_temp_image(_FakeUpload([b"abc", b"def"]))

        assert path.exists()
        assert path.read_bytes() == b"abcdef"
        assert path.parent == tmp_path / "tmp"
        path.unlink()

    async def test_oversize_is_rejected_and_temp_removed(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(search_router.settings, "storage_root", tmp_path)
        monkeypatch.setattr(search_router.settings, "max_image_upload_mb", 0)  # 0MB 上限

        with pytest.raises(HTTPException) as exc_info:
            await search_router._save_temp_image(_FakeUpload([b"x"]))

        assert exc_info.value.status_code == 400
        assert "文件超过大小限制" in exc_info.value.detail
        leftovers = list((tmp_path / "tmp").glob("vec_*")) if (tmp_path / "tmp").exists() else []
        assert leftovers == []  # 失败时清理残留
