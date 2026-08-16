"""相似度检索模块：混合相似推荐、标签兜底、向量回填等编排逻辑。

本模块把 ``embedding``（编码）与 ``store``（LanceDB 持久化）编排起来，
对外提供：
- ``find_similar_hybrid``：图像向量相似度 + 标签匹配加权排序，
  供 ``/api/search/similar/{id}`` 使用；图像向量不可用时回退纯标签匹配。
- ``find_similar_images``：纯标签兜底检索（原 embedding_service 提供）。
- ``cosine_similarity``：余弦相似度计算（原 embedding_service 提供）。
- ``backfill_all_vectors``：为存量素材批量生成文本/图像向量（独立可触发，
  不依赖任务队列，未来可接入队列后改为后台执行）。
- ``rebuild_text_vector``：重建单个素材的文本向量（标签/作者变更后调用）。
- ``rebuild_image_vector``：重建单个素材的图像向量（素材入库后调用）。
- ``rebuild_inspiration_vectors``：一次性重建单个素材的文本 + 图像向量
  （上传 / AI 分析完成后的统一入口）。
"""

import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag
from app.services.vector import store as vector_store
from app.services.vector.embedding import (
    build_inspiration_text,
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
        .where(
            Inspiration.id == inspiration_id,
            Inspiration.deleted_at.is_(None),
        )
    )
    return result.unique().scalar_one_or_none()


async def backfill_all_vectors(
    db: AsyncSession, mode: str = "all", limit: int = 0, incremental: bool = False
) -> dict:
    """为存量素材批量生成文本/图像向量。

    参数:
        db: 数据库会话
        mode: "all" | "text" | "image"（只回填指定类型）
        limit: 处理条数上限，0 表示全部
        incremental: 增量模式。为 True 时跳过「已存在向量」的素材（表 schema 按
            配置维度固定，存在即视为维度/模型未变），只回填缺失向量。

    返回:
        统计字典（processed / text_added / text_failed / text_skipped /
        image_added / image_failed / image_skipped / skipped_non_image 等）

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
        "text_skipped": 0,
        "image_added": 0,
        "image_failed": 0,
        "image_skipped": 0,
        "skipped_non_image": 0,
        "errors": [],
    }

    # 增量模式：预先一次性加载已有向量素材 ID（避免逐条查询放大开销）
    existing_text_ids: set[str] = set()
    existing_image_ids: set[str] = set()
    if incremental:
        if mode in ("all", "text"):
            existing_text_ids = await vector_store.list_vector_ids("text")
        if mode in ("all", "image"):
            existing_image_ids = await vector_store.list_vector_ids("image")

    # 批量累积待写入的向量，循环结束后一次性 add，避免逐条写造成版本膨胀
    text_items: list[tuple[str, list[float]]] = []
    image_items: list[tuple[str, list[float]]] = []

    for insp in inspirations:
        stats["processed"] += 1

        # 文本向量
        if mode in ("all", "text"):
            if insp.id in existing_text_ids:
                stats["text_skipped"] += 1
            else:
                text = build_inspiration_text(insp)
                if text:
                    vec = await generate_text_embedding(text)
                    if vec:
                        text_items.append((insp.id, vec))
                        stats["text_added"] += 1
                    else:
                        stats["text_failed"] += 1

        # 图像向量
        if mode in ("all", "image"):
            if insp.id in existing_image_ids:
                stats["image_skipped"] += 1
            elif insp.media_type != "image":
                stats["skipped_non_image"] += 1
            else:
                full_path = settings.storage_root / insp.file_path
                if not full_path.exists():
                    stats["image_failed"] += 1
                else:
                    vec = await generate_image_embedding(file_path=str(full_path))
                    if vec:
                        image_items.append((insp.id, vec))
                        stats["image_added"] += 1
                    else:
                        stats["image_failed"] += 1

    # 批量落盘（单次 add），写入行数与累积数不符时记 warning
    if text_items:
        written = await vector_store.batch_upsert_vectors("text", text_items)
        if written != len(text_items):
            logger.warning(f"文本向量批量写入 {written}/{len(text_items)} 条")
    if image_items:
        written = await vector_store.batch_upsert_vectors("image", image_items)
        if written != len(image_items):
            logger.warning(f"图像向量批量写入 {written}/{len(image_items)} 条")

    logger.info(
        f"向量回填完成: processed={stats['processed']} "
        f"text_added={stats['text_added']} (skipped {stats['text_skipped']}) "
        f"image_added={stats['image_added']} (skipped {stats['image_skipped']})"
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


async def rebuild_text_vector(db: AsyncSession, inspiration_id: str) -> bool:
    """重建单个素材的文本向量（标签/作者等语义字段变更后调用）。

    语义:
        文本向量由 build_inspiration_text（标签名 + 主色 + 作者）拼接生成。
        标签变更后必须重建，否则语义搜索结果陈旧。本函数重新加载素材与标签、
        生成最新文本 embedding 并 upsert。

    返回:
        True 表示重建成功；LanceDB 未安装、Ollama 不可用、素材不存在或
        无文本内容时静默降级返回 False，不抛错（不阻断标签写入主流程）。
    """
    if not vector_store.is_lancedb_available():
        return False
    insp = await _load_inspiration(db, inspiration_id)
    if insp is None:
        return False
    text = build_inspiration_text(insp)
    if not text:
        return False
    vec = await generate_text_embedding(text)
    if not vec:
        return False
    return await vector_store.upsert_vector("text", insp.id, vec)


async def rebuild_image_vector(db: AsyncSession, inspiration_id: str) -> bool:
    """重建单个素材的图像向量（素材入库后调用，保证相似推荐可用）。

    语义:
        图像向量由 CLIP 对素材原图编码生成，写入 LanceDB 图像向量表。
        素材不存在、非图片、LanceDB 未安装、CLIP 不可用或文件缺失时
        静默降级返回 False，不抛错（不阻断入库/分析主流程）。

    返回:
        True 表示重建成功；其余情况返回 False。
    """
    if not vector_store.is_lancedb_available():
        return False
    insp = await db.get(Inspiration, inspiration_id)
    if insp is None or insp.media_type != "image":
        return False
    full_path = settings.storage_root / insp.file_path
    if not full_path.exists():
        return False
    vec = await generate_image_embedding(file_path=str(full_path))
    if not vec:
        return False
    return await vector_store.upsert_vector("image", insp.id, vec)


async def rebuild_inspiration_vectors(db: AsyncSession, inspiration_id: str) -> dict:
    """重建单个素材的文本 + 图像向量（上传 / AI 分析完成后的统一入口）。

    文本向量依赖标签内容，无标签时自动跳过（返回 False）；图像向量仅对
    图片素材生成（视频跳过）。任一步骤失败均静默降级，不影响主流程。

    返回:
        统计字典 {"text": bool, "image": bool}，True 表示已成功写入或无需生成。
    """
    text_ok = await rebuild_text_vector(db, inspiration_id)
    image_ok = await rebuild_image_vector(db, inspiration_id)
    return {"text": text_ok, "image": image_ok}


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


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    if len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


async def find_similar_images(
    db: AsyncSession,
    inspiration_id: str,
    top_k: int = 10,
) -> list[dict]:
    """
    基于标签重合度寻找相似素材（纯标签兜底方案）。

    当图像向量不可用或无向量数据时，由 /api/search/similar/{id} 回退到本函数。
    """
    from app.models.tag import InspirationTag, Tag

    # 获取源素材的所有标签
    result = await db.execute(
        select(InspirationTag).where(
            InspirationTag.inspiration_id == inspiration_id
        )
    )
    source_tags = result.scalars().all()
    source_tag_ids = {t.tag_id for t in source_tags}

    if not source_tag_ids:
        return []

    # 查找共享标签的素材
    result = await db.execute(
        select(
            Inspiration,
            text("COUNT(inspiration_tags.tag_id) AS shared_count"),
        )
        .join(InspirationTag, Inspiration.id == InspirationTag.inspiration_id)
        .where(
            InspirationTag.tag_id.in_(source_tag_ids),
            Inspiration.id != inspiration_id,
            Inspiration.deleted_at.is_(None),
        )
        .group_by(Inspiration.id)
        .order_by(text("shared_count DESC"))
        .limit(top_k)
    )

    similar = []
    for row in result:
        insp = row[0]
        shared = row[1]
        similarity = shared / len(source_tag_ids) if source_tag_ids else 0
        similar.append({
            "id": insp.id,
            "file_path": insp.file_path,
            "thumbnail_path": insp.thumbnail_path,
            "shared_tags": int(shared),
            "similarity": round(similarity, 3),
        })

    return similar
