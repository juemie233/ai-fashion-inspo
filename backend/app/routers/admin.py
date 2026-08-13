"""素材管理后台 — 统计、完整性检查、批量操作。"""

import os
import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.inspiration import AIAnalysisLog, Inspiration, analysis_log_filter
from app.models.tag import Tag, InspirationTag

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _file_hash(path: Path) -> str | None:
    """计算文件的 MD5 哈希，用于重复检测。"""
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


# 素材媒体目录：完整性检查只扫描这些目录，排除 lancedb（向量库）、logs（日志）、
# cookies、debug 等非素材数据，否则会把向量库内部文件误判为「孤立文件」。
_INSP_MEDIA_DIRS = ("images", "thumbnails", "videos")


def _scan_storage_files() -> dict[str, int]:
    """扫描素材媒体目录（images/thumbnails/videos）中的实际文件，返回 {相对路径: 字节数}。"""
    files: dict[str, int] = {}
    storage_root = settings.storage_root
    if not storage_root.exists():
        return files
    for dir_name in _INSP_MEDIA_DIRS:
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


@router.get("/stats")
async def admin_stats(db: AsyncSession = Depends(get_db)):
    """素材总览仪表盘数据：
    - 总数、总大小、缩略图大小
    - 按来源类型 / 媒体类型 / 分析状态 / 月份分组统计
    """
    # 素材总数
    total_count = (await db.execute(select(func.count(Inspiration.id)))).scalar() or 0

    # 按来源类型
    source_stats = (await db.execute(
        select(Inspiration.source_type, func.count(Inspiration.id))
        .group_by(Inspiration.source_type)
    )).all()

    # 按媒体类型
    media_stats = (await db.execute(
        select(Inspiration.media_type, func.count(Inspiration.id))
        .group_by(Inspiration.media_type)
    )).all()

    # 分析状态统计：通过子查询计算
    # — "done": 有分析日志且全部成功
    # — "error": 有分析日志且至少一条失败
    # — "pending": 没有任何分析日志

    # 有分析日志的素材（去重）
    analyzed_ids_subq = (
        select(AIAnalysisLog.inspiration_id)
        .where(analysis_log_filter(), AIAnalysisLog.inspiration_id.isnot(None))
        .distinct()
    ).subquery()

    # 分析失败的素材 ID
    failed_ids_subq = (
        select(AIAnalysisLog.inspiration_id)
        .where(
            analysis_log_filter(),
            AIAnalysisLog.error.isnot(None),
            AIAnalysisLog.error != "",
        )
        .distinct()
    ).subquery()

    total_count_val = (await db.execute(
        select(func.count(Inspiration.id))
    )).scalar() or 0

    error_count = (await db.execute(
        select(func.count())
        .select_from(failed_ids_subq)
    )).scalar() or 0

    analyzed_count = (await db.execute(
        select(func.count())
        .select_from(analyzed_ids_subq)
    )).scalar() or 0

    done_count = analyzed_count - error_count
    pending_count = total_count_val - analyzed_count

    # 无标签素材数
    untagged_count = (await db.execute(
        select(func.count(Inspiration.id))
        .outerjoin(InspirationTag, Inspiration.id == InspirationTag.inspiration_id)
        .where(InspirationTag.inspiration_id.is_(None))
    )).scalar() or 0

    # 分析失败数（带日志）
    analysis_failed_count = error_count

    # 收藏数
    favorite_count = (await db.execute(
        select(func.count(Inspiration.id))
        .where(Inspiration.is_favorite == True)
    )).scalar() or 0

    # 标签总数
    total_tags = (await db.execute(select(func.count(Tag.id)))).scalar() or 0

    # 墓碑表记录数（已采集 URL 去重）
    from app.models.scraper import ScraperSeenURL
    tombstone_count = (await db.execute(
        select(func.count(ScraperSeenURL.source_url))
    )).scalar() or 0

    # 扫描文件系统中实际文件大小
    storage_files = _scan_storage_files()
    total_size_bytes = sum(storage_files.values())

    # 缩略图大小
    thumbnail_size_bytes = sum(
        s for p, s in storage_files.items() if "thumbnails" in p
    )

    # 图片文件大小
    images_size_bytes = sum(
        s for p, s in storage_files.items() if p.startswith("images")
    )

    # 按月份统计（最近 12 个月）
    month_stats = (await db.execute(
        select(
            func.strftime("%Y-%m", Inspiration.created_at).label("month"),
            func.count(Inspiration.id).label("count"),
        )
        .where(Inspiration.created_at.isnot(None))
        .group_by("month")
        .order_by(text("month DESC"))
        .limit(12)
    )).all()

    return {
        "total_count": total_count,
        "total_size_bytes": total_size_bytes,
        "thumbnail_size_bytes": thumbnail_size_bytes,
        "images_size_bytes": images_size_bytes,
        "untagged_count": untagged_count,
        "analysis_failed_count": analysis_failed_count,
        "favorite_count": favorite_count,
        "total_tags": total_tags,
        "tombstone_count": tombstone_count,
        "by_source_type": [
            {"source_type": s[0] or "unknown", "count": s[1]}
            for s in source_stats
        ],
        "by_media_type": [
            {"media_type": s[0] or "unknown", "count": s[1]}
            for s in media_stats
        ],
        "by_analysis_status": [
            {"status": "done", "count": done_count, "label": "已分析"},
            {"status": "error", "count": error_count, "label": "分析失败"},
            {"status": "pending", "count": pending_count, "label": "未分析"},
        ],
        "by_month": [
            {"month": s[0], "count": s[1]} for s in month_stats
        ],
    }


@router.get("/largest-files")
async def largest_files(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """列出占用空间最大的前 N 个文件。"""
    storage_root = settings.storage_root
    result = (await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.source_type, Inspiration.created_at)
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

    # 所有数据库记录的 file_path 和 thumbnail_path
    db_files_result = (await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.thumbnail_path)
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

    # 扫描磁盘媒体文件，找孤立文件（_scan_storage_files 已排除 lancedb/logs 等非素材目录）
    disk_files = _scan_storage_files()
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
    storage_files = _scan_storage_files()
    storage_root = settings.storage_root

    # 这里需要一个同步的数据库会话来获取文件列表
    from app.database import async_session
    async with async_session() as db:
        result = await db.execute(
            select(Inspiration.file_path, Inspiration.thumbnail_path)
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
    """批量删除素材。

    请求体:
        {"ids": ["id1", "id2", ...]}  — 按 ID 列表删除
        {"condition": "untagged"}     — 按条件删除（无标签的）
        {"condition": "analysis_failed"} — 按条件删除（分析失败的）
    """
    ids = payload.get("ids", [])
    condition = payload.get("condition")

    if not ids and not condition:
        raise HTTPException(status_code=400, detail="请提供 ids 列表或 condition 条件")

    # 按条件查询要删除的 ID
    if condition == "untagged":
        result = await db.execute(
            select(Inspiration.id)
            .outerjoin(InspirationTag, Inspiration.id == InspirationTag.inspiration_id)
            .where(InspirationTag.inspiration_id.is_(None))
        )
        ids = [r[0] for r in result.all()]
    elif condition == "analysis_failed":
        # 查询有分析失败日志的素材 ID
        result = await db.execute(
            select(AIAnalysisLog.inspiration_id)
            .where(
                analysis_log_filter(),
                AIAnalysisLog.error.isnot(None),
                AIAnalysisLog.error != "",
            )
            .distinct()
        )
        ids = [r[0] for r in result.all()]

    if not ids:
        return {"deleted_count": 0, "freed_bytes": 0}

    # 先获取文件路径和 source_url，用于删除磁盘文件和写入墓碑表
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.thumbnail_path, Inspiration.source_url)
        .where(Inspiration.id.in_(ids))
    )
    files_to_delete = result.all()

    storage_root = settings.storage_root
    freed_bytes = 0
    for fid, fpath, thumb, _surl in files_to_delete:
        for p in (fpath, thumb):
            if p:
                full = storage_root / p
                try:
                    if full.exists():
                        freed_bytes += full.stat().st_size
                        full.unlink()
                except Exception:
                    pass

    # 写入墓碑表（防止重复采集）
    urls_to_seal = [r[3] for r in files_to_delete if r[3]]
    if urls_to_seal:
        from app.models.scraper import ScraperSeenURL
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        for url in urls_to_seal:
            await db.execute(
                sqlite_insert(ScraperSeenURL)
                .values(source_url=url)
                .prefix_with("OR IGNORE")
            )

    # 从数据库删除
    await db.execute(
        Inspiration.__table__.delete().where(Inspiration.id.in_(ids))
    )
    await db.commit()

    return {
        "deleted_count": len(ids),
        "freed_bytes": freed_bytes,
    }


def _build_hash_map(
    db_records: list[tuple],
    storage_root: Path,
    include_meta: bool = False,
) -> dict[str, list[dict]]:
    """根据数据库记录构建文件哈希映射。

    Args:
        db_records: (id, file_path, ...) 查询结果
        storage_root: 存储根目录
        include_meta: 是否包含 thumbnail_path、is_favorite、created_at 等元数据

    Returns:
        {hash: [{id, file_path, size_bytes, ...}]}
    """
    hash_map: dict[str, list[dict]] = {}
    for row in db_records:
        rid = row[0]
        fpath = row[1]
        if not fpath:
            continue
        full = storage_root / fpath
        if not full.exists():
            continue
        fhash = _file_hash(full)
        if fhash is None:
            continue
        entry: dict = {
            "id": rid,
            "file_path": fpath,
            "size_bytes": full.stat().st_size,
        }
        if include_meta and len(row) >= 5:
            entry["thumbnail_path"] = row[2]
            entry["is_favorite"] = row[3]
            entry["created_at"] = row[4]
        hash_map.setdefault(fhash, []).append(entry)
    return hash_map


@router.get("/check-duplicate")
async def check_duplicate(
    hash: str = Query(..., min_length=32, max_length=32, description="文件 MD5 哈希"),
    db: AsyncSession = Depends(get_db),
):
    """检查指定 MD5 的文件是否已存在（上传前去重）。"""
    # 先检查数据库中是否有相同哈希
    result = await db.execute(
        select(Inspiration).limit(500)
    )
    inspirations = result.scalars().all()

    storage_root = settings.storage_root
    for insp in inspirations:
        if insp.file_path:
            fpath = storage_root / insp.file_path
            fhash = _file_hash(fpath)
            if fhash and fhash == hash:
                return {
                    "exists": True,
                    "inspiration_id": insp.id,
                    "file_path": insp.file_path,
                }

    return {"exists": False, "inspiration_id": None, "file_path": None}


@router.get("/duplicates")
async def find_duplicates(db: AsyncSession = Depends(get_db)):
    """通过文件哈希检测完全重复的素材。"""
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path)
    )
    hash_map = _build_hash_map(result.all(), settings.storage_root)

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
    """智能去重：每组重复文件保留评分最高的 1 个，其余物理删除。

    评分规则：
      +100 — 已被标签标记
      +50  — 已收藏
      +30  — AI 分析成功
      +10  — 有缩略图
      +5   — 创建时间更早（平局时选 ID 更小的）
    """
    storage_root = settings.storage_root

    # 1. 扫描所有重复组
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.thumbnail_path,
               Inspiration.is_favorite, Inspiration.created_at)
    )
    hash_map = _build_hash_map(result.all(), storage_root, include_meta=True)

    # 只处理有重复的组
    dup_groups = [(h, files) for h, files in hash_map.items() if len(files) > 1]
    if not dup_groups:
        return {"groups_processed": 0, "files_deleted": 0, "freed_bytes": 0, "details": []}

    # 2. 对每个文件查询标签和分析状态，计算评分
    all_ids = [f["id"] for _h, group in dup_groups for f in group]

    # 查询哪些文件有标签
    tagged_result = await db.execute(
        select(InspirationTag.inspiration_id)
        .where(InspirationTag.inspiration_id.in_(all_ids))
        .distinct()
    )
    tagged_ids = {r[0] for r in tagged_result.all()}

    # 查询哪些文件 AI 分析成功
    analyzed_result = await db.execute(
        select(AIAnalysisLog.inspiration_id)
        .where(
            analysis_log_filter(),
            AIAnalysisLog.inspiration_id.in_(all_ids),
            (AIAnalysisLog.error.is_(None)) | (AIAnalysisLog.error == ""),
        )
        .distinct()
    )
    analyzed_ids = {r[0] for r in analyzed_result.all()}

    # 3. 每组评分并决定保留哪个
    details: list[dict] = []
    ids_to_delete: list[str] = []
    files_to_delete: list[tuple[str, str | None]] = []

    for dup_hash, group in dup_groups:
        scored = []
        for f in group:
            score = 0
            reasons: list[str] = []

            # 已被标签标记
            if f["id"] in tagged_ids:
                score += 100
                reasons.append("有标签")
            # 已收藏
            if f["is_favorite"]:
                score += 50
                reasons.append("已收藏")
            # AI 分析成功
            if f["id"] in analyzed_ids:
                score += 30
                reasons.append("AI 已分析")
            # 有缩略图
            if f["thumbnail_path"]:
                score += 10
                reasons.append("有缩略图")
            # 创建时间更早的加分（用于平局决胜）
            created_ts = f["created_at"].timestamp() if f["created_at"] else 0

            scored.append({**f, "score": score, "reasons": reasons, "created_ts": created_ts})

        # 排序：score 降序 → created_ts 升序（更早的在前）→ id 升序
        scored.sort(key=lambda x: (-x["score"], x["created_ts"], x["id"]))
        keeper = scored[0]
        victims = scored[1:]

        # 安全检查：如果保留文件的磁盘文件已不存在，跳过整组（防止误删所有副本）
        keeper_full = storage_root / keeper["file_path"]
        if not keeper_full.exists():
            # 保留文件丢失了，找下一个有磁盘文件的作为保留
            found = False
            for alt in scored[1:]:
                alt_full = storage_root / alt["file_path"]
                if alt_full.exists():
                    keeper = alt
                    victims = [f for f in scored if f["id"] != alt["id"]]
                    found = True
                    break
            if not found:
                # 整组文件都不在磁盘上了，跳过
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

    if not ids_to_delete:
        return {"groups_processed": 0, "files_deleted": 0, "freed_bytes": 0, "details": []}

    # 4. 写入墓碑表（防止被删除图片的 URL 被重新采集）
    url_result = await db.execute(
        select(Inspiration.source_url).where(Inspiration.id.in_(ids_to_delete))
    )
    urls_to_seal = [r[0] for r in url_result.all() if r[0]]
    if urls_to_seal:
        from app.models.scraper import ScraperSeenURL
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        for url in urls_to_seal:
            await db.execute(
                sqlite_insert(ScraperSeenURL)
                .values(source_url=url)
                .prefix_with("OR IGNORE")
            )

    # 5. 先删 DB 记录（级联删除关联 tags 和 analysis_logs），再删磁盘文件
    await db.execute(
        Inspiration.__table__.delete().where(Inspiration.id.in_(ids_to_delete))
    )
    await db.commit()

    # 5. 物理删除磁盘文件
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

    return {
        "groups_processed": len(details),
        "files_deleted": len(ids_to_delete),
        "freed_bytes": freed_bytes,
        "details": details,
    }
