"""任务执行器（task_runners）回归测试：批量删除任务的创建与执行。"""

from sqlalchemy import func, select

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.services.task_runners.batch_delete import (
    create_batch_delete_task,
    execute_batch_delete,
)


async def test_execute_batch_delete_deletes_records_and_files(client, upload):
    """批量删除：删除数据库记录 + 物理删除文件 + 释放空间统计。"""
    a = upload().json()["id"]
    b = upload().json()["id"]

    async with async_session() as db:
        # 记录删除前的文件路径，用于删除后校验物理文件确实消失
        rows = (await db.execute(
            select(Inspiration.file_path, Inspiration.thumbnail_path)
            .where(Inspiration.id.in_([a, b]))
        )).all()
        paths = [settings.storage_root / p for row in rows for p in row if p]
        assert paths and all(p.exists() for p in paths)  # 上传确实落盘

        task = await create_batch_delete_task(db, [a, b], label="ids")
        assert task.total == 2

        await execute_batch_delete(db, task)

        assert task.result["deleted_count"] == 2
        assert task.result["freed_bytes"] > 0
        assert task.done == 2
        assert task.progress == 100

        remaining = await db.scalar(select(func.count(Inspiration.id)))
        assert remaining == 0

    # 删除后：这些文件已从磁盘物理删除
    assert all(not p.exists() for p in paths)


async def test_execute_batch_delete_empty_ids(client):
    """空 ID 列表：任务秒完成，不删任何记录。"""
    async with async_session() as db:
        task = await create_batch_delete_task(db, [], label="ids")
        await execute_batch_delete(db, task)

        assert task.done == 0
        assert task.progress == 100
        assert task.result["deleted_count"] == 0
