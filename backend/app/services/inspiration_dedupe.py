"""灵感素材去重与入库前校验：内容哈希去重、平台 ID 查重、墓碑检查、任务校验。

从 inspiration_service 的创建链路中抽取，供 inspiration_create 的
上传 / URL 导入两条路径复用，保证「先查重后落盘」语义一致。
"""

import asyncio

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import Inspiration, NOT_DELETED
from app.models.scraper import ScraperSeenURL, ScraperTask
from app.utils.file_hash import file_sha256


async def find_duplicate_by_hash(db: AsyncSession, content_hash: str) -> str | None:
    """按文件内容哈希查找重复素材，返回重复素材 ID（无重复返回 None）。

    优先走 content_hash 索引列（快路径）；存量素材尚未回填哈希（列为空）时，
    回退全量扫描磁盘文件，并顺手把哈希回填入库，一次扫描后后续全部走索引。
    """
    # 快路径：哈希列命中（垃圾桶素材视为「可重新入库」，不参与去重）
    result = await db.execute(
        select(Inspiration.id).where(
            Inspiration.content_hash == content_hash,
            NOT_DELETED,
        )
    )
    dup_id = result.scalars().first()
    if dup_id:
        return dup_id

    # 存量回退：库中仍有未回填哈希的行时才全量扫描（避免无谓磁盘 I/O）
    unfilled = (
        await db.execute(
            select(func.count(Inspiration.id)).where(
                Inspiration.content_hash.is_(None),
                Inspiration.file_path.isnot(None),
                NOT_DELETED,
            )
        )
    ).scalar() or 0
    if not unfilled:
        return None

    storage_root = settings.storage_root
    result = await db.execute(
        select(Inspiration.id, Inspiration.file_path).where(
            Inspiration.content_hash.is_(None),
            Inspiration.file_path.isnot(None),
            NOT_DELETED,
        )
    )
    for insp_id, fpath in result.all():
        # 全库磁盘哈希是阻塞 I/O，放线程池执行，避免卡住事件循环
        h = (
            await asyncio.to_thread(file_sha256, storage_root / fpath)
            if fpath
            else None
        )
        if h:
            # 回填哈希（含命中场景），随外层事务一并提交
            await db.execute(
                update(Inspiration)
                .where(Inspiration.id == insp_id)
                .values(content_hash=h)
            )
            if h == content_hash:
                return insp_id
    return None


async def check_platform_id_duplicate(
    db: AsyncSession, source_platform_id: str | None
) -> None:
    """平台 ID 查重：已存在未删除素材时抛 409。

    仅统计未删除素材：垃圾桶素材释放平台 ID，允许「删除后重新采集」
    （与 content_hash 去重、部分唯一索引的语义一致）。
    """
    if not source_platform_id:
        return
    result = await db.execute(
        select(Inspiration).where(
            Inspiration.source_platform_id == source_platform_id,
            NOT_DELETED,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"平台ID '{source_platform_id}' 的素材已存在",
        )


async def check_tombstone(db: AsyncSession, urls: list[str]) -> None:
    """墓碑检查：来源 URL 曾被删除过（如采集结果删除），命中即抛 409 不再重新入库。"""
    check_urls = [u for u in urls if u]
    if not check_urls:
        return
    seen = await db.execute(
        select(ScraperSeenURL.source_url).where(
            ScraperSeenURL.source_url.in_(check_urls)
        )
    )
    if seen.scalars().first():
        raise HTTPException(
            status_code=409,
            detail="该素材来源 URL 已存在墓碑记录（此前被删除），不会重新入库",
        )


async def check_scraper_task_exists(
    db: AsyncSession, scraper_task_id: int | None
) -> None:
    """关联采集任务校验：插件采集链路传 task_id，避免产生指向不存在任务的孤儿记录。"""
    if scraper_task_id is None:
        return
    task = await db.get(ScraperTask, scraper_task_id)
    if not task:
        raise HTTPException(status_code=400, detail="关联的采集任务不存在")
