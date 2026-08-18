"""灵感素材标签关联：单条/批量打标、解除标签。"""

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag
from app.services.tag_service import get_or_create_tag


async def batch_add_tags(
    db: AsyncSession,
    inspiration_ids: list[str],
    names: list[str],
    category: str = "free",
    source: str = "manual",
) -> dict:
    """批量给多个素材关联标签（按名称查找或创建，已关联的自动跳过）。

    仅对实际新增了标签的素材重建文本向量，避免无谓调用 Ollama。
    """
    from app.services.vector_service import rebuild_text_vector

    # 去重（保留顺序），避免重复 ID/名称虚增统计与重复查询
    inspiration_ids = list(dict.fromkeys(inspiration_ids))
    raw_names = [n.strip() for n in names if n.strip()]

    if not raw_names:
        raise HTTPException(status_code=400, detail="请提供有效的标签名称")

    # 先解析标签（批量 get_or_create，避免每个素材重复查询同名标签）
    tags = []
    for name in raw_names:
        tags.append(await get_or_create_tag(db, name, category, source))

    tag_ids = [t.id for t in tags]

    # 一次性校验素材存在性，避免逐个 db.get
    found_result = await db.execute(
        select(Inspiration.id).where(Inspiration.id.in_(inspiration_ids))
    )
    found_ids = set(found_result.scalars().all())
    not_found_ids = [i for i in inspiration_ids if i not in found_ids]

    # 一次性查出已存在的关联，避免 M×N 逐条查询
    existing_result = await db.execute(
        select(InspirationTag.inspiration_id, InspirationTag.tag_id).where(
            InspirationTag.inspiration_id.in_(inspiration_ids),
            InspirationTag.tag_id.in_(tag_ids),
        )
    )
    existing_pairs = {(r[0], r[1]) for r in existing_result.all()}

    # 批量插入新关联，跳过已存在的；记录实际变更的素材。
    # 逐条用 SAVEPOINT flush 并捕获 IntegrityError：并发请求插入同一关联时，
    # 仅回滚该条 SAVEPOINT 而非整个事务，避免 500。
    total_added = 0
    affected_ids: list[str] = []
    skipped_existing = 0
    for inspiration_id in inspiration_ids:
        if inspiration_id not in found_ids:
            continue
        added_for_this = 0
        for tag_id in tag_ids:
            if (inspiration_id, tag_id) in existing_pairs:
                skipped_existing += 1
                continue
            link = InspirationTag(
                inspiration_id=inspiration_id, tag_id=tag_id, confidence=1.0
            )
            db.add(link)
            try:
                async with db.begin_nested():
                    await db.flush()
            except IntegrityError:
                # 并发下同一关联已被其他请求插入：回滚 SAVEPOINT，移除失败对象后跳过
                db.expunge(link)
                skipped_existing += 1
                continue
            added_for_this += 1
        if added_for_this:
            affected_ids.append(inspiration_id)
            total_added += added_for_this

    await db.commit()

    # 标签变更后重建文本向量，保持语义搜索结果最新（LanceDB/Ollama 不可用时静默降级）
    for inspiration_id in affected_ids:
        await rebuild_text_vector(db, inspiration_id)

    return {
        "added": total_added,
        "affected": len(affected_ids),
        # 向后兼容：skipped 仍为「未实际变更的素材数」（含不存在与已全部关联）
        "skipped": len(inspiration_ids) - len(affected_ids),
        # 明确拆分两个跳过维度
        "not_found": len(not_found_ids),
        "skipped_existing": skipped_existing,
        "missing_ids": not_found_ids,
    }


async def add_inspiration_tags(
    db: AsyncSession,
    inspiration_id: str,
    names: list[str],
    category: str = "free",
    source: str = "manual",
) -> dict:
    """手动给素材关联标签（按名称查找或创建，已关联的自动跳过）。"""
    from app.services.vector_service import rebuild_text_vector

    inspiration = await db.get(Inspiration, inspiration_id)
    if not inspiration:
        raise HTTPException(status_code=404, detail="素材未找到")

    if not isinstance(names, list) or not names:
        raise HTTPException(status_code=400, detail="请提供标签名称列表")

    added = []
    for raw in names:
        name = str(raw).strip()
        if not name:
            continue
        tag = await get_or_create_tag(db, name, category, source)
        existing = await db.execute(
            select(InspirationTag).where(
                InspirationTag.inspiration_id == inspiration_id,
                InspirationTag.tag_id == tag.id,
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(
            InspirationTag(
                inspiration_id=inspiration_id, tag_id=tag.id, confidence=1.0
            )
        )
        added.append({"id": tag.id, "name": tag.name, "category": tag.category})

    await db.commit()

    # 标签变更后重建文本向量，保持语义搜索结果最新（LanceDB/Ollama 不可用时静默降级）
    await rebuild_text_vector(db, inspiration_id)

    return {"added": added, "count": len(added)}


async def remove_inspiration_tag(
    db: AsyncSession,
    inspiration_id: str,
    tag_id: int,
) -> dict:
    """解除素材与某个标签的关联（不删除标签本身）。"""
    from app.services.vector_service import rebuild_text_vector

    result = await db.execute(
        delete(InspirationTag).where(
            InspirationTag.inspiration_id == inspiration_id,
            InspirationTag.tag_id == tag_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到该标签关联")

    # 标签变更后重建文本向量，保持语义搜索结果最新（LanceDB/Ollama 不可用时静默降级）
    await rebuild_text_vector(db, inspiration_id)
    return {"removed": 1}
