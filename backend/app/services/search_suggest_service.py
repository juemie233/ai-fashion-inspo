"""搜索建议与标签共现（两个轻量只读查询，从 routers/search.py 下沉，C-3）。

都属于「点一下就返回」的辅助查询：前者给搜索框自动补全，后者给标签详情页的「常一起出现」。
与语义检索主链路（向量 / 相似）无关，故单独成模块，避免 search.py 继续膨胀。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import InspirationTag, Tag


async def search_suggestions(
    db: AsyncSession,
    q: str,
    limit: int = 8,
) -> list[dict]:
    """根据输入前缀返回匹配的标签名建议（用于搜索框自动补全）。"""
    result = await db.execute(
        select(Tag.name, func.count(InspirationTag.inspiration_id).label("cnt"))
        .outerjoin(InspirationTag, Tag.id == InspirationTag.tag_id)
        .where(Tag.name.contains(q))
        .group_by(Tag.name)
        .order_by(func.count(InspirationTag.inspiration_id).desc())
        .limit(limit)
    )
    return [
        {"name": row[0], "usage_count": row[1]}
        for row in result.all()
    ]


async def tag_cooccurrence(
    db: AsyncSession,
    tag_name: str,
    limit: int = 10,
) -> dict:
    """获取与指定标签经常一同出现的其他标签（共现分析）。"""
    tag_result = await db.execute(select(Tag.id).where(Tag.name == tag_name))
    tag_row = tag_result.first()
    if not tag_row:
        return {"tag": tag_name, "related": []}

    tag_id = tag_row[0]

    # 查找与 tag_name 共享素材最多的其他标签
    related = await db.execute(
        select(
            Tag.name,
            Tag.category,
            func.count(InspirationTag.inspiration_id).label("shared_count"),
        )
        .join(InspirationTag, Tag.id == InspirationTag.tag_id)
        .where(
            InspirationTag.inspiration_id.in_(
                select(InspirationTag.inspiration_id).where(
                    InspirationTag.tag_id == tag_id
                )
            ),
            Tag.id != tag_id,
        )
        .group_by(Tag.name, Tag.category)
        .order_by(func.count(InspirationTag.inspiration_id).desc())
        .limit(limit)
    )
    return {
        "tag": tag_name,
        "related": [
            {"name": row[0], "category": row[1], "shared_count": row[2]}
            for row in related.all()
        ],
    }
