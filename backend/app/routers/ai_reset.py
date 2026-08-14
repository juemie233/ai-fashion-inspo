"""AI 子路由。"""

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    analysis_log_filter as _analysis_log_filter,
)
from app.routers.ai_shared import (
    _analysis_semaphore,
    _active_analyses,
    _analysis_tasks,
    _task_by_id,
    _pending_queue,
    _queue_paused,
    _run_analysis,
    _update_env_file,
    _fmt_utc,
    _format_size,
)
from app.services.model_config import get_model_config, update_model_config
from app.utils.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


# ============ 数据重置 ============


@router.delete("/reset")
async def reset_all_data(
    confirm: str = Query("no", description="输入 'yes' 二次确认删除所有数据"),
    _api_key: str = Depends(require_api_key),
):
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
    from app.models.tag import InspirationTag, Tag
    from app.models.scraper import ScraperTask

    # 取消所有进行中的分析任务，避免删除数据后任务写回脏数据
    if _analysis_tasks:
        logger.info(f"取消 {len(_analysis_tasks)} 个进行中的分析任务...")
        for t in list(_analysis_tasks):
            t.cancel()
        _active_analyses.clear()
        await aio.sleep(1)  # 给任务 1 秒处理取消

    async with async_session() as db:
        # 按外键依赖顺序删除（先删子表，再删主表）
        tables_in_order = [
            (InspirationTag, "inspiration_tags"),
            (AIAnalysisLog, "ai_analysis_log"),
            (ScraperTask, "scraper_tasks"),
            (Inspiration, "inspirations"),
            (Tag, "tags"),
        ]
        deleted_counts = {}
        for table_model, table_name in tables_in_order:
            result = await db.execute(delete(table_model))
            deleted_counts[table_name] = result.rowcount
        await db.commit()

    # 清空存储目录（threadpool 异步执行，避免阻塞）
    storage_deleted = 0
    storage_errors = []
    for dir_path in [settings.images_dir, settings.thumbnails_dir, settings.videos_dir]:
        if dir_path.exists():
            file_count = len(list(dir_path.iterdir()))

            def _rmtree(p=dir_path):
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
