"""智能去重任务：全库 MD5 扫描 + 评分保留 + 物理删除冗余副本。

本模块包含「智能去重」（deduplicate）任务的创建与执行逻辑，
由 worker 进程（app/worker.py）通过 TASK_HANDLERS 分发表调度。
execute_deduplicate 原为 182 行巨型函数，现按阶段拆分为：
- _collect_tagged_analyzed_ids：批量查询「有标签/已分析」素材 ID
- _collect_manual_investment：批量查询「手工投入」（在合集里 / 人工审核过 / 打过评分）
- _score_groups：评分并决定每组保留副本
- _delete_files：物理删除冗余文件并统计释放空间
"""

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.collection import CollectionItem
from app.models.inspiration import AIAnalysisLog, Inspiration, analysis_log_filter
from app.models.tag import InspirationTag
from app.models.task import TaskQueue
from app.services.file_service import delete_files_counting
from app.services.scraper_seen_service import seal_urls
from app.services.task_runners.common import (
    _chunked,
    _delete_inspiration_vectors,
    utcnow,
)
from app.utils.file_hash import build_hash_map

logger = logging.getLogger(__name__)


async def _record_dedupe_audit(deleted_count: int, freed_bytes: int) -> None:
    """智能去重完成审计（独立会话，失败不影响任务结果）。"""
    try:
        from app.services.audit_service import record_audit_log

        await record_audit_log(
            action="deduplicate",
            count=deleted_count,
            freed_bytes=freed_bytes,
            detail="智能去重：物理删除冗余素材副本",
        )
    except Exception as e:
        logger.warning(f"写入去重审计失败（忽略）: {e}")


async def create_deduplicate_task(db: AsyncSession) -> TaskQueue:
    """创建「智能去重」任务记录，返回任务对象。

    去重无需预加载 ID：由 worker 执行时全库扫描并计算 MD5，
    因此创建时 total 未知（设为 0，执行阶段再更新）。
    """
    task = TaskQueue(
        type="deduplicate",
        status="pending",
        progress=0,
        total=0,
        done=0,
        result={"inspiration_ids": []},
        max_retries=2,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _collect_tagged_analyzed_ids(
    db: AsyncSession, all_ids: list[str]
) -> tuple[set[str], set[str]]:
    """批量查询「有标签」与「AI 分析成功」的素材 ID。

    全库去重时 all_ids 可能很大，按片查询避免 IN(...) 超过 SQLite 变量上限。
    """
    tagged_ids: set[str] = set()
    for chunk in _chunked(all_ids):
        tagged_result = await db.execute(
            select(InspirationTag.inspiration_id)
            .where(InspirationTag.inspiration_id.in_(chunk))
            .distinct()
        )
        tagged_ids.update(r[0] for r in tagged_result.all())

    analyzed_ids: set[str] = set()
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


async def _collect_manual_investment(
    db: AsyncSession, all_ids: list[str]
) -> tuple[set[str], set[str], set[str]]:
    """批量查询「用户手工投入」的三个信号，供去重评分使用。

    为什么需要（2026-10-20 审查发现）：原先的评分只认「标签 / 收藏 / 来源链接 /
    AI 分析 / 缩略图」。但一条副本被**手工加进合集**、被**人工审核过**、被**打过评分**，
    同样是无法自动重建的投入；删掉它等于把用户的工作一起删了，而且都不报错：

    - 在合集里：`collection_items` 有 `ON DELETE CASCADE`，副本一删成员行跟着消失，
      合集**静默少一张图**；
    - 人工审核过：`quality_reviewed_by == "human"` 是人给的判定，删掉换了条 pending 的，
      判定就丢了。注意**不能**用 `quality_status == "approved"` 判——手动上传按配置会
      自动通过（`manual_upload_auto_approve`），那会把「自动通过」误当人工投入；
    - 打过评分：`rating > 0` 是用户手点出来的。

    与 `_collect_tagged_analyzed_ids` 一样分片查询，避免 IN(...) 超过 SQLite 变量上限。
    """
    in_collection: set[str] = set()
    for chunk in _chunked(all_ids):
        rows = await db.execute(
            select(CollectionItem.inspiration_id)
            .where(CollectionItem.inspiration_id.in_(chunk))
            .distinct()
        )
        in_collection.update(r[0] for r in rows.all())

    human_reviewed: set[str] = set()
    rated: set[str] = set()
    for chunk in _chunked(all_ids):
        rows = await db.execute(
            select(Inspiration.id, Inspiration.quality_reviewed_by, Inspiration.rating)
            .where(Inspiration.id.in_(chunk))
        )
        for insp_id, reviewed_by, rating in rows.all():
            if reviewed_by == "human":
                human_reviewed.add(insp_id)
            if rating:
                rated.add(insp_id)

    return in_collection, human_reviewed, rated


def _score_groups(
    dup_groups: list[tuple[str, list[dict]]],
    tagged_ids: set[str],
    analyzed_ids: set[str],
    storage_root: Path,
    *,
    in_collection: set[str] | None = None,
    human_reviewed: set[str] | None = None,
    rated: set[str] | None = None,
) -> tuple[list[dict], list[str], list[tuple[str, str | None]]]:
    """按评分规则决定每组的保留副本与冗余副本。

    评分规则（越靠前越是「无法自动重建的人工投入」）：
        有标签 +100、在合集里 +60、已收藏 +50、人工审核过 +45、有来源链接 +40、
        AI 分析成功 +30、用户评分 +20、有缩略图 +10；平局时创建时间更早优先、再平 ID 更小。

    「在合集里 / 人工审核过 / 用户评分」是 2026-10-20 补的维度：这三者都是用户手工
    投入，删掉不可恢复（合集成员行还会被 CASCADE 一起删），但此前完全没进评分。

    返回 (详情列表, 待删除 ID, 待删除文件路径列表)。
    """
    in_collection = in_collection or set()
    human_reviewed = human_reviewed or set()
    rated = rated or set()
    details: list[dict] = []
    ids_to_delete: list[str] = []
    files_to_delete: list[tuple[str, str | None]] = []

    for dup_hash, group in dup_groups:
        scored = []
        for f in group:
            score = 0
            reasons: list[str] = []
            if f["id"] in tagged_ids:
                score += 100
                reasons.append("有标签")
            if f["id"] in in_collection:
                score += 60
                reasons.append("在合集里")
            if f["is_favorite"]:
                score += 50
                reasons.append("已收藏")
            if f["id"] in human_reviewed:
                score += 45
                reasons.append("人工审核过")
            if f.get("source_url"):
                # 有来源链接优先：f2 新命名能给出真实作品 ID 与原帖（/note/<id>、/video/<id>），
                # 历史合成 ID 的同内容副本没有链接——评分打平时若按创建时间/ID 决定，
                # 可能保留没有链接的那条，清理重复会把原帖入口一起删掉（2026-09 实测 112 组）
                score += 40
                reasons.append("有来源链接")
            if f["id"] in analyzed_ids:
                score += 30
                reasons.append("AI 已分析")
            if f["id"] in rated:
                score += 20
                reasons.append("用户评分")
            if f["thumbnail_path"]:
                score += 10
                reasons.append("有缩略图")
            created_ts = f["created_at"].timestamp() if f["created_at"] else 0
            scored.append({**f, "score": score, "reasons": reasons, "created_ts": created_ts})

        scored.sort(key=lambda x: (-x["score"], x["created_ts"], x["id"]))
        keeper = scored[0]
        victims = scored[1:]

        # 安全检查：保留文件磁盘已丢失时，换一个磁盘存在的作为保留，避免误删全部副本
        keeper_full = storage_root / keeper["file_path"]
        if not keeper_full.exists():
            found = False
            for alt in scored[1:]:
                if (storage_root / alt["file_path"]).exists():
                    keeper = alt
                    victims = [f for f in scored if f["id"] != alt["id"]]
                    found = True
                    break
            if not found:
                continue

        detail = {
            "hash": dup_hash,
            "kept": {
                "id": keeper["id"],
                "file_path": keeper["file_path"],
                "score": keeper["score"],
                "reasons": keeper["reasons"],
                "size_bytes": keeper["size_bytes"],
            },
            "deleted": [],
        }
        for v in victims:
            ids_to_delete.append(v["id"])
            files_to_delete.append((v["file_path"], v["thumbnail_path"]))
            detail["deleted"].append({
                "id": v["id"],
                "file_path": v["file_path"],
                "score": v["score"],
                "reasons": v["reasons"],
                "size_bytes": v["size_bytes"],
            })
        if detail["deleted"]:
            details.append(detail)

    return details, ids_to_delete, files_to_delete


def _delete_files(
    files_to_delete: list[tuple[str, str | None]], storage_root: Path
) -> int:
    """物理删除冗余文件并统计释放空间（删除失败仅记日志，不抛异常）。"""
    freed_bytes = 0
    for fpath, thumb in files_to_delete:
        freed_bytes += delete_files_counting(fpath, thumb, storage_root=storage_root)
    return freed_bytes


async def execute_deduplicate(db: AsyncSession, task: TaskQueue) -> None:
    """执行智能去重任务：全库 MD5 扫描 + 评分保留 + 物理删除冗余副本（由 worker 调用）。"""
    storage_root = settings.storage_root
    task.error = None
    task.progress = 5
    await db.commit()

    # 阶段 1：全库扫描，计算 MD5 并分组
    # 同步逐块读文件算 MD5 会阻塞事件循环数分钟，放入线程池执行
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.thumbnail_path,
               Inspiration.is_favorite, Inspiration.created_at, Inspiration.source_url)
        .where(Inspiration.deleted_at.is_(None))
    )
    hash_map = await asyncio.to_thread(
        build_hash_map, result.all(), storage_root, include_meta=True
    )

    dup_groups = [(h, files) for h, files in hash_map.items() if len(files) > 1]
    if not dup_groups:
        task.result = {"groups_processed": 0, "files_deleted": 0, "freed_bytes": 0, "details": []}
        task.total = 0
        task.done = 0
        task.progress = 100
        await db.commit()
        return

    task.total = len(dup_groups)
    task.done = 0
    task.progress = 30
    await db.commit()

    # 阶段 2：评分并决定每组保留哪个
    all_ids = [f["id"] for _h, group in dup_groups for f in group]
    tagged_ids, analyzed_ids = await _collect_tagged_analyzed_ids(db, all_ids)
    in_collection, human_reviewed, rated = await _collect_manual_investment(db, all_ids)
    details, ids_to_delete, files_to_delete = _score_groups(
        dup_groups,
        tagged_ids,
        analyzed_ids,
        storage_root,
        in_collection=in_collection,
        human_reviewed=human_reviewed,
        rated=rated,
    )

    if not ids_to_delete:
        task.result = {"groups_processed": 0, "files_deleted": 0, "freed_bytes": 0, "details": []}
        task.done = task.total
        task.progress = 100
        await db.commit()
        return

    # 阶段 3：写墓碑、删数据库记录、删磁盘文件
    urls_to_seal: list[str] = []
    for chunk in _chunked(ids_to_delete):
        url_result = await db.execute(
            select(Inspiration.source_url).where(Inspiration.id.in_(chunk))
        )
        urls_to_seal.extend(r[0] for r in url_result.all() if r[0])
    await seal_urls(db, urls_to_seal)

    # 分片删除，避免 IN(...) 超过 SQLite 变量上限
    for chunk in _chunked(ids_to_delete):
        await db.execute(
            Inspiration.__table__.delete().where(Inspiration.id.in_(chunk))
        )
    await db.commit()

    # 删除 LanceDB 向量，避免孤儿向量（由 vector_store 提供，未安装时静默返回）
    await _delete_inspiration_vectors(ids_to_delete)

    freed_bytes = _delete_files(files_to_delete, storage_root)

    # 记录审计：智能去重物理删除属不可恢复的破坏性操作，留痕便于追溯
    # （含实际删除清单的统计，而非触发时的计划数）
    await _record_dedupe_audit(len(ids_to_delete), freed_bytes)

    task.result = {
        "groups_processed": len(details),
        "files_deleted": len(ids_to_delete),
        "freed_bytes": freed_bytes,
        "details": details,
    }
    task.done = task.total
    task.progress = 100
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"去重任务完成: #{task.id} 处理 {len(details)} 组，删除 {len(ids_to_delete)} 个冗余文件"
    )
