"""人物简介生成的路由共用逻辑。

博主与模特是两张独立表/路由，但生成简介的流程完全一致：取人物 → 拉风格画像 →
调用模型 → 返回文本。这里提供一个共享函数，两个路由都用它，避免两份重复实现。
"""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.person.base import PersonNotFoundError, PersonServiceBase
from app.services.person_bio import BioGenerationError, PersonBioInputs, generate_person_bio

# 平台 → 中文标签（与前端 PERSON_PLATFORM_LABELS 保持一致）
_PLATFORM_LABELS = {"xiaohongshu": "小红书", "douyin": "抖音", "other": "其他"}


async def generate_person_bio_endpoint(
    db: AsyncSession,
    service: PersonServiceBase,
    person_id: int,
) -> dict:
    """生成人物简介（不入库），供前端「AI 生成」按钮调用。"""
    try:
        person = await service.get(db, person_id)
    except PersonNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)

    style_profile = await service.style_profile(db, person_id)
    top_tags = style_profile.get("top_tags") or []

    # 标签不足时直接给出明确提示，避免模型在无依据时硬写
    if not top_tags:
        kind_label = service.label
        raise HTTPException(
            status_code=400,
            detail=f"该{kind_label}还没有任何标签，请先关联/采集带标签的素材后再生成简介",
        )

    inputs = PersonBioInputs(
        kind_label=service.label,
        name=person.name,
        platform_label=_PLATFORM_LABELS.get(person.platform, person.platform or "其他"),
        ip_location=person.ip_location,
        top_tags=top_tags,
        by_category=style_profile.get("by_category") or {},
    )

    try:
        bio = await generate_person_bio(inputs)
    except BioGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"bio": bio}
