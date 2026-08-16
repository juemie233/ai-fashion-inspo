"""素材管理后台 — 统计、完整性检查、批量操作、导出与趋势分析。"""

import csv
import io
import logging
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

from app.config import settings
from app.database import get_db
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    NOT_DELETED,
    latest_analysis_log_subquery,
    utcnow,
)
from app.models.person import InspirationPerson, Person
from app.models.tag import InspirationTag
from app.models.audit import AuditLog
from app.services import admin_stats_service
from app.services.audit_service import record_audit_log
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
    failed: list[str] = []
    # 保护期：跳过最近写入的文件，避免与「上传落盘 → 建库/哈希 → 事务提交」的窗口
    # 并发（TOCTOU），误删正在上传、尚未入库的媒体文件
    _ORPHAN_GRACE_SECONDS = 600  # 10 分钟
    cutoff = time.time() - _ORPHAN_GRACE_SECONDS
    for rel_path in storage_files:
        if rel_path not in db_paths:
            fpath = storage_root / rel_path
            try:
                st = fpath.stat()
                if st.st_mtime > cutoff:
                    continue  # 最近写入，可能仍在入库流程中
                sz = st.st_size
                fpath.unlink()
                deleted += 1
                freed_bytes += sz
            except Exception as e:
                # 删除失败不能静默吞掉：记录日志与失败列表，freed_bytes 与实际释放保持一致
                failed.append(rel_path)
                logger.warning(f"孤立文件删除失败: {rel_path} — {e}")

    # 清理空目录
    for dirpath in sorted(storage_root.rglob("*"), reverse=True):
        if dirpath.is_dir():
            try:
                dirpath.rmdir()  # 仅删除空目录
            except OSError:
                pass

    # 记录审计：清理孤立文件属破坏性操作，留痕便于追溯
    if deleted > 0:
        async with async_session() as audit_db:
            await record_audit_log(
                audit_db,
                action="cleanup_orphans",
                count=deleted,
                freed_bytes=freed_bytes,
                detail="删除磁盘上有但数据库无记录的孤立媒体文件",
            )

    return {
        "deleted_count": deleted,
        "freed_bytes": freed_bytes,
        "failed_files": failed if failed else None,
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
        # 查询「最新一条标签分析日志失败」的素材 ID（排除垃圾桶）。
        # 用 latest 语义：历史失败过但最新已成功的素材不应被误删。
        latest = latest_analysis_log_subquery()
        result = await db.execute(
            select(AIAnalysisLog.inspiration_id)
            .join(
                latest,
                (AIAnalysisLog.inspiration_id == latest.c.inspiration_id)
                & (AIAnalysisLog.id == latest.c.max_id),
            )
            .where(
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

    # 记录审计：批量删除（物理删除）属破坏性操作
    await record_audit_log(
        db,
        action="batch_delete",
        count=len(ids),
        detail=f"任务 #{task.id}，条件={label}",
    )

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


# ============ 数据导出 / 趋势 / 人物频次 ============


@router.get("/export")
async def export_inspirations(db: AsyncSession = Depends(get_db)):
    """导出全部未删除素材为 CSV（含标签与关联人物），供 Excel/表格工具离线分析。

    响应为 UTF-8（带 BOM，Excel 打开不乱码），Content-Disposition 触发浏览器下载。
    """
    result = await db.execute(
        select(Inspiration)
        .options(
            selectinload(Inspiration.tags).selectinload(InspirationTag.tag),
            selectinload(Inspiration.persons).selectinload(InspirationPerson.person),
        )
        .where(NOT_DELETED)
        .order_by(Inspiration.created_at.desc())
    )
    inspirations = result.unique().scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "source_type", "source_author", "source_url", "media_type",
        "is_favorite", "quality_status", "quality_reason", "is_ai_generated",
        "dominant_colors", "tags", "persons", "created_at", "updated_at",
    ])
    for insp in inspirations:
        tags = "|".join(t.tag.name for t in insp.tags)
        persons = "|".join(p.person.name for p in insp.persons)
        writer.writerow([
            insp.id,
            insp.source_type,
            insp.source_author or "",
            insp.source_url or "",
            insp.media_type,
            "1" if insp.is_favorite else "0",
            insp.quality_status or "",
            insp.quality_reason or "",
            "1" if insp.is_ai_generated else "0",
            insp.dominant_colors or "",
            tags,
            persons,
            insp.created_at.isoformat() if insp.created_at else "",
            insp.updated_at.isoformat() if insp.updated_at else "",
        ])

    filename = f"inspirations_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/trend")
async def inspiration_trend(
    days: int = Query(30, ge=1, le=365, description="统计最近 N 天"),
    db: AsyncSession = Depends(get_db),
):
    """按天统计新增素材数量（近 N 天），供管理页趋势图使用。

    created_at 按项目约定为 UTC（utcnow 写入），截止时间必须同样用 UTC
    计算，否则在非 UTC 时区（如 UTC+8）下按天边界整体偏移。
    """
    cutoff = utcnow() - timedelta(days=days)
    rows = (await db.execute(
        select(
            func.strftime("%Y-%m-%d", Inspiration.created_at).label("day"),
            func.count(Inspiration.id).label("cnt"),
        )
        .where(Inspiration.created_at >= cutoff, NOT_DELETED)
        .group_by("day")
        .order_by("day")
    )).all()
    return {
        "days": days,
        "trend": [{"day": r[0], "count": r[1]} for r in rows],
    }


@router.get("/person-frequency")
async def person_frequency(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """按关联素材数量降序返回人物（排除垃圾桶素材），辅助识别高频模特/博主。"""
    rows = (await db.execute(
        select(
            Person.id,
            Person.name,
            Person.person_type,
            Person.platform,
            func.count(InspirationPerson.inspiration_id).label("cnt"),
        )
        .join(InspirationPerson, InspirationPerson.person_id == Person.id)
        .join(Inspiration, Inspiration.id == InspirationPerson.inspiration_id)
        .where(NOT_DELETED)
        .group_by(Person.id, Person.name, Person.person_type, Person.platform)
        .order_by(func.count(InspirationPerson.inspiration_id).desc())
        .limit(limit)
    )).all()
    return [
        {
            "id": r[0],
            "name": r[1],
            "person_type": r[2],
            "platform": r[3],
            "count": r[4],
        }
        for r in rows
    ]


@router.get("/audit-logs")
async def audit_logs(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """按时间倒序返回破坏性操作审计日志。"""
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "action": r.action,
            "target_type": r.target_type,
            "count": r.count,
            "freed_bytes": r.freed_bytes,
            "detail": r.detail,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/near-duplicates")
async def near_duplicates(
    limit: int = Query(1000, ge=1, le=5000, description="扫描图片数量上限"),
    threshold: int = Query(32, ge=1, le=256, description="汉明距离阈值（越小越严格，768 位 RGB dHash）"),
    db: AsyncSession = Depends(get_db),
):
    """检测视觉近似重复的图片素材（感知哈希分组），仅返回候选、不删除。

    与「重复文件」（SHA-256 精确重复）互补：本接口识别字节不同但视觉相似的
    近似重复（不同压缩/缩放/水印），由前端并排预览后人工确认删除。
    """
    from app.services.near_duplicate_service import scan_near_duplicates

    return await scan_near_duplicates(db, limit=limit, threshold=threshold)


# ============ 向量化管理（一键回填缺失向量） ============


async def _get_missing_image_vector_ids(db: AsyncSession) -> list[str]:
    """计算缺失图像向量的素材 ID 列表（图片素材 - 已有图像向量）。

    详情页相似推荐对无向量素材会现场做 CLIP 编码（单张数秒）导致卡顿，
    本函数用于找出这些「未入库」素材，供一键回填。
    """
    from app.services.vector import store as vector_store

    # 全部有效图片素材
    result = await db.execute(
        select(Inspiration.id).where(
            NOT_DELETED,
            Inspiration.media_type == "image",
        )
    )
    all_image_ids = [row[0] for row in result.all()]
    if not all_image_ids:
        return []

    # 已有图像向量的素材
    existing = await vector_store.list_vector_ids("image")
    return [i for i in all_image_ids if i not in existing]


@router.get("/vector-stats")
async def vector_stats(db: AsyncSession = Depends(get_db)):
    """向量化状态统计：素材总数 / 已有向量数 / 缺失数（供管理页展示）。"""
    from app.services.vector import store as vector_store

    missing_ids = await _get_missing_image_vector_ids(db)
    return {
        "total_inspirations": (
            await db.execute(
                select(func.count()).select_from(Inspiration).where(
                    NOT_DELETED, Inspiration.media_type == "image"
                )
            )
        ).scalar() or 0,
        "image_vectors": await vector_store.count_vectors("image"),
        "text_vectors": await vector_store.count_vectors("text"),
        "missing": len(missing_ids),
        "lancedb_available": vector_store.is_lancedb_available(),
    }


@router.post("/vector-backfill")
async def vector_backfill(db: AsyncSession = Depends(get_db)):
    """一键为缺失向量的素材创建向量回填任务（异步，由 worker 执行）。

    返回 task_id 供前端轮询进度；无缺失素材时返回 count=0。
    """
    from app.services.task_runners.vector_backfill import create_vector_backfill_task
    from app.services.vector import store as vector_store

    if not vector_store.is_lancedb_available():
        raise HTTPException(
            status_code=400,
            detail="lancedb 未安装，请先执行：pip install lancedb",
        )

    missing_ids = await _get_missing_image_vector_ids(db)
    if not missing_ids:
        return {
            "message": "没有缺失向量的素材，全部已入库",
            "task_id": None,
            "count": 0,
        }

    task = await create_vector_backfill_task(db, missing_ids)
    return {
        "message": f"已创建向量回填任务 #{task.id}，共 {len(missing_ids)} 个素材",
        "task_id": task.id,
        "count": len(missing_ids),
    }
