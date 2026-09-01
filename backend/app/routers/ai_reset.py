"""数据重置路由：最高危操作，带四重防呆（快照/确认文字/裸奔兜底/审计）。"""

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete

from app.config import settings
from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.routers.ai_shared import _active_analyses, _analysis_tasks
from app.services.audit_service import record_audit_log
from app.utils.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter()

# reset 必须精确输入的确认文字（防止误点）
CONFIRM_TEXT = "DELETE"
# 回环来源：本机访问；TestClient 的 host 固定为 "testclient"，一并视为本机
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "testclient"}
# reset 前快照保留天数
PRE_RESET_SNAPSHOT_RETENTION_DAYS = 7


def _is_loopback(host: str | None) -> bool:
    """判断请求来源是否为本机回环地址。"""
    if not host:
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _take_pre_reset_snapshot() -> tuple[Path | None, int]:
    """reset 执行前对「将被销毁的数据」做轻量快照，保留 7 天。

    快照内容（reset 实际会销毁的部分）：
    - fashion_inspo.db（含 -wal/-shm）：复制（DB 须留在原位供 reset 的 ORM 操作）；
    - images/ thumbnails/ videos/ lancedb：同盘移动到快照目录后原位重建空目录
      （reset 本来就要 rmtree 这些目录，移动是秒级 rename，不阻塞；原位重建保证
      reset 后续的上传/向量重建不因目录缺失而失败）。

    person_photos/ trash/ 等目录 reset 本身不删除文件，故不纳入快照（不扩大
    reset 的破坏面，也省去 ~800MB 拷贝）。快照位于项目盘，只防误操作、不防
    磁盘损坏——那是定时异盘备份（scripts/backup_data.sh）的职责。

    返回 (快照目录, 从活动存储移走的文件数)；快照失败时返回 (None, 0)。
    """
    moved_files = 0
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        snap_dir = settings.storage_root / "_pre_reset_snapshot" / stamp
        snap_dir.mkdir(parents=True, exist_ok=False)

        # DB 用 sqlite3 backup API 做一致性快照（WAL 下在线安全，避免直接
        # 拷贝 .db/-wal/-shm 在并发写入时拿到半截数据）
        import sqlite3

        db_src = settings.storage_root.parent / "fashion_inspo.db"
        if db_src.exists():
            src = sqlite3.connect(str(db_src))
            dst = sqlite3.connect(str(snap_dir / "fashion_inspo.db"))
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()

        for sub in ("images", "thumbnails", "videos", "keyframes", "lancedb"):
            src = settings.storage_root / sub
            if src.exists():
                moved_files += sum(1 for _ in src.rglob("*") if _.is_file())
                shutil.move(str(src), str(snap_dir / sub))
                src.mkdir(parents=True, exist_ok=True)

        logger.info(f"reset 前快照已生成: {snap_dir}（移走 {moved_files} 个文件）")
        return snap_dir, moved_files
    except Exception as e:
        # 快照失败不应阻断已被明确确认的 reset，但要留下醒目日志
        logger.error(f"reset 前快照生成失败（继续执行 reset）: {e}")
        return None, 0


def cleanup_expired_snapshots() -> int:
    """删除超过保留期的 reset/restore 前快照目录，返回清理数量。

    在后端启动时调用一次（reset/restore 均为低频操作，无需周期轮询）。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRE_RESET_SNAPSHOT_RETENTION_DAYS)
    cleaned = 0
    for parent_name in ("_pre_reset_snapshot", "_pre_restore_snapshot"):
        parent = settings.storage_root / parent_name
        if not parent.exists():
            continue
        for child in parent.iterdir():
            if not child.is_dir():
                continue
            try:
                mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    cleaned += 1
            except Exception as e:
                logger.warning(f"清理过期快照失败 {child}: {e}")
    if cleaned:
        logger.info(f"[启动清理] 删除 {cleaned} 个过期的 reset/restore 前快照")
    return cleaned


# ============ 数据重置 ============


@router.delete("/reset")
async def reset_all_data(
    request: Request,
    confirm: str = Query("no", description="输入 'yes' 二次确认删除所有数据"),
    confirm_text: str = Query(
        "", description=f"必须精确输入 {CONFIRM_TEXT} 才能执行重置（防误触）"
    ),
    _api_key: str | None = Depends(require_api_key),
) -> dict:
    """重置所有数据：清空数据库所有表 + 删除存储文件。

    四重防呆（T5）：
    1. 执行前自动快照 DB 与素材目录到 _pre_reset_snapshot/（保留 7 天）；
    2. confirm=yes 且 confirm_text=DELETE 双重确认；
    3. 未配置 API Key 的开发模式下，非回环来源直接拒绝（403）；
    4. 完成后写 audit_logs 留痕。
    """
    # 确认文字（强人工确认，防误点）
    if confirm != "yes" or confirm_text != CONFIRM_TEXT:
        raise HTTPException(
            status_code=400,
            detail=(
                f"需要 confirm=yes 且 confirm_text={CONFIRM_TEXT} 确认。"
                "此操作将删除所有素材、标签、分析记录和照片文件，且不可恢复！"
            ),
        )

    # 裸奔兜底：未配 API Key 时，仅允许本机回环执行 reset
    client_host = request.client.host if request.client else None
    if not settings.api_key and not _is_loopback(client_host):
        logger.warning(f"reset 被拒绝：未配置 API Key 且来源非回环（{client_host}）")
        raise HTTPException(
            status_code=403,
            detail="未配置 API Key 时，数据重置仅允许本机访问。请配置 API Key 或从本机执行。",
        )

    import asyncio as aio
    from app.models.person import (
        Blogger,
        InspirationBlogger,
        InspirationModel,
        Model,
        ModelPhoto,
        ModelPhotoSet,
    )
    from app.models.tag import InspirationTag, Tag, TagAlias
    from app.models.task import TaskQueue
    from app.models.scraper import ScraperSchedule, ScraperSeenURL, ScraperTask

    # 先取消所有进行中的分析任务，再做快照，保证快照的是静止状态的数据
    if _analysis_tasks:
        logger.info(f"取消 {len(_analysis_tasks)} 个进行中的分析任务...")
        for t in list(_analysis_tasks):
            t.cancel()
        _active_analyses.clear()
        await aio.sleep(1)  # 给任务 1 秒处理取消

    # 防护 1：执行前快照（DB 一致性备份 + 素材目录移动，秒级）
    snapshot_dir, snap_moved = await aio.to_thread(_take_pre_reset_snapshot)

    async with async_session() as db:
        # 按外键依赖顺序删除（先删子表，再删主表）。
        # audit_logs 刻意保留：审计日志的意义是留痕，本次重置动作本身也会记入。
        tables_in_order = [
            (InspirationTag, "inspiration_tags"),
            (AIAnalysisLog, "ai_analysis_log"),
            (ModelPhoto, "model_photos"),
            (ModelPhotoSet, "model_photo_sets"),
            (InspirationBlogger, "inspiration_bloggers"),
            (InspirationModel, "inspiration_models"),
            (ScraperTask, "scraper_tasks"),
            (Inspiration, "inspirations"),
            (Blogger, "bloggers"),
            (Model, "models"),
            (TagAlias, "tag_aliases"),
            (Tag, "tags"),
            (ScraperSeenURL, "scraper_seen_urls"),  # 墓碑表：重置后不应再跳过旧 URL
            (ScraperSchedule, "scraper_schedules"),  # 定时计划：不清空则重置后自动复活采集
            (TaskQueue, "task_queue"),  # 队列：不清空则重置后残留任务继续执行
        ]
        deleted_counts = {}
        total_rows = 0
        for table_model, table_name in tables_in_order:
            result = await db.execute(delete(table_model))
            rc = result.rowcount or 0
            deleted_counts[table_name] = rc
            total_rows += rc
        await db.commit()

    # 丢弃缓存的向量连接并清空向量库目录。
    # 必须在跨进程写锁内执行：reset 删除目录若与 worker 的向量写入并发，会把
    # 正在写入的数据集目录删成「空骨架」（表注册存在但 _versions/data 全空），
    # 之后所有向量操作报 "Table exists but could not be loaded"，管理页显示
    # 大量缺失向量——历史事故根因（见 vector.store.reset_lancedb_storage）。
    from app.services.vector import store as vector_store

    await vector_store.reset_lancedb_storage()

    # 清空素材存储目录（threadpool 异步执行，避免阻塞）。
    # 注意：images/thumbnails/videos 已在快照阶段移动并重建为空目录，这里
    # rmtree 兜底快照后新写入的文件；向量库（lancedb）不在此列——它已由上方
    # reset_lancedb_storage() 在跨进程写锁内删除（无锁 rmtree 会与 worker 并发
    # 写入竞争把数据集删成空骨架，见该函数 docstring）。从活动存储移走的文件
    # 数由 snap_moved 统计，一并计入 files_deleted。
    storage_deleted = snap_moved
    storage_errors = []
    for dir_path in [settings.images_dir, settings.thumbnails_dir, settings.videos_dir, settings.keyframes_dir]:
        if dir_path.exists():
            file_count = len(list(dir_path.iterdir()))

            def _rmtree(p: Path = dir_path) -> None:
                shutil.rmtree(p)
                p.mkdir(parents=True)

            try:
                await aio.to_thread(_rmtree)
                storage_deleted += file_count
            except Exception as e:
                storage_errors.append(f"{dir_path.name}: {e}")

    result_msg = "所有数据已重置"
    if storage_errors:
        result_msg += f"（{len(storage_errors)} 个目录删除失败）"
        logger.warning(f"存储目录删除错误: {storage_errors}")

    logger.warning(
        f"⚠ 数据已全部重置！数据库: {deleted_counts}, 文件: {storage_deleted} 个, "
        f"来源: {client_host}, 快照: {snapshot_dir}"
    )

    # 防护 4：审计留痕（独立会话，失败不影响 reset 结果）
    await record_audit_log(
        action="reset",
        target_type="all",
        count=total_rows,
        freed_bytes=0,
        detail=json.dumps(
            {
                "tables": deleted_counts,
                "files_deleted": storage_deleted,
                "snapshot": str(snapshot_dir) if snapshot_dir else None,
                "source_ip": client_host,
                "storage_errors": storage_errors,
            },
            ensure_ascii=False,
        ),
    )

    return {
        "message": result_msg,
        "database": deleted_counts,
        "files_deleted": storage_deleted,
        "snapshot": str(snapshot_dir) if snapshot_dir else None,
        "storage_errors": storage_errors if storage_errors else None,
    }
