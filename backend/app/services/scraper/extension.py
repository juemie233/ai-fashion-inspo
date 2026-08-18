"""浏览器插件采集会话任务记录：创建会话、汇总计数并标记完成。"""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scraper import ScraperTask
from app.utils.time import utcnow


async def create_extension_task(db: AsyncSession, payload: dict) -> ScraperTask:
    """为浏览器插件的一次采集会话创建任务记录（running 状态）。

    插件在批量上传图片前调用，获得 task_id 后随每次上传附带；
    上传结束后调用 complete_extension_task 汇总计数并标记完成。
    """
    config = {
        "mode": "extension",
        "source_url": payload.get("source_url"),
        "origin_platform": payload.get("platform") or "browser_extension",
    }
    task = ScraperTask(
        platform="browser_extension",
        status="running",
        config=json.dumps(config, ensure_ascii=False),
        started_at=utcnow(),
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    await db.commit()
    return task


async def complete_extension_task(db: AsyncSession, task_id: int, payload: dict) -> dict:
    """汇总浏览器插件采集会话的发现/入库数量并标记任务完成。"""
    task = await db.get(ScraperTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.platform != "browser_extension":
        raise HTTPException(status_code=400, detail="仅浏览器插件任务可使用此接口")

    items_found = int(payload.get("items_found") or 0)
    items_added = int(payload.get("items_added") or 0)
    skipped = max(0, items_found - items_added)

    task.status = "completed"
    task.items_found = items_found
    task.items_added = items_added
    task.finished_at = utcnow()
    # 组装最小漏斗：与手动采集任务的漏斗结构对齐，前端漏斗弹窗可直接展示
    task.diagnostics = json.dumps({
        "per_search": [{
            "keyword": payload.get("source_url") or "插件采集",
            "sort_type": "extension",
            "batch_added": items_added,
            "batch_skipped_existing": skipped,
        }],
        "summary": {
            "total_found": items_found,
            "skipped_url_seen": 0,
            "skipped_content_dup": 0,
            "skipped_http_error": 0,
            "skipped_network_error": skipped,
            "total_added": items_added,
        },
    }, ensure_ascii=False)
    if items_added == 0 and items_found > 0:
        task.error = "全部图片上传失败（可能内容重复或后端异常）"
    await db.commit()
    return {"message": "已记录插件采集", "task_id": task_id, "items_added": items_added}
