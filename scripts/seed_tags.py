"""独立脚本：手动导入/重置预设标签。"""

import asyncio
import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.database import async_session, init_db
from app.services.tag_service import seed_tags


async def main():
    """导入预设标签到数据库。"""
    print("正在初始化数据库...")
    await init_db()

    async with async_session() as db:
        added = await seed_tags(db)
        print(f"已导入 {added} 个新标签")

        # 检查已有标签数量
        from sqlalchemy import select, func
        from app.models.tag import Tag

        result = await db.execute(select(func.count()).select_from(Tag))
        total = result.scalar()
        print(f"数据库中标签总数: {total}")

    print("完成！")


if __name__ == "__main__":
    asyncio.run(main())
