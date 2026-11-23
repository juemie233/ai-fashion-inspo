"""合集智能扩充：按已有合集内容推荐「相似但尚未入夹」的素材（2026-11-22）。

实现口径（用户已确认）：
- 只服务**用户自建手动合集**（校验复用 ``collection_service.require_expandable``）；
- 一次性候选 + 人工勾选，**本模块只读**，不写库；
- 召回用「图像向量质心 + 标签画像」，打分沿用单素材相似检索的权重
  （``settings.vector_similarity_weight`` / ``vector_tag_weight``，默认 0.6 / 0.4）；
- 穿搭大标签（``tags.category='outfit'``）既参与打分，也可作为硬约束
  （``outfit_only``），这是「大标签 ↔ 收藏夹」联动的第一层。

不做 AI 调用：本地 8B 模型慢且输出不稳定，预览式扩充更适合可解释的向量 + 标签。
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.collection import CollectionItem
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services.collection_service import evaluate_smart_collection, require_expandable
from app.services.vector import store as vector_store  # 注意：不是 services.vector_store 薄壳（无 get_vectors_batch）
from app.services.inspiration_query import inspiration_load_options

logger = logging.getLogger(__name__)

# 种子上限：取合集前 N 个成员（按 position），避免上千成员的合集反复算质心
SEED_LIMIT = 200
# 向量召回池：后续再精排截断到 limit
VECTOR_POOL = 200
# 标签召回池（SQL 侧限制，避免把全库标签命中都拉出来）
TAG_POOL = 300
# 标签画像取前 N 个高频标签
TAG_PROFILE_LIMIT = 8
# 大标签画像上限
OUTFIT_PROFILE_LIMIT = 5
# 打分时共同标签数达到该值即满分（再多也不继续加分）
TAG_SCORE_CAP = 3
# 质心至少需要这么多条种子向量，否则视为向量不可用
MIN_SEED_VECTORS = 5


def _normalize(vector: list[float]) -> list[float] | None:
    """L2 归一化；全零向量返回 None（无法定义方向）。"""
    norm = sum(x * x for x in vector) ** 0.5
    if norm <= 0:
        return None
    return [x / norm for x in vector]


def _centroid(vectors: list[list[float]]) -> list[float] | None:
    """多向量求均值并归一化（维度不一致/为空时放弃）。"""
    usable = [v for v in vectors if v]
    if len(usable) < MIN_SEED_VECTORS:
        return None
    dim = len(usable[0])
    if any(len(v) != dim for v in usable):
        return None
    mean = [sum(column) / len(usable) for column in zip(*usable, strict=True)]
    return _normalize(mean)


def _reason(visual: float | None, shared_tags: list[str], outfit_tags: list[str]) -> str:
    """给用户看的命中理由（可解释性优先）。"""
    parts: list[str] = []
    if visual is not None:
        parts.append(f"视觉相似 {round(visual * 100)}%")
    if outfit_tags:
        parts.append(f"同大标签：{'、'.join(outfit_tags[:2])}")
    elif shared_tags:
        parts.append(f"共同标签：{'、'.join(shared_tags[:2])}")
    return " · ".join(parts) or "标签相近"


async def _seed_and_members(
    db: AsyncSession, collection
) -> tuple[list[str], set[str]]:
    """返回（种子 ID 前 SEED_LIMIT 条，当前全部内容 ID）。

    - 手动合集：按 position 取实体成员；
    - 半智能合集（``query_json`` 非空）：求值「规则匹配 ∪ 成员」——种子取其中
      最新的 SEED_LIMIT 条；成员集合用于排除候选，否则规则已命中的素材会被当成
      候选重复推荐。
    """
    if collection.query_json is not None:
        items, _total = await evaluate_smart_collection(db, collection)
        ordered = [item.id for item in items]
        return ordered[:SEED_LIMIT], set(ordered)

    rows = (
        await db.execute(
            select(CollectionItem.inspiration_id)
            .join(Inspiration, Inspiration.id == CollectionItem.inspiration_id)
            .where(
                CollectionItem.collection_id == collection.id,
                Inspiration.deleted_at.is_(None),
            )
            .order_by(CollectionItem.position.asc(), CollectionItem.added_at.asc())
        )
    ).scalars().all()
    return list(rows[:SEED_LIMIT]), set(rows)


async def _tag_profile(
    db: AsyncSession, seed_ids: list[str]
) -> tuple[list[tuple[int, str, str]], list[str]]:
    """种子画像：返回 (Top 标签 [(id, name, category)], 大标签名列表)。"""
    if not seed_ids:
        return [], []
    rows = (
        await db.execute(
            select(Tag.id, Tag.name, Tag.category, func.count().label("cnt"))
            .join(InspirationTag, InspirationTag.tag_id == Tag.id)
            .where(InspirationTag.inspiration_id.in_(seed_ids))
            .group_by(Tag.id)
            .order_by(func.count().desc(), Tag.name.asc())
            .limit(TAG_PROFILE_LIMIT)
        )
    ).all()
    profile = [(row[0], row[1], row[2]) for row in rows]
    outfit_tags = [name for _tid, name, category in profile if category == "outfit"]
    return profile, outfit_tags[:OUTFIT_PROFILE_LIMIT]


async def _auto_collection_members(db: AsyncSession) -> set[str]:
    """所有抖音自动维护合集（含二级收藏夹）的成员，供「排除其它抖音夹」使用。"""
    from app.models.collection import Collection

    rows = (
        await db.execute(
            select(CollectionItem.inspiration_id)
            .join(Collection, Collection.id == CollectionItem.collection_id)
            .where(Collection.auto_source.isnot(None))
        )
    ).scalars().all()
    return set(rows)


async def _candidate_tag_map(
    db: AsyncSession, candidate_ids: list[str]
) -> dict[str, list[tuple[str, str]]]:
    """候选素材的标签：{id: [(name, category), ...]}。"""
    if not candidate_ids:
        return {}
    rows = (
        await db.execute(
            select(InspirationTag.inspiration_id, Tag.name, Tag.category)
            .join(Tag, Tag.id == InspirationTag.tag_id)
            .where(InspirationTag.inspiration_id.in_(candidate_ids))
        )
    ).all()
    mapping: dict[str, list[tuple[str, str]]] = {}
    for insp_id, name, category in rows:
        mapping.setdefault(insp_id, []).append((name, category))
    return mapping


async def suggest_items(
    db: AsyncSession,
    collection_id: int,
    *,
    limit: int = 40,
    min_similarity: float = 0.5,
    outfit_only: bool = False,
    exclude_other_auto: bool = True,
) -> dict:
    """为手动合集生成扩充候选（只读，不写库）。

    Args:
        collection_id: 目标合集（必须是用户自建手动合集）。
        limit: 返回条数（1~100，路由层已校验）。
        min_similarity: 综合相似度下限（0~1）。
        outfit_only: 只保留与种子共享至少一个大标签的候选。
        exclude_other_auto: 排除已属于其它抖音自动合集的素材（默认开）。

    Returns:
        ``{items, total, profile}``；``items`` 元素为
        ``{inspiration, similarity, shared_tags, outfit_tags, reason, match_source}``。
    """
    collection = await require_expandable(db, collection_id)
    seeds, members = await _seed_and_members(db, collection)
    tag_profile, outfit_profile = await _tag_profile(db, seeds)
    top_tag_ids = [tag_id for tag_id, _name, _category in tag_profile]
    top_tag_names = {tag_id: name for tag_id, name, _category in tag_profile}
    outfit_names = set(outfit_profile)

    # ── 向量召回：种子向量质心 ──
    visual_scores: dict[str, float] = {}
    vector_available = False
    if seeds and vector_store.is_lancedb_available():
        try:
            vectors = await vector_store.get_vectors_batch("image", seeds)
            centroid = _centroid(list(vectors.values()))
            if centroid is not None:
                hits = await vector_store.search_vectors("image", centroid, VECTOR_POOL)
                vector_available = True
                for hit in hits:
                    visual_scores[str(hit["inspiration_id"])] = float(hit["score"])
        except Exception as exc:  # noqa: BLE001 —— 向量库异常只降级为纯标签匹配
            logger.warning("合集扩充：向量召回失败，回退纯标签：%s", exc)

    # ── 标签召回：含种子 Top 标签的素材 ──
    tag_candidates: list[str] = []
    if top_tag_ids:
        tag_candidates = (
            await db.execute(
                select(InspirationTag.inspiration_id)
                .join(Inspiration, Inspiration.id == InspirationTag.inspiration_id)
                .where(
                    InspirationTag.tag_id.in_(top_tag_ids),
                    Inspiration.deleted_at.is_(None),
                )
                .group_by(InspirationTag.inspiration_id)
                .order_by(func.count().desc())
                .limit(TAG_POOL)
            )
        ).scalars().all()

    candidate_ids = list({*visual_scores, *tag_candidates} - members)
    if not candidate_ids:
        return {
            "items": [],
            "total": 0,
            "profile": {
                "top_tags": [name for _tid, name, _cat in tag_profile],
                "outfit_tags": outfit_profile,
                "seeds_used": len(seeds),
                "vector_available": vector_available,
            },
        }

    # 只保留未删除素材（标签召回已过滤，向量召回需要再确认）
    live_ids = set(
        (
            await db.execute(
                select(Inspiration.id).where(
                    Inspiration.id.in_(candidate_ids),
                    Inspiration.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )
    candidate_ids = [cid for cid in candidate_ids if cid in live_ids]

    if exclude_other_auto:
        excluded = await _auto_collection_members(db)
        candidate_ids = [cid for cid in candidate_ids if cid not in excluded]
    if not candidate_ids:
        return {
            "items": [],
            "total": 0,
            "profile": {
                "top_tags": [name for _tid, name, _cat in tag_profile],
                "outfit_tags": outfit_profile,
                "seeds_used": len(seeds),
                "vector_available": vector_available,
            },
        }

    tag_map = await _candidate_tag_map(db, candidate_ids)
    visual_weight = settings.vector_similarity_weight
    tag_weight = settings.vector_tag_weight
    profile_names = set(top_tag_names.values())

    scored: list[dict] = []
    for candidate_id in candidate_ids:
        tags = tag_map.get(candidate_id, [])
        shared_names = [name for name, _cat in tags if name in profile_names]
        shared_outfit = [name for name, category in tags if category == "outfit" and name in outfit_names]
        if outfit_only and not shared_outfit:
            continue

        visual = visual_scores.get(candidate_id)
        tag_score = min(len(shared_names), TAG_SCORE_CAP) / TAG_SCORE_CAP
        if visual is None:
            # 纯标签命中（无向量）：只用标签分，避免把「无向量素材」系统性压低
            combined = tag_score
        else:
            combined = visual_weight * visual + tag_weight * tag_score
        if combined < min_similarity:
            continue
        scored.append(
            {
                "inspiration_id": candidate_id,
                "similarity": round(combined, 4),
                "visual": round(visual, 4) if visual is not None else None,
                "shared_tags": shared_names,
                "outfit_tags": shared_outfit,
                "match_source": "hybrid" if visual is not None else "tag",
            }
        )

    scored.sort(key=lambda item: item["similarity"], reverse=True)
    scored = scored[:limit]

    # 按得分顺序加载 ORM（保持顺序，供路由序列化）
    insp_rows = (
        await db.execute(
            select(Inspiration)
            .options(*inspiration_load_options())
            .where(Inspiration.id.in_([item["inspiration_id"] for item in scored]))
        )
    ).unique().scalars().all()
    by_id = {item.id: item for item in insp_rows}

    items = []
    for item in scored:
        inspiration = by_id.get(item["inspiration_id"])
        if inspiration is None:
            continue
        items.append(
            {
                "inspiration": inspiration,
                "similarity": item["similarity"],
                "shared_tags": item["shared_tags"],
                "outfit_tags": item["outfit_tags"],
                "match_source": item["match_source"],
                "reason": _reason(item["visual"], item["shared_tags"], item["outfit_tags"]),
            }
        )

    return {
        "items": items,
        "total": len(items),
        "profile": {
            "top_tags": [name for _tid, name, _cat in tag_profile],
            "outfit_tags": outfit_profile,
            "seeds_used": len(seeds),
            "vector_available": vector_available,
        },
    }
