"""AI 子路由。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.inspiration import Inspiration

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/outfit-tags/suggest")
async def suggest_outfit_tags(
    inspiration_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, list[str] | str | int]:
    """根据素材的小标签，AI 建议穿搭大标签（只建议，不入库）。"""
    from app.models.tag import InspirationTag, Tag

    inspiration = await db.get(Inspiration, inspiration_id)
    if not inspiration:
        raise HTTPException(status_code=404, detail="素材未找到")

    # 取出素材的现有小标签名称
    result = await db.execute(
        select(Tag.name)
        .join(InspirationTag, InspirationTag.tag_id == Tag.id)
        .where(InspirationTag.inspiration_id == inspiration_id)
    )
    small_tags = [row[0] for row in result]

    if not small_tags:
        return {"suggestions": [], "small_tags": [], "message": "素材暂无小标签，无法总结大标签"}

    from app.services.ai_service import summarize_outfit_tags

    suggestions = await summarize_outfit_tags(small_tags)

    return {
        "suggestions": suggestions,
        "small_tags": small_tags,
        "count": len(suggestions),
    }
