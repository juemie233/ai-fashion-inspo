"""后端测试数据工厂（2026-11-04 测试体系评测 P2）。

把「素材 / 博主 / 任务」的建数据默认值收敛到一处，减少各测试文件手写
`Inspiration(...)` / `TaskQueue(...)` 的重复与字段漂移。只接受调用方传入的
`AsyncSession`，不在工厂内开新会话（与项目「长事务不另开会话」的约定一致）。

用法::

    async with async_session() as db:
        insp = await create_inspiration(db, source_type="douyin")
        task = await create_task(db, type="deduplicate", status="success")
"""

from __future__ import annotations

from typing import Any

from app.models.person import Blogger
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue


async def create_inspiration(db, **overrides: Any) -> Inspiration:
    """创建一条素材；默认值只保证必填字段合法，不写任何真实文件。"""
    values: dict[str, Any] = {
        "source_type": "manual_upload",
        "file_path": "images/factory/test.jpg",
        "media_type": "image",
    }
    values.update(overrides)
    insp = Inspiration(**values)
    db.add(insp)
    await db.commit()
    await db.refresh(insp)
    return insp


async def create_blogger(db, **overrides: Any) -> Blogger:
    """创建一个抖音博主（最小必填：name/platform）。"""
    values: dict[str, Any] = {
        "name": "工厂博主",
        "platform": "douyin",
    }
    values.update(overrides)
    blogger = Blogger(**values)
    db.add(blogger)
    await db.commit()
    await db.refresh(blogger)
    return blogger


async def create_task(db, **overrides: Any) -> TaskQueue:
    """创建一个任务队列条目（默认 success/100，避免 worker 真的认领）。"""
    values: dict[str, Any] = {
        "type": "deduplicate",
        "status": "success",
        "progress": 100,
        "total": 0,
        "done": 0,
    }
    values.update(overrides)
    task = TaskQueue(**values)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task
