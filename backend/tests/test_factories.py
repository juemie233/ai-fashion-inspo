"""测试数据工厂自身的冒烟：默认值必须能落库，避免工厂字段漂移（2026-11-04 P2）。"""

from __future__ import annotations

from sqlalchemy import select

from app.database import async_session
from app.models.person import Blogger
from app.models.task import TaskQueue
from factories import create_blogger, create_inspiration, create_task


async def test_factories_create_minimal_rows(client):
    async with async_session() as db:
        insp = await create_inspiration(db, source_type="douyin")
        blogger = await create_blogger(db, platform_user_id="factory-sec")
        task = await create_task(db, type="deduplicate")

    assert insp.id and insp.file_path
    assert blogger.id and blogger.name
    assert task.id and task.status == "success"

    async with async_session() as db:
        assert (await db.execute(select(Blogger).where(Blogger.id == blogger.id))).scalar_one()
        assert (await db.execute(select(TaskQueue).where(TaskQueue.id == task.id))).scalar_one()
