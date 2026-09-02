"""素材管理后台 — 统计、完整性检查、批量操作、导出与趋势分析。"""

import asyncio
import csv
import io
import logging
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
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
from app.models.person import Blogger, InspirationBlogger, InspirationModel, Model
from app.models.tag import InspirationTag
from app.models.audit import AuditLog
from app.services import admin_stats_service, backup_service, inspiration_service
from app.services.audit_service import record_audit_log
from app.utils.csv_safety import sanitize_csv_cell
from app.utils.file_hash import build_hash_map
from app.utils.time import format_utc

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats")
async def admin_stats(db: AsyncSession = Depends(get_db)) -> dict:
    """素材总览仪表盘数据（聚合逻辑在 app.services.admin_stats_service）。"""
    return await admin_stats_service.collect_stats(db)


@router.get("/backup/status")
async def backup_status() -> dict:
    """数据备份状态（只读）：开关/最近成功备份/历史/运行锁/日志尾部。

    供任务管理页「数据备份」卡片展示；不触发备份，也不读取数据库。
    备份双通道（每日计划任务 + 启动补备）的运行锁（.backup.lock）与
    历史目录均从备份目标目录实时读取，两通道的信息都能看到。
    """
    return backup_service.build_backup_status()


@router.get("/largest-files")
async def largest_files(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """列出占用空间最大的前 N 个文件。"""
    storage_root = settings.storage_root
    result = (await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.source_type, Inspiration.created_at)
        .where(NOT_DELETED)
        .order_by(Inspiration.created_at.desc())
    )).all()

    # 磁盘 stat 属阻塞 I/O，放线程池避免大库时卡事件循环
    def _collect() -> list[dict]:
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
                "created_at": format_utc(row[3]),
                "size_bytes": size,
                "exists": exists,
            })
        files.sort(key=lambda f: f["size_bytes"], reverse=True)
        return files[:limit]

    return await asyncio.to_thread(_collect)


@router.get("/integrity-check")
async def integrity_check(db: AsyncSession = Depends(get_db)) -> dict:
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

    # 检查 missing files（磁盘 exists 属阻塞 I/O，放线程池）
    def _find_missing() -> list[dict]:
        missing: list[dict] = []
        for file_path, ids in id_by_path.items():
            fpath = storage_root / file_path
            if not fpath.exists():
                missing.append({
                    "file_path": file_path,
                    "inspiration_ids": list(set(ids)),
                })
        return missing

    missing_files = await asyncio.to_thread(_find_missing)

    # 扫描磁盘媒体文件，找孤立文件（scan_storage_files 已排除 lancedb/logs 等非素材目录）
    disk_files = await asyncio.to_thread(admin_stats_service.scan_storage_files)
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
        # 垃圾桶状态不变量违规（R1/R2/R3）：软删除三字段必须同真同假，见
        # inspiration_service.verify_trash_invariants；空列表 = 健康
        "trash_invariants": await inspiration_service.verify_trash_invariants(db),
    }


@router.post("/cleanup-orphans")
async def cleanup_orphan_files() -> dict:
    """删除所有孤立文件（磁盘上有但数据库无记录的文件）。"""
    storage_files = await asyncio.to_thread(admin_stats_service.scan_storage_files)
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
        await record_audit_log(
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
) -> dict:
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
) -> dict:
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
) -> dict:
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
async def find_duplicates(db: AsyncSession = Depends(get_db)) -> dict:
    """通过文件哈希检测完全重复的素材。"""
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path).where(NOT_DELETED)
    )
    hash_map = await asyncio.to_thread(build_hash_map, result.all(), settings.storage_root)

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
async def deduplicate_files(db: AsyncSession = Depends(get_db)) -> dict:
    """智能去重（已改造为任务队列，创建任务后返回 task_id）。

    每组重复文件保留评分最高的 1 个，其余物理删除；
    评分与删除逻辑见 task_runner.execute_deduplicate。
    """
    from app.services.task_runner import create_deduplicate_task
    task = await create_deduplicate_task(db)
    return {"message": f"已提交去重任务 #{task.id}", "task_id": task.id}


# ============ 数据导出 / 趋势 / 人物频次 ============


@router.get("/export")
async def export_inspirations(db: AsyncSession = Depends(get_db)) -> Response:
    """导出全部未删除素材为 CSV（含标签与关联人物），供 Excel/表格工具离线分析。

    响应为 UTF-8（带 BOM，Excel 打开不乱码），Content-Disposition 触发浏览器下载。
    """
    result = await db.execute(
        select(Inspiration)
        .options(
            selectinload(Inspiration.tags).selectinload(InspirationTag.tag),
            selectinload(Inspiration.bloggers).selectinload(InspirationBlogger.blogger),
            selectinload(Inspiration.models).selectinload(InspirationModel.model),
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
        "dominant_colors", "tags", "bloggers", "models", "created_at", "updated_at",
    ])
    for insp in inspirations:
        tags = "|".join(t.tag.name for t in insp.tags)
        bloggers = "|".join(b.blogger.name for b in insp.bloggers)
        models = "|".join(m.model.name for m in insp.models)
        row = [
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
            bloggers,
            models,
            format_utc(insp.created_at) or "",
            format_utc(insp.updated_at) or "",
        ]
        # 防 CSV 公式注入：用户/模型可控的单元格以 = + - @ 开头时加 ' 转义
        writer.writerow([sanitize_csv_cell(x) for x in row])

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
) -> dict:
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
) -> list[dict]:
    """按关联素材数量降序返回人物（排除垃圾桶素材），辅助识别高频模特/博主。

    博主与模特已拆分两表，本统计合并两者返回；``person_type`` 保留以区分来源。
    """
    combined = (
        select(
            Blogger.id.label("id"),
            Blogger.name.label("name"),
            Blogger.platform.label("platform"),
            func.count(InspirationBlogger.inspiration_id).label("cnt"),
        )
        .join(InspirationBlogger, InspirationBlogger.blogger_id == Blogger.id)
        .join(Inspiration, Inspiration.id == InspirationBlogger.inspiration_id)
        .where(NOT_DELETED)
        .group_by(Blogger.id, Blogger.name, Blogger.platform)
        .union_all(
            select(
                Model.id.label("id"),
                Model.name.label("name"),
                Model.platform.label("platform"),
                func.count(InspirationModel.inspiration_id).label("cnt"),
            )
            .join(InspirationModel, InspirationModel.model_id == Model.id)
            .join(Inspiration, Inspiration.id == InspirationModel.inspiration_id)
            .where(NOT_DELETED)
            .group_by(Model.id, Model.name, Model.platform)
        )
        .subquery()
    )
    rows = (await db.execute(
        select(combined.c.id, combined.c.name, combined.c.platform, combined.c.cnt)
        .select_from(combined)
        .order_by(combined.c.cnt.desc(), combined.c.name.asc())
        .limit(limit)
    )).all()
    return [
        {
            "id": r[0],
            "name": r[1],
            "person_type": "blogger",
            "platform": r[2],
            "count": r[3],
        }
        for r in rows
    ]


@router.get("/audit-logs")
async def audit_logs(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
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
            "created_at": format_utc(r.created_at),
        }
        for r in rows
    ]


class NearDuplicateScanRequest(BaseModel):
    """近似重复检测请求参数（扫描动作 + 哈希缓存补算副作用，故用 POST）。"""

    # limit=0 表示全量扫描（服务层约定）：phash 已缓存时分组是纯内存运算，
    # 万级素材也在秒级完成，比「随机抽样漏检后反复扫」体验更好。随机抽样
    # 仍可显式传 500/1000/2000/5000；上限 100000 仅作防御，实际素材数远低于此。
    limit: int = Field(1000, ge=0, le=100000, description="随机抽样图片数量上限；0 表示全量扫描")
    threshold: int = Field(32, ge=1, le=256, description="汉明距离阈值（越小越严格，768 位 RGB dHash）")


@router.post("/near-duplicates")
async def near_duplicates(
    payload: NearDuplicateScanRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """检测视觉近似重复的图片素材（感知哈希分组），仅返回候选、不删除。

    与「重复文件」（SHA-256 精确重复）互补：本接口识别字节不同但视觉相似的
    近似重复（不同压缩/缩放/水印），由前端并排预览后人工确认删除。

    扫描规则：全库图片**随机抽样**（每次覆盖不同素材）；感知哈希首次计算后
    缓存到 inspirations.phash，本接口会顺带补算缺失哈希（单次有限额）。
    """
    from app.services.near_duplicate_service import scan_near_duplicates

    return await scan_near_duplicates(
        db, limit=payload.limit, threshold=payload.threshold
    )


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
async def vector_stats(db: AsyncSession = Depends(get_db)) -> dict:
    """向量化状态统计：素材总数 / 已有向量数 / 缺失数 / 文本向量公式版本（供管理页展示）。"""
    from app.services.vector import store as vector_store
    from app.services.vector.embedding import TEXT_EMBEDDING_FORMULA_VERSION

    missing_ids = await _get_missing_image_vector_ids(db)
    text_vectors = await vector_store.count_vectors("text")
    stored_version = vector_store.get_stored_text_formula_version()
    return {
        "total_inspirations": (
            await db.execute(
                select(func.count()).select_from(Inspiration).where(
                    NOT_DELETED, Inspiration.media_type == "image"
                )
            )
        ).scalar() or 0,
        "image_vectors": await vector_store.count_vectors("image"),
        "text_vectors": text_vectors,
        "missing": len(missing_ids),
        "lancedb_available": vector_store.is_lancedb_available(),
        "text_vector_version": {
            "current": TEXT_EMBEDDING_FORMULA_VERSION,
            "stored": stored_version,
            # stored 为 None（从未记录）视为旧版本：v2 上线前的存量向量都由旧公式生成
            "stale": text_vectors > 0 and stored_version != TEXT_EMBEDDING_FORMULA_VERSION,
        },
    }


class VectorBackfillRequest(BaseModel):
    """向量回填触发参数。"""

    rebuild_text: bool = Field(
        default=False,
        description="为 True 时全量重建文本向量（公式版本升级后启用正文语义搜索），跳过图像向量",
    )


@router.post("/vector-backfill")
async def vector_backfill(
    payload: VectorBackfillRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """一键为缺失向量的素材创建向量回填任务（异步，由 worker 执行）。

    手动触发语义：立即 flush 攒批队列——把「缺失向量的素材」与待回填表中
    积累的素材（未达自动触发阈值的）合并为一个批量任务，不等阈值。

    rebuild_text=True 时改为全量重建文本向量（mode=text，跳过图像向量的
    CLIP 编码），用于文本公式版本升级后让存量向量获得正文 caption 语义。

    返回 task_id 供前端轮询进度；无缺失素材且无待回填素材时返回 count=0。
    """
    from app.services.task_runners.vector_backfill import (
        create_vector_backfill_task,
        flush_pending_vector_backfills,
    )
    from app.services.vector import store as vector_store

    if not vector_store.is_lancedb_available():
        raise HTTPException(
            status_code=400,
            detail="lancedb 未安装，请先执行：pip install lancedb",
        )

    rebuild_text = bool(payload and payload.rebuild_text)
    if rebuild_text:
        result = await db.execute(select(Inspiration.id).where(NOT_DELETED))
        all_ids = [row[0] for row in result.all()]
        task = await create_vector_backfill_task(db, all_ids, mode="text")
        if task is None:
            return {
                "message": "没有可重建文本向量的素材",
                "task_id": None,
                "count": 0,
            }
        return {
            "message": f"已创建文本向量重建任务 #{task.id}，共 {task.total} 个素材",
            "task_id": task.id,
            "count": task.total,
        }

    missing_ids = await _get_missing_image_vector_ids(db)
    task = await flush_pending_vector_backfills(db, force=True, extra_ids=missing_ids)
    if task is None:
        return {
            "message": "没有缺失向量的素材，全部已入库",
            "task_id": None,
            "count": 0,
        }
    return {
        "message": f"已创建向量回填任务 #{task.id}，共 {task.total} 个素材",
        "task_id": task.id,
        "count": task.total,
    }


# ============ 手机图剪裁（一键裁剪手动上传的竖屏截图） ============


class PhoneCropRequest(BaseModel):
    """手机图剪裁请求参数。"""

    mode: str = Field(default="auto", description="auto 黑边自动检测 / ratio 固定比例 / content 内容边界检测")
    crop_top: float = Field(default=0.03, ge=0, lt=0.5, description="顶部裁剪比例（仅 ratio 模式）")
    crop_bottom: float = Field(default=0.05, ge=0, lt=0.5, description="底部裁剪比例（仅 ratio 模式）")
    limit: int = Field(default=200, ge=1, le=1000, description="单次最多返回候选数")
    cursor: str | None = Field(default=None, description="分页游标（上一批返回的 next_cursor，继续扫描剩余素材）")
    time_budget: float = Field(default=60.0, ge=5, le=300, description="单次扫描时间预算（秒），预算耗尽返回已找到候选")
    vlm_review: bool = Field(default=True, description="AI 复核（较慢，约 1.3s/候选）：VLM 判断顶部状态栏/底部进度条，阳性候选置顶并标注")


class PhoneCropApplyRequest(BaseModel):
    """手机图剪裁执行请求：用户勾选确认的素材 ID 列表。"""

    ids: list[str] = Field(..., description="勾选确认要裁剪的素材 ID 列表")
    mode: str = Field(default="auto", description="auto 黑边自动检测 / ratio 固定比例 / content 内容边界检测")
    crop_top: float = Field(default=0.03, ge=0, lt=0.5, description="顶部裁剪比例（仅 ratio 模式）")
    crop_bottom: float = Field(default=0.05, ge=0, lt=0.5, description="底部裁剪比例（仅 ratio 模式）")


@router.post("/crop-phone-screenshots/scan")
async def crop_phone_screenshots_scan(
    payload: PhoneCropRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """扫描手机图剪裁候选（只读）：手动上传竖屏截图清单 + 逐张裁剪信息，供人工确认。"""
    from app.services.crop_service import scan_candidates

    try:
        return await scan_candidates(
            db,
            mode=payload.mode,
            crop_top=payload.crop_top,
            crop_bottom=payload.crop_bottom,
            limit=payload.limit,
            cursor=payload.cursor,
            time_budget=payload.time_budget,
            vlm_review=payload.vlm_review,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/crop-phone-screenshots/apply")
async def crop_phone_screenshots_apply(
    payload: PhoneCropApplyRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """按勾选确认的素材执行手机图剪裁：备份原图、裁剪替换、重建缩略图/哈希/主色调并入队向量回填。"""
    from app.services.crop_service import apply_crops

    if not payload.ids:
        raise HTTPException(status_code=400, detail="请至少勾选一个要裁剪的素材")

    try:
        return await apply_crops(
            db,
            ids=payload.ids,
            mode=payload.mode,
            crop_top=payload.crop_top,
            crop_bottom=payload.crop_bottom,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
