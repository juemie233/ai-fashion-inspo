"""素材管理后台 — 统计、完整性检查、批量操作。"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    NOT_DELETED,
    analysis_log_filter,
    utcnow,
)
from app.models.tag import InspirationTag
from app.services import admin_stats_service
from app.utils.file_hash import build_hash_map

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats")
async def admin_stats(db: AsyncSession = Depends(get_db)):
    """素材总览仪表盘数据（聚合逻辑在 app.services.admin_stats_service）。"""
    return await admin_stats_service.collect_stats(db)


@router.get("/largest-files")
async def largest_files(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """列出占用空间最大的前 N 个文件。"""
    storage_root = settings.storage_root
    result = (await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.source_type, Inspiration.created_at)
        .where(NOT_DELETED)
        .order_by(Inspiration.created_at.desc())
    )).all()

    files = []
    for row in result:
        fpath = storage_root / row[1] if row[1] else None
        size = 0
        exists = False
        if fpath and fpath.exists():
            size = fpath.stat().st_size
            exists = True
        files.append({
            "id": row[0],
            "file_path": row[1],
            "source_type": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
            "size_bytes": size,
            "exists": exists,
        })

    # 按文件大小降序排列
    files.sort(key=lambda f: f["size_bytes"], reverse=True)
    return files[:limit]


@router.get("/integrity-check")
async def integrity_check(db: AsyncSession = Depends(get_db)):
    """数据完整性检查：
    - missing_files: 数据库有记录但文件不存在的素材
    - orphan_files: 磁盘上有文件但数据库无对应记录的文件
    """
    storage_root = settings.storage_root

    # 所有数据库记录的 file_path 和 thumbnail_path（排除垃圾桶：其文件已移入 trash/）
    db_files_result = (await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.thumbnail_path)
        .where(NOT_DELETED)
    )).all()

    db_file_paths: set[str] = set()
    id_by_path: dict[str, list[str]] = {}  # path → [id, ...]
    for row in db_files_result:
        for p in (row[1], row[2]):
            if p:
                db_file_paths.add(p)
                id_by_path.setdefault(p, []).append(row[0])

    # 检查 missing files
    missing_files: list[dict] = []
    for file_path, ids in id_by_path.items():
        fpath = storage_root / file_path
        if not fpath.exists():
            missing_files.append({
                "file_path": file_path,
                "inspiration_ids": list(set(ids)),
            })

    # 扫描磁盘媒体文件，找孤立文件（scan_storage_files 已排除 lancedb/logs 等非素材目录）
    disk_files = admin_stats_service.scan_storage_files()
    orphan_files: list[dict] = []
    orphan_total_size = 0
    for rel_path, size in disk_files.items():
        if rel_path not in db_file_paths:
            orphan_files.append({
                "file_path": rel_path,
                "size_bytes": size,
            })
            orphan_total_size += size

    return {
        "missing_files": missing_files,
        "missing_count": len(missing_files),
        "orphan_files": orphan_files,
        "orphan_count": len(orphan_files),
        "orphan_total_size_bytes": orphan_total_size,
    }


@router.post("/cleanup-orphans")
async def cleanup_orphan_files():
    """删除所有孤立文件（磁盘上有但数据库无记录的文件）。"""
    storage_files = admin_stats_service.scan_storage_files()
    storage_root = settings.storage_root

    # 这里需要一个同步的数据库会话来获取文件列表
    from app.database import async_session
    async with async_session() as db:
        result = await db.execute(
            select(Inspiration.file_path, Inspiration.thumbnail_path).where(NOT_DELETED)
        )
        db_paths: set[str] = set()
        for row in result:
            for p in (row[0], row[1]):
                if p:
                    db_paths.add(p)

    deleted = 0
    freed_bytes = 0
    for rel_path in storage_files:
        if rel_path not in db_paths:
            fpath = storage_root / rel_path
            try:
                sz = fpath.stat().st_size
                fpath.unlink()
                deleted += 1
                freed_bytes += sz
            except Exception:
                pass

    # 清理空目录
    for dirpath in sorted(storage_root.rglob("*"), reverse=True):
        if dirpath.is_dir():
            try:
                dirpath.rmdir()  # 仅删除空目录
            except OSError:
                pass

    return {
        "deleted_count": deleted,
        "freed_bytes": freed_bytes,
    }


@router.post("/batch-delete")
async def batch_delete(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """批量删除素材（已改造为任务队列，创建任务后返回 task_id）。

    请求体:
        {"ids": ["id1", "id2", ...]}  — 按 ID 列表删除
        {"condition": "untagged"}     — 按条件删除（无标签的）
        {"condition": "analysis_failed"} — 按条件删除（分析失败的）
    """
    ids = payload.get("ids", [])
    condition = payload.get("condition")

    if not ids and not condition:
        raise HTTPException(status_code=400, detail="请提供 ids 列表或 condition 条件")

    # 按条件查询要删除的 ID（在 API 层解析，保证删除语义稳定）
    label = condition or "ids"
    if condition == "untagged":
        result = await db.execute(
            select(Inspiration.id)
            .outerjoin(InspirationTag, Inspiration.id == InspirationTag.inspiration_id)
            .where(InspirationTag.inspiration_id.is_(None), NOT_DELETED)
        )
        ids = [r[0] for r in result.all()]
    elif condition == "analysis_failed":
        # 查询有分析失败日志的素材 ID（排除垃圾桶）
        result = await db.execute(
            select(AIAnalysisLog.inspiration_id)
            .where(
                analysis_log_filter(),
                AIAnalysisLog.error.isnot(None),
                AIAnalysisLog.error != "",
                AIAnalysisLog.inspiration_id.in_(select(Inspiration.id).where(NOT_DELETED)),
            )
            .distinct()
        )
        ids = [r[0] for r in result.all()]

    if not ids:
        return {"deleted_count": 0, "freed_bytes": 0}

    from app.services.task_runner import create_batch_delete_task
    task = await create_batch_delete_task(db, ids, label=label)

    return {
        "message": f"已提交批量删除任务 #{task.id}，共 {len(ids)} 个素材",
        "task_id": task.id,
    }


@router.post("/batch-unmark-ai")
async def batch_unmark_ai(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """批量将疑似 AI 素材重新标记为非 AI（人工复核翻案）。

    请求体: {"ids": ["id1", "id2", ...]}

    仅做数据库标记更新（不删除文件、不触发 AI），因此同步执行并立即返回。
    """
    ids = payload.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="请提供 ids 列表")

    result = await db.execute(
        update(Inspiration)
        .where(Inspiration.id.in_(ids))
        .values(is_ai_generated=False, updated_at=utcnow())
    )
    await db.commit()
    return {"updated": result.rowcount}


@router.get("/check-duplicate")
async def check_duplicate(
    hash: str = Query(..., min_length=64, max_length=64, description="文件 SHA-256 哈希"),
    db: AsyncSession = Depends(get_db),
):
    """检查指定 SHA-256 的文件是否已存在（上传前去重）。

    优先走 content_hash 索引列；存量素材未回填哈希时自动回退扫描并回填。
    """
    from app.services.inspiration_service import find_duplicate_by_hash

    dup_id = await find_duplicate_by_hash(db, hash)
    if dup_id:
        result = await db.execute(
            select(Inspiration.file_path).where(Inspiration.id == dup_id)
        )
        fpath = result.scalar_one_or_none()
        return {
            "exists": True,
            "inspiration_id": dup_id,
            "file_path": fpath,
        }

    return {"exists": False, "inspiration_id": None, "file_path": None}


@router.get("/duplicates")
async def find_duplicates(db: AsyncSession = Depends(get_db)):
    """通过文件哈希检测完全重复的素材。"""
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path).where(NOT_DELETED)
    )
    hash_map = build_hash_map(result.all(), settings.storage_root)

    duplicates = [
        {"hash": h, "files": files}
        for h, files in hash_map.items()
        if len(files) > 1
    ]

    dup_count = sum(len(d["files"]) - 1 for d in duplicates)
    dup_size = sum(
        d["files"][0]["size_bytes"] * (len(d["files"]) - 1)
        for d in duplicates
    )

    return {
        "duplicate_groups": duplicates,
        "duplicate_count": dup_count,
        "wasted_bytes": dup_size,
    }


@router.post("/deduplicate")
async def deduplicate_files(db: AsyncSession = Depends(get_db)):
    """智能去重（已改造为任务队列，创建任务后返回 task_id）。

    每组重复文件保留评分最高的 1 个，其余物理删除；
    评分与删除逻辑见 task_runner.execute_deduplicate。
    """
    from app.services.task_runner import create_deduplicate_task
    task = await create_deduplicate_task(db)
    return {"message": f"已提交去重任务 #{task.id}", "task_id": task.id}
