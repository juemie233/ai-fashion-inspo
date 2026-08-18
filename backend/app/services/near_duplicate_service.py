"""近似重复检测服务：基于感知哈希（dHash）分组视觉相似图片。

仅做「候选分组 + 评分建议保留」，不自动删除——由前端并排预览后人工确认，
避免近似匹配的误删风险（与精确去重的自动删除策略区分）。

扫描规则（性能与覆盖平衡）：
- **全库随机抽样**：`ORDER BY RANDOM()` 每次覆盖不同素材，不再固定扫描
  「最新 N 张」——旧素材的近似重复同样会被发现，多次扫描结果不重复。
- **感知哈希缓存**：phash 首次计算后写入 `inspirations.phash`，后续扫描
  零解码（纯内存分组，秒级响应）；单次请求最多补算 `BACKFILL_PER_SCAN`
  张缺失哈希，存量库分批渐进补齐。
- 素材文件被替换（如手机图剪裁）后 phash 置空，下次扫描懒重算。
- 哈希计算为阻塞 I/O，统一放线程池执行。
"""

import asyncio

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    NOT_DELETED,
    analysis_log_filter,
)
from app.models.tag import InspirationTag
from app.services.task_runners.common import _chunked
from app.utils.image_hash import perceptual_hash

# 默认阈值（768 位 RGB dHash 汉明距离）：≤32 视为近似重复（约 4% 差异内）
DEFAULT_THRESHOLD = 32
# 默认扫描上限（0 表示不限）；同步接口，超大库建议分批或后续改造为任务队列
DEFAULT_LIMIT = 1000
# 单次扫描最多补算的缺失 phash 数：控制首跑/增量成本，缓存渐进完备后扫描零解码
BACKFILL_PER_SCAN = 300


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
    哈希已预转为 int，距离比较直接 XOR + bit_count，避免反复解析 hex。
    """
    clusters: list[dict] = []
    for item in items:
        placed = False
        for cluster in clusters:
            if (cluster["rep_phash_int"] ^ item["phash_int"]).bit_count() <= threshold:
                cluster["items"].append(item)
                placed = True
                break
        if not placed:
            clusters.append({"rep_phash_int": item["phash_int"], "items": [item]})

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
                "distance": (cluster["rep_phash_int"] ^ m["phash_int"]).bit_count(),
            }
            for m in members
        ]
        wasted_bytes = sum(f["size_bytes"] for f in files if f["id"] != keeper["id"])
        groups.append(
            {
                "rep_phash": f"{cluster['rep_phash_int']:0192x}",
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
    """扫描视觉近似重复的图片素材，返回分组候选（不删除）。

    本接口带「phash 缓存补算」副作用（幂等，写回成功计算的哈希）：
    - 全库图片随机抽样参与分组，每次覆盖不同素材；
    - 缺失哈希的素材按随机顺序补算（单次最多 BACKFILL_PER_SCAN 张）。
    """
    storage_root = settings.storage_root

    total = (
        await db.execute(
            select(func.count(Inspiration.id)).where(
                NOT_DELETED, Inspiration.media_type == "image"
            )
        )
    ).scalar() or 0

    # ── 1) 补算缺失哈希：随机抽缺失素材（首跑/增量成本受 BACKFILL_PER_SCAN 约束）──
    missing_rows = (
        await db.execute(
            select(Inspiration.id, Inspiration.file_path)
            .where(
                NOT_DELETED,
                Inspiration.media_type == "image",
                Inspiration.phash.is_(None),
            )
            .order_by(func.random())
            .limit(BACKFILL_PER_SCAN)
        )
    ).all()

    def _compute_hashes() -> list[tuple[str, str]]:
        """同步计算缺失素材的感知哈希（线程池执行，避免阻塞事件循环）。"""
        out: list[tuple[str, str]] = []
        for mid, mpath in missing_rows:
            if not mpath:
                continue
            full = storage_root / mpath
            if not full.exists():
                continue
            phash = perceptual_hash(full)
            if phash:
                out.append((mid, phash))
        return out

    computed = await asyncio.to_thread(_compute_hashes)
    if computed:
        for mid, phash in computed:
            await db.execute(
                update(Inspiration).where(Inspiration.id == mid).values(phash=phash)
            )
        await db.commit()

    # ── 2) 全库随机抽样：仅取已有哈希缓存的素材（含刚补算的）参与分组 ──
    query = (
        select(
            Inspiration.id,
            Inspiration.file_path,
            Inspiration.thumbnail_path,
            Inspiration.is_favorite,
            Inspiration.created_at,
            Inspiration.phash,
        )
        .where(NOT_DELETED, Inspiration.media_type == "image", Inspiration.phash.isnot(None))
        .order_by(func.random())
    )
    if limit and limit > 0:
        query = query.limit(limit)
    rows = (await db.execute(query)).all()

    items: list[dict] = []
    for row in rows:
        phash = row[5]
        if not phash:
            continue
        # 文件缺失时按 0 字节处理（磁盘文件被手动删除/孤儿时不应让扫描整体 500）
        size_bytes = 0
        if row[1]:
            try:
                full = storage_root / row[1]
                size_bytes = full.stat().st_size if full.exists() else 0
            except OSError:
                size_bytes = 0
        items.append(
            {
                "id": row[0],
                "file_path": row[1],
                "thumbnail_path": row[2],
                "is_favorite": row[3],
                "created_at": row[4],
                "phash_int": int(phash, 16),
                "size_bytes": size_bytes,
            }
        )

    tagged_ids, analyzed_ids = await _collect_scoring_ids(
        db, [it["id"] for it in items]
    )
    for it in items:
        it["score"] = _score(it, tagged_ids, analyzed_ids)

    groups = _group(items, threshold)

    # ── 3) 缓存进度统计（供前端展示「哈希缓存 N/全库 M」）──
    cached_total = (
        await db.execute(
            select(func.count(Inspiration.id)).where(
                NOT_DELETED, Inspiration.media_type == "image", Inspiration.phash.isnot(None)
            )
        )
    ).scalar() or 0

    return {
        "groups": groups,
        "scanned": len(items),
        "total": total,
        "truncated": total > len(items),
        "threshold": threshold,
        "backfilled": len(computed),
        "cached_total": cached_total,
    }
