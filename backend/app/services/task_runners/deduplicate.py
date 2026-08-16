"""智能去重任务：全库 MD5 扫描 + 评分保留 + 物理删除冗余副本。

本模块包含「智能去重」（deduplicate）任务的创建与执行逻辑，
由 worker 进程（app/worker.py）通过 TASK_HANDLERS 分发表调度。
execute_deduplicate 原为 182 行巨型函数，现按阶段拆分为：
- _collect_tagged_analyzed_ids：批量查询「有标签/已分析」素材 ID
- _score_groups：评分并决定每组保留副本
- _delete_files：物理删除冗余文件并统计释放空间
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import AIAnalysisLog, Inspiration, analysis_log_filter
from app.models.tag import InspirationTag
from app.models.task import TaskQueue
from app.services.scraper_seen_service import seal_urls
from app.services.task_runners.common import (
    _chunked,
    _delete_inspiration_vectors,
    utcnow,
)
from app.utils.file_hash import build_hash_map

logger = logging.getLogger(__name__)


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


def _score_groups(
    dup_groups: list[tuple[str, list[dict]]],
    tagged_ids: set[str],
    analyzed_ids: set[str],
    storage_root,
) -> tuple[list[dict], list[str], list[tuple[str, str | None]]]:
    """按评分规则决定每组的保留副本与冗余副本。

    评分规则：有标签 +100、已收藏 +50、AI 分析成功 +30、有缩略图 +10、
    创建时间更早优先（平局时 ID 更小）。
    返回 (详情列表, 待删除 ID, 待删除文件路径列表)。
    """
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
            if f["is_favorite"]:
                score += 50
                reasons.append("已收藏")
            if f["id"] in analyzed_ids:
                score += 30
                reasons.append("AI 已分析")
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
    files_to_delete: list[tuple[str, str | None]], storage_root
) -> int:
    """物理删除冗余文件并统计释放空间（删除失败仅跳过，不抛异常）。"""
    freed_bytes = 0
    for fpath, thumb in files_to_delete:
        for p in (fpath, thumb):
            if p:
                full = storage_root / p
                try:
                    if full.exists():
                        freed_bytes += full.stat().st_size
                        full.unlink()
                except Exception:
                    pass
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
               Inspiration.is_favorite, Inspiration.created_at)
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
    details, ids_to_delete, files_to_delete = _score_groups(
        dup_groups, tagged_ids, analyzed_ids, storage_root
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
