from app.services import inspiration_service
from app.utils.time import format_utc
from app.utils.file_hash import build_hash_map
"""素材管理统计服务：概览仪表盘聚合与存储目录扫描。

此前 admin_stats 的 10+ 条聚合查询全部写在 routers/admin.py（152 行函数），
按「路由薄、业务在 services」的约定整体下沉到本模块，并拆分为
若干 ≤60 行的小函数，便于单独阅读与测试。
"""

import asyncio

from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    NOT_DELETED,
    analysis_log_filter,
    latest_analysis_log_subquery,
)
from app.models.scraper import ScraperSeenURL
from app.models.tag import Tag, InspirationTag
from app.utils.time import utcnow
import csv
import io
from app.utils.csv_safety import sanitize_csv_cell
from app.services.inspiration_query import inspiration_load_options

# 素材媒体目录：完整性检查/存储统计只扫描这些目录，排除 lancedb（向量库）、
# logs（日志）、cookies、debug 等非素材数据，否则会把向量库内部文件误判为
# 「孤立文件」。垃圾桶（trash）同样不在此列：软删除文件已移入 trash/。
INSP_MEDIA_DIRS = ("images", "thumbnails", "videos")


def scan_storage_files() -> dict[str, int]:
    """扫描素材媒体目录中的实际文件，返回 {相对路径: 字节数}。"""
    files: dict[str, int] = {}
    storage_root = settings.storage_root
    if not storage_root.exists():
        return files
    for dir_name in INSP_MEDIA_DIRS:
        dir_path = storage_root / dir_name
        if not dir_path.exists():
            continue
        for fpath in dir_path.rglob("*"):
            if fpath.is_file():
                # 计算相对于 storage 的路径
                try:
                    rel = fpath.relative_to(storage_root).as_posix()
                except ValueError:
                    rel = str(fpath)
                files[rel] = fpath.stat().st_size
    return files


def _compute_storage_sizes(storage_files: dict[str, int]) -> dict[str, int]:
    """按目录聚合存储占用：总大小 / 缩略图 / 图片。"""
    return {
        "total_size_bytes": sum(storage_files.values()),
        "thumbnail_size_bytes": sum(
            s for p, s in storage_files.items() if "thumbnails" in p
        ),
        "images_size_bytes": sum(
            s for p, s in storage_files.items() if p.startswith("images")
        ),
    }


async def _query_base_counts(db: AsyncSession) -> dict[str, int]:
    """基础计数：素材总数、无标签数、收藏数、标签总数、墓碑数（均排除垃圾桶）。"""
    total_count = (await db.execute(
        select(func.count(Inspiration.id)).where(NOT_DELETED)
    )).scalar() or 0

    untagged_count = (await db.execute(
        select(func.count(Inspiration.id))
        .outerjoin(InspirationTag, Inspiration.id == InspirationTag.inspiration_id)
        .where(InspirationTag.inspiration_id.is_(None), NOT_DELETED)
    )).scalar() or 0

    favorite_count = (await db.execute(
        select(func.count(Inspiration.id))
        .where(Inspiration.is_favorite.is_(True), NOT_DELETED)
    )).scalar() or 0

    total_tags = (await db.execute(select(func.count(Tag.id)))).scalar() or 0

    tombstone_count = (await db.execute(
        select(func.count(ScraperSeenURL.source_url))
    )).scalar() or 0

    return {
        "total_count": total_count,
        "untagged_count": untagged_count,
        "favorite_count": favorite_count,
        "total_tags": total_tags,
        "tombstone_count": tombstone_count,
    }


async def _query_analysis_counts(db: AsyncSession) -> dict[str, int]:
    """分析状态计数：已分析（done）/ 失败（error）/ 未分析（pending）。"""
    non_deleted_ids = select(Inspiration.id).where(NOT_DELETED)

    analyzed_ids_subq = (
        select(AIAnalysisLog.inspiration_id)
        .where(
            analysis_log_filter(),
            AIAnalysisLog.inspiration_id.isnot(None),
            AIAnalysisLog.inspiration_id.in_(non_deleted_ids),
        )
        .distinct()
    ).subquery()

    # 「失败」按最新一条标签分析日志判定（latest 语义），
    # 避免「历史失败但最新已成功」的素材既计入已分析又被扣为失败
    latest_log_sub = latest_analysis_log_subquery()
    failed_ids_subq = (
        select(AIAnalysisLog.inspiration_id)
        .join(
            latest_log_sub,
            (AIAnalysisLog.inspiration_id == latest_log_sub.c.inspiration_id)
            & (AIAnalysisLog.id == latest_log_sub.c.max_id),
        )
        .where(
            AIAnalysisLog.error.isnot(None),
            AIAnalysisLog.error != "",
            AIAnalysisLog.inspiration_id.in_(non_deleted_ids),
        )
        .distinct()
    ).subquery()

    analyzed_count = (await db.execute(
        select(func.count()).select_from(analyzed_ids_subq)
    )).scalar() or 0

    error_count = (await db.execute(
        select(func.count()).select_from(failed_ids_subq)
    )).scalar() or 0

    total_count = (await db.execute(
        select(func.count(Inspiration.id)).where(NOT_DELETED)
    )).scalar() or 0

    return {
        "analyzed_count": analyzed_count,
        "error_count": error_count,
        "pending_count": total_count - analyzed_count,
    }


async def _query_distributions(db: AsyncSession) -> dict[str, list]:
    """分布统计：按来源类型 / 媒体类型 / 月份（最近 12 个月）。"""
    source_stats = (await db.execute(
        select(Inspiration.source_type, func.count(Inspiration.id))
        .where(NOT_DELETED)
        .group_by(Inspiration.source_type)
    )).all()

    media_stats = (await db.execute(
        select(Inspiration.media_type, func.count(Inspiration.id))
        .where(NOT_DELETED)
        .group_by(Inspiration.media_type)
    )).all()

    month_stats = (await db.execute(
        select(
            func.strftime("%Y-%m", Inspiration.created_at).label("month"),
            func.count(Inspiration.id).label("count"),
        )
        .where(Inspiration.created_at.isnot(None), NOT_DELETED)
        .group_by("month")
        .order_by(text("month DESC"))
        .limit(12)
    )).all()

    return {
        "by_source_type": [
            {"source_type": s[0] or "unknown", "count": s[1]} for s in source_stats
        ],
        "by_media_type": [
            {"media_type": s[0] or "unknown", "count": s[1]} for s in media_stats
        ],
        "by_month": [{"month": s[0], "count": s[1]} for s in month_stats],
    }


async def query_inspiration_trend(db: AsyncSession, days: int) -> dict:
    """按天统计新增素材数量（近 N 天），供管理页趋势图使用。

    ``created_at`` 按项目约定为 UTC（``utcnow`` 写入），截止时间必须同样用 UTC 计算，
    否则在非 UTC 时区（如 UTC+8）下按天边界整体偏移。
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


async def collect_stats(db: AsyncSession) -> dict:
    """汇总素材总览仪表盘数据（原 routers/admin.py 的 admin_stats 逻辑）。"""
    base = await _query_base_counts(db)
    analysis = await _query_analysis_counts(db)
    distributions = await _query_distributions(db)
    sizes = _compute_storage_sizes(await asyncio.to_thread(scan_storage_files))

    return {
        **base,
        **sizes,
        "analysis_failed_count": analysis["error_count"],
        "by_analysis_status": [
            {"status": "done", "count": analysis["analyzed_count"] - analysis["error_count"], "label": "已分析"},
            {"status": "error", "count": analysis["error_count"], "label": "分析失败"},
            {"status": "pending", "count": analysis["pending_count"], "label": "未分析"},
        ],
        **distributions,
    }

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


async def find_duplicates(db: AsyncSession) -> dict:
    """通过文件哈希检测完全重复的素材（按 storage_root 扫描文件大小/哈希）。

    从 ``routers/admin.py`` 下沉（C-3）。
    """
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


async def vector_stats(db: AsyncSession) -> dict:
    """向量化状态统计：素材总数 / 已有向量数 / 缺失数 / 文本向量公式版本。

    从 ``routers/admin.py`` 下沉（C-3）。
    """
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

async def largest_files(
    db: AsyncSession,
    limit: int = 20,
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


async def integrity_check(db: AsyncSession) -> dict:
    """数据完整性检查（管理页巡检）：
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
    disk_files = await asyncio.to_thread(scan_storage_files)
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


async def export_inspirations_csv(db: AsyncSession) -> tuple[str, str]:
    """导出全部未删除素材为 CSV，返回 ``(filename, csv_text)``（不含 BOM）。

    从 ``routers/admin.py`` 下沉（C-3）：服务层只产出文本，HTTP 细节（BOM / Content-Type /
    Content-Disposition）留给路由，避免业务层依赖 FastAPI 的 Response。
    """
    result = await db.execute(
        select(Inspiration)
        .options(
            *inspiration_load_options(),
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
    return filename, buf.getvalue()

