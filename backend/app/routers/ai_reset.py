"""AI 子路由。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete

from app.config import settings
from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.routers.ai_shared import _active_analyses, _analysis_tasks
from app.utils.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


# ============ 数据重置 ============


@router.delete("/reset")
async def reset_all_data(
    confirm: str = Query("no", description="输入 'yes' 二次确认删除所有数据"),
    _api_key: str = Depends(require_api_key),
) -> dict:
    """重置所有数据：清空数据库所有表 + 删除存储文件。

    危险操作，需 query 参数 confirm=yes 才执行。
    """
    if confirm != "yes":
        raise HTTPException(
            status_code=400,
            detail="需要 confirm=yes 确认。此操作将删除所有素材、标签、分析记录和照片文件！",
        )

    import asyncio as aio
    import shutil
    from app.models.person import InspirationPerson, Person
    from app.models.tag import InspirationTag, Tag, TagAlias
    from app.models.task import TaskQueue
    from app.models.scraper import ScraperSchedule, ScraperSeenURL, ScraperTask

    # 取消所有进行中的分析任务，避免删除数据后任务写回脏数据
    if _analysis_tasks:
        logger.info(f"取消 {len(_analysis_tasks)} 个进行中的分析任务...")
        for t in list(_analysis_tasks):
            t.cancel()
        _active_analyses.clear()
        await aio.sleep(1)  # 给任务 1 秒处理取消

    async with async_session() as db:
        # 按外键依赖顺序删除（先删子表，再删主表）。
        # audit_logs 刻意保留：审计日志的意义是留痕，本次重置动作本身也会记入。
        tables_in_order = [
            (InspirationTag, "inspiration_tags"),
            (AIAnalysisLog, "ai_analysis_log"),
            (InspirationPerson, "inspiration_persons"),
            (ScraperTask, "scraper_tasks"),
            (Inspiration, "inspirations"),
            (Person, "persons"),
            (TagAlias, "tag_aliases"),
            (Tag, "tags"),
            (ScraperSeenURL, "scraper_seen_urls"),  # 墓碑表：重置后不应再跳过旧 URL
            (ScraperSchedule, "scraper_schedules"),  # 定时计划：不清空则重置后自动复活采集
            (TaskQueue, "task_queue"),  # 队列：不清空则重置后残留任务继续执行
        ]
        deleted_counts = {}
        for table_model, table_name in tables_in_order:
            result = await db.execute(delete(table_model))
            deleted_counts[table_name] = result.rowcount
        await db.commit()

    # 丢弃缓存的向量连接，并清空向量库目录（避免重置后残留孤儿向量）
    from app.services.vector import store as vector_store

    vector_store.reset_connection()
    if settings.lancedb_dir.exists():
        try:
            await aio.to_thread(shutil.rmtree, settings.lancedb_dir)
            logger.info(f"已清空向量库目录: {settings.lancedb_dir}")
        except Exception as e:
            logger.warning(f"向量库目录删除失败: {settings.lancedb_dir} — {e}")

    # 清空存储目录（threadpool 异步执行，避免阻塞）
    storage_deleted = 0
    storage_errors = []
    for dir_path in [settings.images_dir, settings.thumbnails_dir, settings.videos_dir]:
        if dir_path.exists():
            file_count = len(list(dir_path.iterdir()))

            def _rmtree(p=dir_path) -> None:
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
        f"⚠ 数据已全部重置！数据库: {deleted_counts}, 文件: {storage_deleted} 个"
    )
    return {
        "message": result_msg,
        "database": deleted_counts,
        "files_deleted": storage_deleted,
        "storage_errors": storage_errors if storage_errors else None,
    }
