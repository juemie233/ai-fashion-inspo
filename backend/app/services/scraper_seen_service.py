"""采集已见 URL 墓碑服务：防止被删除素材被采集器重复入库。

「墓碑」即 scraper_seen_urls 表中的记录：素材被物理删除（垃圾桶清空、
去重、批量删除等）时写入其来源 URL，采集器在去重检查时会跳过这些 URL。
此前该插入逻辑在 inspiration_service / scraper_service / task_runners
等 6 处重复实现，现统一收敛到本模块。
"""

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scraper import ScraperSeenURL


async def seal_urls(db: AsyncSession, urls: list[str]) -> None:
    """批量写入已见 URL 墓碑（幂等：重复 URL 自动忽略）。

    参数:
        db: 数据库会话（调用方负责事务边界，本函数只追加 INSERT）
        urls: 来源 URL 列表（空串 / None 会被跳过）
    """
    for url in urls:
        if not url:
            continue
        await db.execute(
            sqlite_insert(ScraperSeenURL)
            .values(source_url=url)
            .prefix_with("OR IGNORE")
        )
