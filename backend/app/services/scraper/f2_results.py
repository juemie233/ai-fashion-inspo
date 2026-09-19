"""f2「一键获取素材」任务的结果浏览与审查。

为什么以**批次清单**为归属依据：f2 导入的素材没有 `scraper_task_id`（该列外键
指向 `scraper_tasks` 表，不能塞任务队列 id），「这一批导入了哪些素材」只记录在
`apply_import` 落盘的清单里（`storage/import_batches/f2-*.json`，任务结果
`result.import.batch_file` 指向它）——同一份清单也是 CLI `--rollback` 的依据。

清单只保存导入瞬间的字段（正文/作者/路径/平台 ID），因此列表展示的「当前状态」
（是否在垃圾桶、质量审核状态、收藏、实际文件路径）一律以数据库为准，
避免清单与库不一致时误导审查。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.schemas.inspiration import TRASH_REASONS
from app.services.audit_service import record_audit_log
from app.services.inspiration_trash import (
    batch_trash_inspirations,
    restore_inspiration,
)

logger = logging.getLogger(__name__)

"""结果筛选口径（与前端 tab 一一对应）。"""
RESULT_FILTERS = ("all", "pending", "approved", "rejected", "trash", "gone")

"""单次 IN (...) 查询的 ID 分片大小（SQLite 变量上限 999，留余量）。"""
_ID_CHUNK = 800


async def _load_f2_task(db: AsyncSession, task_id: int) -> TaskQueue:
    """取 f2 导入任务（非该类型一律 404，避免误查任务队列里的其它任务）。"""
    task = await db.get(TaskQueue, task_id)
    if task is None or task.type != "f2_import":
        raise HTTPException(status_code=404, detail="抖音采集任务未找到")
    return task


def _batch_entries(task: TaskQueue) -> tuple[list[dict], str]:
    """读任务对应的批次清单，返回 (条目列表, batch_id)。

    Args:
        task: f2 导入任务（其 result.import.batch_file 指向清单）。

    Returns:
        (清单里的 imported 条目, batch_id)；清单缺失（含入库阶段未落盘/写失败）
        时返回 ([], "")，由调用方按「本批没有可浏览的结果」处理。
    """
    from scripts import import_f2_downloads as f2

    result = task.result if isinstance(task.result, dict) else {}
    import_summary = result.get("import") if isinstance(result.get("import"), dict) else {}
    batch_file = str(import_summary.get("batch_file") or "")
    if not batch_file:
        return [], ""
    manifest = f2.load_batch_manifest(Path(batch_file))
    return manifest["imported"], manifest["batch_id"]


def _result_summary(task: TaskQueue) -> dict:
    """任务结果里的三个阶段摘要（下载作者数 / 计划文件数 / 实际入库数）。"""
    result = task.result if isinstance(task.result, dict) else {}

    def _int(section: str, key: str) -> int:
        block = result.get(section)
        if not isinstance(block, dict):
            return 0
        try:
            return int(block.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "imported": _int("import", "imported"),
        "failed": _int("import", "failed"),
        "fetch_ok": _int("fetch", "ok"),
        "fetch_total": _int("fetch", "total"),
    }


def _entry_ids(entries: list[dict]) -> list[str]:
    """清单条目里的素材 ID（保持导入顺序，去重）。"""
    ids: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        inspiration_id = str(entry.get("inspiration_id") or "")
        if inspiration_id and inspiration_id not in seen:
            seen.add(inspiration_id)
            ids.append(inspiration_id)
    return ids


async def get_f2_task_results(
    db: AsyncSession,
    task_id: int,
    page: int = 1,
    size: int = 60,
    state: str = "all",
    author: str = "",
) -> dict:
    """列出某次 f2 导入产出的素材（分页 + 状态/作者筛选）。

    Args:
        db: 数据库会话。
        task_id: f2 导入任务 id。
        page: 页码（从 1 开始）。
        size: 每页条数。
        state: 筛选口径，见 :data:`RESULT_FILTERS`。
        author: 只保留该作者（source_author 精确匹配，空表示不限）。

    Returns:
        {"task": {...}, "batch_id": str, "items": [...], "total": int,
         "page": int, "size": int, "counts": {...}, "authors": [...],
         "has_batch": bool}
        ``counts`` 为整批口径（不受筛选影响）：total / live / trash / gone /
        pending / approved / rejected。
    """
    if state not in RESULT_FILTERS:
        raise HTTPException(status_code=400, detail=f"筛选口径不支持：{state}")

    task = await _load_f2_task(db, task_id)
    entries, batch_id = _batch_entries(task)
    ids = _entry_ids(entries)

    rows: dict[str, Inspiration] = {}
    for start in range(0, len(ids), _ID_CHUNK):
        chunk = ids[start : start + _ID_CHUNK]
        result = await db.execute(select(Inspiration).where(Inspiration.id.in_(chunk)))
        for row in result.scalars().all():
            rows[row.id] = row

    counts = {
        "total": len(ids),
        "live": 0,
        "trash": 0,
        "gone": 0,
        "pending": 0,
        "approved": 0,
        "rejected": 0,
    }
    items: list[dict] = []
    author_counter: dict[str, int] = {}

    for entry in entries:
        inspiration_id = str(entry.get("inspiration_id") or "")
        if not inspiration_id:
            continue
        row = rows.get(inspiration_id)
        entry_author = str(entry.get("author_dir") or "")
        if row is None:
            # 已被彻底删除：清单里仍有记录，列表用占位卡展示（不可选、不可操作）
            counts["gone"] += 1
            state_of = "gone"
            item = {
                "id": inspiration_id,
                "state": "gone",
                "quality_status": None,
                "media_type": str(entry.get("media_type") or ""),
                "caption": str(entry.get("caption") or ""),
                "author": entry_author,
                "hashtags": entry.get("hashtags") or [],
                "file_path": None,
                "thumbnail_path": None,
                "is_favorite": False,
                "trash_reason": None,
                "source_platform_id": str(entry.get("platform_id") or ""),
                "created_at": str(entry.get("created_at") or ""),
            }
        else:
            quality = row.quality_status or "pending"
            if row.deleted_at is not None:
                counts["trash"] += 1
                state_of = "trash"
            else:
                counts["live"] += 1
                if quality in counts:
                    counts[quality] += 1
                state_of = quality
            item = {
                "id": row.id,
                "state": state_of,
                "quality_status": row.quality_status,
                "media_type": row.media_type,
                "caption": row.caption or "",
                "author": row.source_author or entry_author,
                "hashtags": entry.get("hashtags") or [],
                "file_path": row.file_path,
                "thumbnail_path": row.thumbnail_path,
                "is_favorite": bool(row.is_favorite),
                "trash_reason": row.trash_reason,
                "source_platform_id": row.source_platform_id,
                "created_at": str(row.created_at or ""),
            }
        if item["author"]:
            author_counter[item["author"]] = author_counter.get(item["author"], 0) + 1
        items.append(item)

    if author:
        items = [i for i in items if i["author"] == author]
    if state != "all":
        items = [i for i in items if i["state"] == state]

    total = len(items)
    start = (page - 1) * size
    paged = items[start : start + size]

    return {
        "task": {
            "id": task.id,
            "status": task.status,
            "created_at": str(task.created_at or ""),
            "updated_at": str(task.updated_at or ""),
            "error": task.error,
            **_result_summary(task),
        },
        "batch_id": batch_id,
        "items": paged,
        "total": total,
        "page": page,
        "size": size,
        "counts": counts,
        "authors": [
            {"name": name, "count": count}
            for name, count in sorted(author_counter.items(), key=lambda x: (-x[1], x[0]))
        ],
        "has_batch": bool(batch_id or ids),
    }


async def _allowed_ids(db: AsyncSession, task_id: int, ids: list[str]) -> list[str]:
    """把请求里的 ID 收敛到「确实属于该任务批次」的范围（越权/脏 ID 直接丢弃）。"""
    task = await _load_f2_task(db, task_id)
    entries, _batch_id = _batch_entries(task)
    allowed = set(_entry_ids(entries))
    if not allowed:
        raise HTTPException(status_code=400, detail="该任务没有可操作的批次清单")
    return [i for i in ids if i in allowed]


async def trash_f2_task_results(
    db: AsyncSession,
    task_id: int,
    ids: list[str],
    reason: str | None = None,
) -> dict:
    """把本批素材移入垃圾桶（软删除，可恢复；垃圾桶素材同时作为负样本）。

    Args:
        db: 数据库会话。
        task_id: f2 导入任务 id。
        ids: 待移入的素材 ID（不属于本批的会被忽略）。
        reason: 删除原因（缺省由后端按素材状态推断）；非法值由状态机拒绝。

    Returns:
        {"requested": int, "trashed": int, "skipped": int}
    """
    if reason is not None and reason not in TRASH_REASONS:
        raise HTTPException(status_code=400, detail=f"删除原因不支持：{reason}")
    target = await _allowed_ids(db, task_id, ids)
    if not target:
        raise HTTPException(status_code=400, detail="没有属于该任务的素材")

    outcome = await batch_trash_inspirations(db, target, reason, source="manual")
    return {
        "requested": len(ids),
        "trashed": outcome["trashed"],
        "skipped": outcome["skipped"] + (len(ids) - len(target)),
    }


async def restore_f2_task_results(
    db: AsyncSession, task_id: int, ids: list[str]
) -> dict:
    """把本批已在垃圾桶的素材还原回素材库。

    逐条复用单条恢复逻辑（文件移回、三字段状态机、平台 ID 冲突前置检查），
    单条失败计入 skipped 继续处理其余素材。

    Returns:
        {"requested": int, "restored": int, "skipped": int}
    """
    target = await _allowed_ids(db, task_id, ids)
    if not target:
        raise HTTPException(status_code=400, detail="没有属于该任务的素材")

    restored = 0
    skipped = len(ids) - len(target)
    for inspiration_id in target:
        try:
            await restore_inspiration(db, inspiration_id)
            restored += 1
        except HTTPException:
            skipped += 1
        except Exception as exc:  # noqa: BLE001 —— 单条失败不阻断其余还原
            skipped += 1
            logger.warning(f"f2 结果还原失败（计入跳过）{inspiration_id}: {exc}")

    if restored:
        await record_audit_log(
            action="batch_restore",
            count=restored,
            detail=f"来自 f2 结果审查（任务 #{task_id}）",
        )
    return {"requested": len(ids), "restored": restored, "skipped": skipped}


async def delete_f2_task_results(
    db: AsyncSession, task_id: int, ids: list[str]
) -> dict:
    """彻底删除本批素材：创建 batch_delete 任务（不可恢复，物理删文件与记录）。

    与 ``POST /api/admin/batch-delete`` 同一套执行链路（worker 执行：删 DB 行 →
    向量 → 关键帧 → 文件/缩略图），因此立即返回 task_id 即可，前端提示「已提交」。

    Returns:
        {"requested": int, "count": int, "task_id": int}
    """
    target = await _allowed_ids(db, task_id, ids)
    if not target:
        raise HTTPException(status_code=400, detail="没有属于该任务的素材")

    from app.services.task_runner import create_batch_delete_task

    delete_task = await create_batch_delete_task(db, target, label=f"f2_task_{task_id}")
    await record_audit_log(
        action="batch_delete",
        count=len(target),
        detail=f"任务 #{delete_task.id}，来自 f2 结果审查（来源任务 #{task_id}）",
    )
    return {"requested": len(ids), "count": len(target), "task_id": delete_task.id}
