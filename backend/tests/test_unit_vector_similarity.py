"""向量检索与相似推荐的测试（纯计算 + 混合排序 + 降级路径）。

这块的失败模式几乎全是「静默降级成空列表」——用户点「相似素材」什么都没发生，
也看不到任何错误。所以测试重点在**降级链路**：LanceDB 不可用、CLIP 不可用、
素材无向量、无标签时各自应该退到什么，而不是抛异常或返回错的结果。
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services.vector import similarity as sim


async def _insp(insp_id: str, *, media_type: str = "image", deleted: bool = False) -> str:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg",
                media_type=media_type,
                quality_status="approved",
                deleted_at=(
                    datetime.now(timezone.utc) if deleted else None
                ),
            )
        )
        await db.commit()
    return insp_id


async def _tag_links(insp_id: str, names: list[str]) -> None:
    async with async_session() as db:
        for name in names:
            tag = (
                await db.execute(select(Tag).where(Tag.name == name))
            ).scalar_one_or_none()
            if tag is None:
                tag = Tag(name=name, category="item", source="ai_generated")
                db.add(tag)
                await db.flush()
            db.add(
                InspirationTag(
                    inspiration_id=insp_id, tag_id=tag.id, confidence=0.9, source="ai_generated"
                )
            )
        await db.commit()


# ── 纯函数 ──


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ([1.0, 0.0], [1.0, 0.0], 1.0),
        ([1.0, 0.0], [0.0, 1.0], 0.0),
        ([1.0, 0.0], [-1.0, 0.0], -1.0),
        ([0.0, 0.0], [1.0, 0.0], 0.0),   # 零向量不参与相似度
        ([1.0, 0.0], [1.0], 0.0),        # 维度不一致返回 0 而不是抛错
    ],
)
def test_cosine_similarity(a, b, expected):
    assert sim.cosine_similarity(a, b) == pytest.approx(expected)


def test_count_shared_tags():
    class _Insp:
        tags = [
            type("T", (), {"tag_id": 1}),
            type("T", (), {"tag_id": 2}),
        ]

    assert sim._count_shared_tags(_Insp(), {1, 2, 3}) == 2
    assert sim._count_shared_tags(_Insp(), {3}) == 0


# ── 标签兜底检索 ──


async def test_find_similar_images_by_shared_tags(client):
    await _insp("src")
    await _tag_links("src", ["针织开衫", "米色"])
    await _insp("cand1")
    await _tag_links("cand1", ["针织开衫", "米色", "阔腿裤"])
    await _insp("cand2")
    await _tag_links("cand2", ["针织开衫"])
    await _insp("unrelated")
    await _tag_links("unrelated", ["运动鞋"])

    async with async_session() as db:
        results = await sim.find_similar_images(db, "src", top_k=10)

    ids = [r["id"] for r in results]
    assert ids[0] == "cand1"       # 共享 2 个标签，排在前面
    assert "cand2" in ids
    assert "unrelated" not in ids   # 无共享标签不出现
    assert results[0]["shared_tags"] == 2
    assert 0 < results[0]["similarity"] <= 1


async def test_find_similar_images_empty_when_source_has_no_tags(client):
    await _insp("bare")
    async with async_session() as db:
        assert await sim.find_similar_images(db, "bare") == []


async def test_find_similar_images_excludes_trash_and_self(client):
    await _insp("s2")
    await _tag_links("s2", ["针织开衫"])
    await _insp("alive")
    await _tag_links("alive", ["针织开衫"])
    await _insp("trashed", deleted=True)
    await _tag_links("trashed", ["针织开衫"])

    async with async_session() as db:
        results = await sim.find_similar_images(db, "s2")

    ids = [r["id"] for r in results]
    assert "alive" in ids
    assert "trashed" not in ids
    assert "s2" not in ids


# ── 混合检索：三级降级 ──


async def test_hybrid_falls_back_to_tags_when_lancedb_unavailable(client, monkeypatch):
    """LanceDB 不可用 → 纯标签兜底（这是最常见的情况：没装或没建表）。"""
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: False)

    await _insp("h1")
    await _tag_links("h1", ["针织开衫"])
    await _insp("h2")
    await _tag_links("h2", ["针织开衫"])

    async with async_session() as db:
        source = await db.get(Inspiration, "h1")
        results = await sim.find_similar_hybrid(db, source, top_k=5)

    assert [r["match_source"] for r in results] == ["tag"]
    assert results[0]["inspiration"].id == "h2"


async def test_hybrid_uses_visual_scores_and_weights(client, monkeypatch):
    """有向量时按 visual/tag 权重混合，并标注 match_source=hybrid / visual。"""
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)

    async def _vec(db, insp):
        return [1.0, 0.0]

    async def _search(kind, vec, limit):
        return [
            {"inspiration_id": "v1", "score": 0.9},
            {"inspiration_id": "v2", "score": 0.5},
        ]

    monkeypatch.setattr(sim, "_get_or_build_image_vector", _vec)
    monkeypatch.setattr(sim.vector_store, "search_vectors", _search)
    monkeypatch.setattr(sim.settings, "vector_similarity_weight", 0.7, raising=False)
    monkeypatch.setattr(sim.settings, "vector_tag_weight", 0.3, raising=False)

    await _insp("src2")
    await _tag_links("src2", ["针织开衫"])
    await _insp("v1")
    await _tag_links("v1", ["针织开衫"])   # 有共享标签 → hybrid
    await _insp("v2")                       # 无共享标签 → visual

    async with async_session() as db:
        source = await db.get(Inspiration, "src2")
        results = await sim.find_similar_hybrid(db, source, top_k=5)

    by_id = {r["inspiration"].id: r for r in results}
    assert by_id["v1"]["match_source"] == "hybrid"
    assert by_id["v2"]["match_source"] == "visual"
    # v1: 0.7*0.9 + 0.3*(1/1) = 0.93；v2: 0.7*0.5 + 0 = 0.35
    assert by_id["v1"]["similarity"] == pytest.approx(0.93, abs=1e-3)
    assert results[0]["inspiration"].id == "v1"


async def test_hybrid_skips_trashed_visual_hits(client, monkeypatch):
    """向量命中但素材已进垃圾桶/已消失 → 跳过（LanceDB 里有陈旧记录很常见）。"""
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)

    async def _vec(db, insp):
        return [1.0]

    async def _search(kind, vec, limit):
        return [{"inspiration_id": "gone", "score": 0.99}]

    monkeypatch.setattr(sim, "_get_or_build_image_vector", _vec)
    monkeypatch.setattr(sim.vector_store, "search_vectors", _search)

    await _insp("src3")
    async with async_session() as db:
        source = await db.get(Inspiration, "src3")
        results = await sim.find_similar_hybrid(db, source, top_k=5)

    assert all(r["inspiration"].id != "gone" for r in results)


async def test_hybrid_returns_empty_without_any_signal(client, monkeypatch):
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: False)

    await _insp("src4")
    async with async_session() as db:
        source = await db.get(Inspiration, "src4")
        assert await sim.find_similar_hybrid(db, source, top_k=5) == []


# ── 向量重建：全部静默降级 ──


async def test_rebuild_text_vector_degrades_when_lancedb_missing(client, monkeypatch):
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: False)
    await _insp("rb1")
    async with async_session() as db:
        assert await sim.rebuild_text_vector(db, "rb1") is False


async def test_rebuild_text_vector_returns_false_without_text(client, monkeypatch):
    """无标签 → 文本为空 → 不生成向量（否则会把空串编码进库）。"""
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)
    await _insp("rb2")

    async with async_session() as db:
        assert await sim.rebuild_text_vector(db, "rb2") is False


async def test_rebuild_text_vector_writes_on_success(client, monkeypatch):
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)

    async def _embed(text):
        return [0.1, 0.2]

    async def _upsert(kind, insp_id, vec):
        return True

    monkeypatch.setattr(sim, "generate_text_embedding", _embed)
    monkeypatch.setattr(sim.vector_store, "upsert_vector", _upsert)

    await _insp("rb3")
    await _tag_links("rb3", ["针织开衫"])

    async with async_session() as db:
        assert await sim.rebuild_text_vector(db, "rb3") is True


async def test_rebuild_text_vector_returns_false_when_embedding_fails(client, monkeypatch):
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)

    async def _embed(text):
        return None

    monkeypatch.setattr(sim, "generate_text_embedding", _embed)
    await _insp("rb4")
    await _tag_links("rb4", ["针织开衫"])

    async with async_session() as db:
        assert await sim.rebuild_text_vector(db, "rb4") is False


async def test_rebuild_image_vector_rejects_unsupported_media(client, monkeypatch):
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)
    await _insp("rb5", media_type="document")
    async with async_session() as db:
        assert await sim.rebuild_image_vector(db, "rb5") is False


async def test_rebuild_image_vector_returns_false_when_source_missing(client, monkeypatch):
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)

    async def _no_path(insp):
        return None

    monkeypatch.setattr(sim, "_resolve_vector_source_path", _no_path)
    await _insp("rb6")

    async with async_session() as db:
        assert await sim.rebuild_image_vector(db, "rb6") is False


async def test_rebuild_image_vector_writes_on_success(client, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)

    async def _path(insp):
        return Path("dummy.jpg")

    async def _embed(file_path):
        return [0.3]

    async def _upsert(kind, insp_id, vec):
        return True

    monkeypatch.setattr(sim, "_resolve_vector_source_path", _path)
    monkeypatch.setattr(sim, "generate_image_embedding", _embed)
    monkeypatch.setattr(sim.vector_store, "upsert_vector", _upsert)

    await _insp("rb7")
    async with async_session() as db:
        assert await sim.rebuild_image_vector(db, "rb7") is True


async def test_rebuild_inspiration_vectors_returns_both_flags(client, monkeypatch):
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: False)
    await _insp("rb8")
    await _tag_links("rb8", ["针织开衫"])

    async with async_session() as db:
        result = await sim.rebuild_inspiration_vectors(db, "rb8")

    assert result == {"text": False, "image": False}


async def test_get_or_build_image_vector_reuses_stored(client, monkeypatch):
    """库里已有向量时直接复用（重新编码一张图要几百毫秒，没必要）。"""
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)

    async def _get(kind, insp_id):
        return [0.5, 0.5]

    monkeypatch.setattr(sim.vector_store, "get_vector", _get)
    await _insp("rb9")

    async with async_session() as db:
        insp = await db.get(Inspiration, "rb9")
        assert await sim._get_or_build_image_vector(db, insp) == [0.5, 0.5]


async def test_get_or_build_image_vector_returns_none_for_unsupported(client, monkeypatch):
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)

    async def _none(kind, insp_id):
        return None

    monkeypatch.setattr(sim.vector_store, "get_vector", _none)
    await _insp("rb10", media_type="document")

    async with async_session() as db:
        insp = await db.get(Inspiration, "rb10")
        assert await sim._get_or_build_image_vector(db, insp) is None


# ── 批量回填（向量回填任务的核心）──


async def test_backfill_returns_error_without_lancedb(client, monkeypatch):
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: False)
    async with async_session() as db:
        result = await sim.backfill_all_vectors(db)
    assert "lancedb 未安装" in result["error"]


async def test_backfill_counts_text_and_image(client, monkeypatch):
    """一次回填：文本向量按标签生成，图像向量按文件生成，非多媒体素材单独计数。"""
    from pathlib import Path

    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)
    monkeypatch.setattr(sim.vector_store, "list_vector_ids", lambda kind: _empty())

    written: dict[str, list] = {"text": [], "image": []}

    async def _batch(kind, items):
        written[kind].extend(items)
        return len(items)

    async def _text(text):
        return [0.1]

    async def _image(file_path):
        return [0.2]

    async def _path(insp):
        return Path("dummy.jpg") if insp.media_type == "image" else None

    monkeypatch.setattr(sim.vector_store, "batch_upsert_vectors", _batch)
    monkeypatch.setattr(sim, "generate_text_embedding", _text)
    monkeypatch.setattr(sim, "generate_image_embedding", _image)
    monkeypatch.setattr(sim, "_resolve_vector_source_path", _path)

    await _insp("bf1")
    await _tag_links("bf1", ["针织开衫"])
    await _insp("bf2")
    await _tag_links("bf2", ["阔腿裤"])
    await _insp("bf3", media_type="document")
    await _tag_links("bf3", ["配件"])  # 有标签就有文本向量；只是没有图像可编码

    async with async_session() as db:
        stats = await sim.backfill_all_vectors(db)

    assert stats["processed"] == 3
    assert stats["text_added"] == 3       # 三条都有标签文本
    assert stats["image_added"] == 2      # 只有两条 image 有源文件
    assert stats["skipped_non_image"] == 1
    assert len(written["text"]) == 3
    assert len(written["image"]) == 2


async def test_backfill_incremental_skips_existing(client, monkeypatch):
    """增量模式：已有向量的素材直接跳过，不重复编码（一张图几百毫秒）。"""
    from pathlib import Path

    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)

    async def _ids(kind):
        return {"bf4"}

    async def _batch(kind, items):
        return len(items)

    async def _path(insp):
        return Path("dummy.jpg")

    monkeypatch.setattr(sim.vector_store, "list_vector_ids", _ids)
    monkeypatch.setattr(sim.vector_store, "batch_upsert_vectors", _batch)
    monkeypatch.setattr(sim, "_resolve_vector_source_path", _path)

    await _insp("bf4")
    await _tag_links("bf4", ["针织开衫"])

    async with async_session() as db:
        stats = await sim.backfill_all_vectors(db, incremental=True)

    assert stats["text_skipped"] == 1
    assert stats["image_skipped"] == 1
    assert stats["text_added"] == 0


async def test_backfill_counts_failures(client, monkeypatch):
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)
    monkeypatch.setattr(sim.vector_store, "list_vector_ids", lambda kind: _empty())

    async def _none_embed(text):
        return None

    async def _path(insp):
        return None  # 源文件缺失

    monkeypatch.setattr(sim, "generate_text_embedding", _none_embed)
    monkeypatch.setattr(sim, "_resolve_vector_source_path", _path)

    await _insp("bf5")
    await _tag_links("bf5", ["针织开衫"])

    async with async_session() as db:
        stats = await sim.backfill_all_vectors(db)

    assert stats["text_failed"] == 1
    assert stats["image_failed"] == 1


async def test_backfill_mode_filters(client, monkeypatch):
    """mode 只跑指定向量类型（分开补更可控，也避免一次跑太久）。"""
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)
    monkeypatch.setattr(sim.vector_store, "list_vector_ids", lambda kind: _empty())

    async def _batch(kind, items):
        return len(items)

    async def _text(text):
        return [0.1]

    async def _image(file_path):  # pragma: no cover - 不该被调用
        raise AssertionError("mode=text 不应生成图像向量")

    monkeypatch.setattr(sim.vector_store, "batch_upsert_vectors", _batch)
    monkeypatch.setattr(sim, "generate_text_embedding", _text)
    monkeypatch.setattr(sim, "generate_image_embedding", _image)

    await _insp("bf6")
    await _tag_links("bf6", ["针织开衫"])

    async with async_session() as db:
        stats = await sim.backfill_all_vectors(db, mode="text")

    assert stats["text_added"] == 1
    assert stats["image_added"] == 0
    assert stats["image_skipped"] == 0  # 没跑图像分支，不应累计


async def test_backfill_respects_limit(client, monkeypatch):
    monkeypatch.setattr(sim.vector_store, "is_lancedb_available", lambda: True)
    monkeypatch.setattr(sim.vector_store, "list_vector_ids", lambda kind: _empty())
    monkeypatch.setattr(sim, "generate_text_embedding", lambda text: _await_return([0.1]))
    monkeypatch.setattr(sim.vector_store, "batch_upsert_vectors", lambda kind, items: _await_len(items))

    for i in range(3):
        await _insp(f"bf7-{i}")
        await _tag_links(f"bf7-{i}", ["针织开衫"])

    async with async_session() as db:
        stats = await sim.backfill_all_vectors(db, limit=2)

    assert stats["processed"] == 2


async def _empty() -> set[str]:
    return set()


async def _await_return(value):
    return value


async def _await_len(items) -> int:
    return len(items)
