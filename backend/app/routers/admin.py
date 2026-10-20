"""素材管理后台 — 统计、完整性检查、批量操作、导出与趋势分析。"""

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.config import settings  # noqa: E402  (有意延迟导入：避免循环依赖)
from app.database import get_db  # noqa: E402  (有意延迟导入：避免循环依赖)
from app.models.inspiration import (  # noqa: E402  (有意延迟导入：避免循环依赖)
    AIAnalysisLog,
    Inspiration,
    NOT_DELETED,
    latest_analysis_log_subquery,
    utcnow,
)
from app.models.tag import InspirationTag  # noqa: E402  (有意延迟导入：避免循环依赖)
from app.services import admin_stats_service, backup_service, inspiration_service  # noqa: E402  (有意延迟导入：避免循环依赖)
from app.services import audit_service  # noqa: E402  (有意延迟导入：避免循环依赖)
from app.services.audit_service import record_audit_log  # noqa: E402  (有意延迟导入：避免循环依赖)
from app.schemas.admin import NearDuplicateScanRequest, PhoneCropRequest, PhoneCropApplyRequest, VectorBackfillRequest  # noqa: E402  (有意延迟导入：避免循环依赖)

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
    """列出占用空间最大的前 N 个文件；扫描在 ``admin_stats_service``。"""
    return await admin_stats_service.largest_files(db, limit)


@router.get("/integrity-check")
async def integrity_check(db: AsyncSession = Depends(get_db)) -> dict:
    """数据完整性检查（缺文件 / 孤立文件）；扫描在 ``admin_stats_service``。"""
    return await admin_stats_service.integrity_check(db)


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
    """检查指定 SHA-256 的文件是否已存在（上传前去重）；查询在 ``inspiration_service``。"""
    return await inspiration_service.check_duplicate_hash(db, hash)


@router.get("/find-duplicates")
async def find_duplicates(db: AsyncSession = Depends(get_db)) -> dict:
    """检测完全重复的素材（SHA-256 相同）；查询在 ``admin_stats_service``。"""
    return await admin_stats_service.find_duplicates(db)


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
    """导出全部未删除素材为 CSV（UTF-8 BOM，浏览器直接下载）；内容在 ``admin_stats_service``。"""
    filename, text = await admin_stats_service.export_inspirations_csv(db)
    return Response(
        content="\ufeff" + text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/trend")
async def inspiration_trend(
    days: int = Query(30, ge=1, le=365, description="统计最近 N 天"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """按天统计新增素材数量（近 N 天）；查询在 ``admin_stats_service`` 里。"""
    return await admin_stats_service.query_inspiration_trend(db, days)


@router.get("/person-frequency")
async def person_frequency(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """按关联素材数量降序返回人物（博主+模特合并）；查询在 ``admin_stats_service``。"""
    return await admin_stats_service.person_frequency(db, limit)


@router.get("/audit-logs")
async def audit_logs(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """按时间倒序返回破坏性操作审计日志；查询在 ``audit_service``。"""
    return await audit_service.query_audit_logs(db, limit)


@router.post("/near-duplicates")
async def near_duplicates(
    payload: NearDuplicateScanRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """检测视觉近似重复的图片素材（感知哈希分组），仅返回候选、不删除。

    与「重复文件」（SHA-256 精确重复）互补：本接口识别字节不同但视觉相似的
    近似重复（不同压缩/缩放/水印），由前端并排预览后人工确认删除。

    扫描规则：全库图片**随机抽样**（每次覆盖不同素材）；感知哈希首次计算后
    缓存到 inspirations.phash，本接口会顺带补算缺失哈希，但**有时间预算**
    （`BACKFILL_TIME_BUDGET_SECONDS`）——大库全量补算需数十分钟，压在一次请求里
    会超时。返回里的 `missing` / `cache_complete` 说明缓存是否已就绪：未就绪时
    用 `POST /api/admin/phash-backfill` 起后台任务一次性补齐。
    """
    from app.services.near_duplicate_service import scan_near_duplicates

    return await scan_near_duplicates(
        db, limit=payload.limit, threshold=payload.threshold
    )


@router.post("/phash-backfill")
async def phash_backfill(db: AsyncSession = Depends(get_db)) -> dict:
    """创建「感知哈希缓存补齐」后台任务（近似重复扫描的前置）。

    扫描接口只做限时补算（避免请求超时），本接口把剩余缺失交给 worker 一次性补齐。
    幂等：已有 pending/running 同类任务时返回该任务（`reused=true`）不重复入队；
    缓存已完整时返回 400。返回 200 而非 201：本接口是「确保缓存就绪」的幂等语义，
    复用既有任务同样算成功。
    """
    from app.services.task_runner import create_phash_backfill_task

    created = await create_phash_backfill_task(db)
    if created["task_id"] is None:
        if created["total"] == 0:
            raise HTTPException(status_code=400, detail="哈希缓存已完整，无需补齐")
    total = created["total"]
    return {
        **created,
        "message": (
            f"已有补齐任务在跑（#{created['task_id']}，待补约 {total} 张），可在任务中心查看进度"
            if created["reused"]
            else f"已创建哈希缓存补齐任务（{total} 张），可在任务中心查看进度"
        ),
    }


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
    """向量化状态统计；查询在 ``admin_stats_service``。"""
    return await admin_stats_service.vector_stats(db)


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
        raise HTTPException(status_code=400, detail=str(e)) from None


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
        raise HTTPException(status_code=400, detail=str(e)) from None
