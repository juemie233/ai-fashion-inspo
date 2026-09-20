"""近似重复检测服务：基于感知哈希（dHash）分组视觉相似图片。

仅做「候选分组 + 评分建议保留」，不自动删除——由前端并排预览后人工确认，
避免近似匹配的误删风险（与精确去重的自动删除策略区分）。

扫描规则（性能与覆盖平衡）：
- **全库随机抽样**：`ORDER BY RANDOM()` 每次覆盖不同素材，不再固定扫描
  「最新 N 张」——旧素材的近似重复同样会被发现，多次扫描结果不重复。
- **感知哈希缓存**：phash 首次计算后写入 `inspirations.phash`，后续扫描
  零解码（纯内存分组，秒级响应）；缺失哈希由 :func:`backfill_phash_cache`
  补算，**单次调用有时间预算**（`BACKFILL_TIME_BUDGET_SECONDS`），剩余部分
  交给后台任务 `phash_backfill` 或下一次扫描继续。
- 素材文件被替换（如手机图剪裁）后 phash 置空，下次扫描懒重算。
- 哈希计算为阻塞 I/O，统一放线程池执行。

⚠ 为什么补算必须有预算（2026-09 实测事故）：库里 11,568 张 f2 抖音原图没有 phash，
单张实测 **142 ms**（中位 0.25 MB、最大 1.92 MB 的大图解码），全量补齐约 **27 分钟**。
早期实现是「一次请求内 `while True` 补到一张不剩」，于是前端 / 反代先超时（用户看到
「接口异常」），而后端还在继续跑。现在扫描最多补 `BACKFILL_TIME_BUDGET_SECONDS` 秒，
并且**每轮都没算出哈希时立即停手**（文件缺失/损坏的行否则会让循环原地打转）。
"""

import asyncio
import time

from collections.abc import Callable

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
from app.utils.time import format_utc

# 默认阈值（768 位 RGB dHash 汉明距离）：≤32 视为近似重复（约 4% 差异内）
DEFAULT_THRESHOLD = 32
# 默认扫描上限（0 表示不限）；同步接口，超大库建议分批或后续改造为任务队列
DEFAULT_LIMIT = 1000
# 单次扫描最多补算的缺失 phash 数（单轮批大小）：控制单轮内存与单次线程池批处理规模
BACKFILL_PER_SCAN = 300
# 单次调用的补算**时间**预算（秒）。HTTP 接口必须在这个量级内返回：大库全量补算
# 需数十分钟（实测 11,568 张大图约 27 分钟），压在一次请求里必然超时。
# 剩余缺失由后台任务 phash_backfill（worker 执行，无请求超时约束）继续补齐。
BACKFILL_TIME_BUDGET_SECONDS = 5.0


async def _count_missing_phash(db: AsyncSession) -> int:
    """统计仍未缓存感知哈希的图片素材数（不含垃圾桶）。"""
    return (
        await db.execute(
            select(func.count(Inspiration.id)).where(
                NOT_DELETED,
                Inspiration.media_type == "image",
                Inspiration.phash.is_(None),
            )
        )
    ).scalar() or 0


async def backfill_phash_cache(
    db: AsyncSession,
    *,
    budget_seconds: float = BACKFILL_TIME_BUDGET_SECONDS,
    on_batch: Callable[[int, int], None] | None = None,
) -> dict:
    """补算缺失的感知哈希（写回 `inspirations.phash`），最多跑 ``budget_seconds`` 秒。

    幂等：只算 `phash IS NULL` 的行，算过的不再重算；每轮独立 commit。
    到点即返回（`complete=False`），由调用方决定继续（再点一次）还是交给后台任务。

    Args:
        db: 数据库会话。
        budget_seconds: 时间预算（秒）；``0`` 表示只统计不补算（扫描接口的「只看现状」用法）。
        on_batch: 每轮结束后的回调 ``(已补算总数, 仍未缓存数)``，供后台任务写进度。

    Returns:
        ``{"computed": 本次新算出的哈希数, "missing": 仍未缓存数,
        "complete": 是否已全部就绪, "unhashable": 算不出哈希的素材数}``。
        ``unhashable`` 指文件缺失/损坏等永远算不出哈希的行——**必须排除**，
        否则补算循环会一直查到同一批行原地打转。
    """
    storage_root = settings.storage_root
    started = time.monotonic()
    computed_total = 0
    unhashable: set[str] = set()

    while True:
        missing = await _count_missing_phash(db)
        if missing == 0:
            return {
                "computed": computed_total,
                "missing": 0,
                "complete": True,
                "unhashable": len(unhashable),
            }
        if budget_seconds <= 0 or time.monotonic() - started >= budget_seconds:
            return {
                "computed": computed_total,
                "missing": missing,
                "complete": False,
                "unhashable": len(unhashable),
            }

        # 每轮随机取一批：优先让不同素材先拿到哈希（扫描抽样才有意义），
        # 并排除已知算不出哈希的行（否则会反复命中同一批，永远没有进展）
        stmt = select(Inspiration.id, Inspiration.file_path).where(
            NOT_DELETED,
            Inspiration.media_type == "image",
            Inspiration.phash.is_(None),
        )
        if unhashable:
            stmt = stmt.where(Inspiration.id.notin_(sorted(unhashable)))
        rows = (
            await db.execute(stmt.order_by(func.random()).limit(BACKFILL_PER_SCAN))
        ).all()

        if not rows:
            # 剩下的行都算不出哈希：停手，交给调用方按 unhashable 处理
            return {
                "computed": computed_total,
                "missing": missing,
                "complete": False,
                "unhashable": len(unhashable),
            }

        def _compute_hashes(batch: list) -> list[tuple[str, str]]:
            """同步计算缺失素材的感知哈希（线程池执行，避免阻塞事件循环）。

            ``batch`` 显式传参（而不是闭包捕获循环变量）：避免「加进线程池时
            循环已进入下一轮」造成的错批问题。
            """
            out: list[tuple[str, str]] = []
            for mid, mpath in batch:
                if not mpath:
                    continue
                full = storage_root / mpath
                if not full.exists():
                    continue
                phash = perceptual_hash(full)
                if phash:
                    out.append((mid, phash))
            return out

        computed = await asyncio.to_thread(_compute_hashes, rows)
        if not computed:
            # 本轮一张都没算出来：记下这些行并进入下一轮（下一轮排除它们，
            # 因此不会原地打转；预算到点也会正常返回）
            unhashable.update(mid for mid, _path in rows)
            if on_batch is not None:
                on_batch(computed_total, missing)
            continue

        for mid, phash in computed:
            await db.execute(
                update(Inspiration).where(Inspiration.id == mid).values(phash=phash)
            )
        await db.commit()
        computed_total += len(computed)
        if on_batch is not None:
            on_batch(computed_total, max(0, missing - len(computed)))


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
                "created_at": format_utc(m["created_at"]),
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

    本接口带「phash 缓存补算」副作用（幂等，写回成功计算的哈希），但**有时间预算**：
    最多补 :data:`BACKFILL_TIME_BUDGET_SECONDS` 秒就返回，避免大库首扫把请求拖到超时
    （实测 11,568 张大图需约 27 分钟）。返回里的 ``missing`` / ``cache_complete``
    告诉调用方缓存是否已就绪：未就绪时本次扫描只覆盖已缓存部分，需要后台任务
    （``phash_backfill``）或再次扫描继续补齐。
    """
    storage_root = settings.storage_root

    total = (
        await db.execute(
            select(func.count(Inspiration.id)).where(
                NOT_DELETED, Inspiration.media_type == "image"
            )
        )
    ).scalar() or 0

    # ── 1) 补算缺失哈希（限时）──
    # 显式传预算（而不是依赖默认参数）：调用方/测试可以按需调大调小，
    # 后台任务则用更长预算一次补完
    backfill = await backfill_phash_cache(
        db, budget_seconds=BACKFILL_TIME_BUDGET_SECONDS
    )

    # ── 2) 全库随机抽样：仅取已有哈希缓存的素材参与分组 ──
    # commit 后同一个 session 的查询应当能看到已提交数据；
    # 避免在此处切换 session（async_session 上下文管理器会 auto-commit/rollback），
    # 否则可能读到旧快照或丢失采样结果。
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
        "backfilled": backfill["computed"],
        "cached_total": cached_total,
        # 缓存就绪度：未就绪时本次只覆盖了已缓存部分（前端据此提示「先补齐」）
        "missing": backfill["missing"],
        "cache_complete": backfill["complete"],
        "unhashable": backfill["unhashable"],
    }
