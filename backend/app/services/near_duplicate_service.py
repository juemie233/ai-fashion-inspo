"""近似重复检测服务：基于感知哈希（dHash）分组视觉相似图片。

仅做「候选分组 + 评分建议保留」，不自动删除——由前端并排预览后人工确认，
避免近似匹配的误删风险（与精确去重的自动删除策略区分）。
"""

import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    NOT_DELETED,
    analysis_log_filter,
)
from app.models.tag import InspirationTag
from app.utils.image_hash import hamming_distance, perceptual_hash

# 默认阈值（64 位 dHash 汉明距离）：≤10 视为近似重复（约 15% 差异内）
DEFAULT_THRESHOLD = 10
# 默认扫描上限（0 表示不限）；同步接口，超大库建议分批或后续改造为任务队列
DEFAULT_LIMIT = 1000


def _chunked(ids: list[str], size: int = 500) -> list[list[str]]:
    """将 ID 列表按 size 分片，规避 SQLite IN(...) 变量上限。"""
    return [ids[i : i + size] for i in range(0, len(ids), size)]


async def _collect_scoring_ids(
    db: AsyncSession, all_ids: list[str]
) -> tuple[set[str], set[str]]:
    """批量查询「有标签」与「AI 分析成功」的素材 ID（用于保留评分）。"""
    tagged_ids: set[str] = set()
    analyzed_ids: set[str] = set()

    for chunk in _chunked(all_ids):
        tagged_result = await db.execute(
            select(InspirationTag.inspiration_id)
            .where(InspirationTag.inspiration_id.in_(chunk))
            .distinct()
        )
        tagged_ids.update(r[0] for r in tagged_result.all())

    for chunk in _chunked(all_ids):
        analyzed_result = await db.execute(
            select(AIAnalysisLog.inspiration_id)
            .where(
                analysis_log_filter(),
                AIAnalysisLog.inspiration_id.in_(chunk),
                (AIAnalysisLog.error.is_(None)) | (AIAnalysisLog.error == ""),
            )
            .distinct()
        )
        analyzed_ids.update(r[0] for r in analyzed_result.all())

    return tagged_ids, analyzed_ids


def _score(item: dict, tagged_ids: set[str], analyzed_ids: set[str]) -> int:
    """评分：有标签 +100、已收藏 +50、AI 已分析 +30、有缩略图 +10（同精确去重规则）。"""
    score = 0
    if item["id"] in tagged_ids:
        score += 100
    if item["is_favorite"]:
        score += 50
    if item["id"] in analyzed_ids:
        score += 30
    if item["thumbnail_path"]:
        score += 10
    return score


def _group(items: list[dict], threshold: int) -> list[dict]:
    """贪心分组：与各组代表哈希的汉明距离 ≤ 阈值则入组，否则新开一组。

    只返回成员 ≥ 2 的组；每组计算保留建议（评分最高者，平局取创建更早）。
    """
    clusters: list[dict] = []
    for item in items:
        placed = False
        for cluster in clusters:
            if hamming_distance(cluster["rep_phash"], item["phash"]) <= threshold:
                cluster["items"].append(item)
                placed = True
                break
        if not placed:
            clusters.append({"rep_phash": item["phash"], "items": [item]})

    groups: list[dict] = []
    for cluster in clusters:
        if len(cluster["items"]) < 2:
            continue
        members = cluster["items"]
        # 保留建议：评分降序，再按创建时间升序（更早优先），最后按 id
        members.sort(
            key=lambda f: (-f["score"], f["created_at"] or "", f["id"])
        )
        keeper = members[0]
        files = [
            {
                "id": m["id"],
                "file_path": m["file_path"],
                "thumbnail_path": m["thumbnail_path"],
                "is_favorite": m["is_favorite"],
                "created_at": m["created_at"].isoformat() if m["created_at"] else None,
                "size_bytes": m["size_bytes"],
                "score": m["score"],
                "distance": hamming_distance(cluster["rep_phash"], m["phash"]),
            }
            for m in members
        ]
        wasted_bytes = sum(f["size_bytes"] for f in files if f["id"] != keeper["id"])
        groups.append(
            {
                "rep_phash": cluster["rep_phash"],
                "files": files,
                "keeper_id": keeper["id"],
                "wasted_bytes": wasted_bytes,
            }
        )

    # 组按可回收空间降序，优先呈现收益最大的组
    groups.sort(key=lambda g: -g["wasted_bytes"])
    return groups


async def scan_near_duplicates(
    db: AsyncSession,
    *,
    limit: int = DEFAULT_LIMIT,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict:
    """扫描视觉近似重复的图片素材，返回分组候选（不删除）。"""
    storage_root = settings.storage_root

    total = (
        await db.execute(
            select(func.count(Inspiration.id)).where(
                NOT_DELETED, Inspiration.media_type == "image"
            )
        )
    ).scalar() or 0

    query = (
        select(
            Inspiration.id,
            Inspiration.file_path,
            Inspiration.thumbnail_path,
            Inspiration.is_favorite,
            Inspiration.created_at,
        )
        .where(NOT_DELETED, Inspiration.media_type == "image")
        .order_by(Inspiration.created_at.desc())
    )
    if limit and limit > 0:
        query = query.limit(limit)
    rows = (await db.execute(query)).all()

    def _compute() -> list[dict]:
        """同步计算每张图的感知哈希与文件大小（在线程池执行，避免阻塞事件循环）。"""
        items: list[dict] = []
        for row in rows:
            fpath = row[1]
            if not fpath:
                continue
            full = storage_root / fpath
            if not full.exists():
                continue
            phash = perceptual_hash(full)
            if phash is None:
                continue
            items.append(
                {
                    "id": row[0],
                    "file_path": fpath,
                    "thumbnail_path": row[2],
                    "is_favorite": row[3],
                    "created_at": row[4],
                    "phash": phash,
                    "size_bytes": full.stat().st_size,
                }
            )
        return items

    items = await asyncio.to_thread(_compute)

    tagged_ids, analyzed_ids = await _collect_scoring_ids(
        db, [it["id"] for it in items]
    )
    for it in items:
        it["score"] = _score(it, tagged_ids, analyzed_ids)

    groups = _group(items, threshold)

    return {
        "groups": groups,
        "scanned": len(items),
        "total": total,
        "truncated": total > len(items),
        "threshold": threshold,
    }
