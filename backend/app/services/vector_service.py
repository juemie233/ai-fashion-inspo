"""向量检索服务编排层：存量向量回填、混合相似推荐。

- ``backfill_all_vectors``：为存量素材批量生成文本/图像向量（独立可触发，
  不依赖任务队列，未来可接入队列后改为后台执行）。
- ``find_similar_hybrid``：图像向量相似度 + 标签匹配加权排序，
  供 ``/api/search/similar/{id}`` 使用；图像向量不可用时回退纯标签匹配。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag
from app.services import vector_store
from app.services.embedding_service import (
    build_inspiration_text,
    find_similar_images,
    generate_image_embedding,
    generate_text_embedding,
)

logger = logging.getLogger(__name__)


async def _load_inspiration(
    db: AsyncSession, inspiration_id: str
) -> Inspiration | None:
    """加载素材（预加载标签），不存在时返回 None。

    必须显式 selectinload 标签，避免异步环境下访问未加载的
    关系触发 MissingGreenlet。
    """
    result = await db.execute(
        select(Inspiration)
        .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
        .where(Inspiration.id == inspiration_id)
    )
    return result.unique().scalar_one_or_none()


async def backfill_all_vectors(
    db: AsyncSession, mode: str = "all", limit: int = 0
) -> dict:
    """为存量素材批量生成文本/图像向量。

    参数:
        db: 数据库会话
        mode: "all" | "text" | "image"（只回填指定类型）
        limit: 处理条数上限，0 表示全部

    返回:
        统计字典（processed / text_added / image_added / 失败计数等）

    说明:
        文本向量基于素材标签拼接文本（build_inspiration_text）生成；
        图像向量基于素材图片文件生成（需 CLIP 可用）。
        该函数同步执行，素材量大时耗时较长，可放入脚本或后台任务运行。
    """
    if not vector_store.is_lancedb_available():
        return {"error": "lancedb 未安装，请先执行：pip install lancedb"}

    query = (
        select(Inspiration)
        .options(selectinload(Inspiration.tags).selectinload(InspirationTag.tag))
        .order_by(Inspiration.created_at.asc())
    )
    if limit > 0:
        query = query.limit(limit)

    result = await db.execute(query)
    inspirations = result.unique().scalars().all()

    stats = {
        "processed": 0,
        "text_added": 0,
        "text_failed": 0,
        "image_added": 0,
        "image_failed": 0,
        "skipped_non_image": 0,
        "errors": [],
    }

    for insp in inspirations:
        stats["processed"] += 1

        # 文本向量
        if mode in ("all", "text"):
            text = build_inspiration_text(insp)
            if text:
                vec = await generate_text_embedding(text)
                if vec:
                    ok = await vector_store.upsert_vector("text", insp.id, vec)
                    if ok:
                        stats["text_added"] += 1
                    else:
                        stats["text_failed"] += 1
                else:
                    stats["text_failed"] += 1

        # 图像向量
        if mode in ("all", "image"):
            if insp.media_type != "image":
                stats["skipped_non_image"] += 1
                continue
            full_path = settings.storage_root / insp.file_path
            if not full_path.exists():
                stats["image_failed"] += 1
                continue
            vec = await generate_image_embedding(file_path=str(full_path))
            if vec:
                ok = await vector_store.upsert_vector("image", insp.id, vec)
                if ok:
                    stats["image_added"] += 1
                else:
                    stats["image_failed"] += 1
            else:
                stats["image_failed"] += 1

    logger.info(
        f"向量回填完成: processed={stats['processed']} "
        f"text_added={stats['text_added']} image_added={stats['image_added']}"
    )
    return stats


async def _get_or_build_image_vector(db: AsyncSession, inspiration: Inspiration) -> list[float] | None:
    """获取素材的图像向量。

    优先读取 LanceDB 中已存储的向量；不存在且 CLIP 可用时现场生成并写回。
    全部失败返回 None。
    """
    if vector_store.is_lancedb_available():
        existing = await vector_store.get_vector("image", inspiration.id)
        if existing:
            return existing

    if inspiration.media_type != "image":
        return None

    full_path = settings.storage_root / inspiration.file_path
    if not full_path.exists():
        return None

    vec = await generate_image_embedding(file_path=str(full_path))
    if vec and vector_store.is_lancedb_available():
        await vector_store.upsert_vector("image", inspiration.id, vec)
    return vec


def _count_shared_tags(candidate: Inspiration, source_tag_ids: set[int]) -> int:
    """统计候选素材与源素材共享的标签数量。"""
    if not source_tag_ids:
        return 0
    candidate_ids = {t.tag_id for t in candidate.tags}
    return len(source_tag_ids & candidate_ids)


async def find_similar_hybrid(
    db: AsyncSession, source: Inspiration, top_k: int = 10
) -> list[dict]:
    """寻找相似素材（向量相似度 + 标签匹配加权排序）。

    策略:
        1. 优先用源素材图像向量在 LanceDB 检索（视觉相似），
           混合视觉分数与标签重合度（权重见 config.vector_similarity_weight/tag_weight）。
        2. 视觉结果不足 top_k 时，用纯标签匹配补充。
        3. 图像向量不可用/无数据时，整体回退纯标签匹配。

    返回:
        [{"inspiration": Inspiration, "similarity": float,
          "shared_tags": int, "match_source": "visual"|"hybrid"|"tag"}, ...]
    """
    source_tag_ids = {t.tag_id for t in source.tags}
    source_id = source.id

    out_items: list[dict] = []
    used_ids: set[str] = set()

    # 1) 图像向量检索 + 加权排序
    if vector_store.is_lancedb_available():
        query_vec = await _get_or_build_image_vector(db, source)
        if query_vec:
            visual_hits = await vector_store.search_vectors("image", query_vec, top_k * 2)
            for hit in visual_hits:
                if hit["inspiration_id"] == source_id:
                    continue
                cand_id = hit["inspiration_id"]
                cand = await _load_inspiration(db, cand_id)
                if cand is None:
                    continue
                shared = _count_shared_tags(cand, source_tag_ids)
                visual_score = hit["score"]
                tag_score = shared / len(source_tag_ids) if source_tag_ids else 0.0
                combined = (
                    settings.vector_similarity_weight * visual_score
                    + settings.vector_tag_weight * tag_score
                )
                out_items.append({
                    "inspiration": cand,
                    "similarity": round(combined, 4),
                    "shared_tags": shared,
                    "match_source": "hybrid" if shared > 0 else "visual",
                })
                used_ids.add(cand_id)

            out_items.sort(key=lambda x: x["similarity"], reverse=True)
            out_items = out_items[:top_k]

            # 2) 视觉结果不足时用标签匹配补充
            if len(out_items) < top_k:
                tag_hits = await find_similar_images(db, source_id, top_k)
                for item in tag_hits:
                    if item["id"] in used_ids or item["id"] == source_id:
                        continue
                    cand = await _load_inspiration(db, item["id"])
                    if cand is None:
                        continue
                    out_items.append({
                        "inspiration": cand,
                        "similarity": item["similarity"],
                        "shared_tags": item["shared_tags"],
                        "match_source": "tag",
                    })
                    used_ids.add(item["id"])
                    if len(out_items) >= top_k:
                        break

    # 3) 纯标签匹配兜底
    if not out_items:
        tag_hits = await find_similar_images(db, source_id, top_k)
        for item in tag_hits:
            cand = await _load_inspiration(db, item["id"])
            if cand is None:
                continue
            out_items.append({
                "inspiration": cand,
                "similarity": item["similarity"],
                "shared_tags": item["shared_tags"],
                "match_source": "tag",
            })

    return out_items
