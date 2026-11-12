"""灵感素材标签关联：单条/批量打标、解除标签。"""

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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
    strict_category: bool = False,
) -> dict:
    """批量给多个素材关联标签（按名称查找或创建，已关联的自动跳过）。

    仅对实际新增了标签的素材登记文本向量重建（攒批队列执行），避免无谓调用 Ollama。
    """
    # 去重（保留顺序），避免重复 ID/名称虚增统计与重复查询
    inspiration_ids = list(dict.fromkeys(inspiration_ids))
    raw_names = [n.strip() for n in names if n.strip()]

    if not raw_names:
        raise HTTPException(status_code=400, detail="请提供有效的标签名称")

    # 先解析标签（批量 get_or_create，避免每个素材重复查询同名标签）
    tags = []
    for name in raw_names:
        tags.append(await get_or_create_tag(db, name, category, source))

    # 再按 tag_id 去重：上面的去重发生在**归一化之前**，"JK" 与 "JK "（尾部空格）
    # 会落到同一个标签，若不去重，同一素材会插两条相同关联并撞
    # UNIQUE(inspiration_id, tag_id)——2026-10-20 由服务层用例发现（此前会 500）。
    tag_ids = list(dict.fromkeys(t.id for t in tags))

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
    # 用一条 SQLite ``INSERT OR IGNORE`` 批量写入，替代「每条关联一次 SAVEPOINT +
    # flush」——500 素材 × 5 标签会产生 2500 次 savepoint/flush，是批量打标慢的
    # 主因。UNIQUE(inspiration_id, tag_id) 自带幂等，并发时由 OR IGNORE 兜底。
    new_pairs = [
        (inspiration_id, tag_id)
        for inspiration_id in inspiration_ids
        if inspiration_id in found_ids
        for tag_id in tag_ids
        if (inspiration_id, tag_id) not in existing_pairs
    ]
    affected_ids: list[str] = []
    total_added = 0
    if new_pairs:
        # 分块写入：一次 values(...) 过大时 SQLite 会撞变量数上限；
        # 500 行/批在「往返次数」与「SQL 体积」之间平衡。
        for start in range(0, len(new_pairs), 500):
            chunk = new_pairs[start:start + 500]
            result = await db.execute(
                sqlite_insert(InspirationTag)
                .values(
                    [
                        {
                            "inspiration_id": inspiration_id,
                            "tag_id": tag_id,
                            "confidence": 1.0,
                            "source": "manual",
                        }
                        for inspiration_id, tag_id in chunk
                    ]
                )
                .on_conflict_do_nothing()
            )
            inserted = result.rowcount
            if inserted is None or inserted < 0:  # 驱动不支持 rowcount 时退回尝试数
                inserted = len(chunk)
            total_added += inserted
        affected_set = {inspiration_id for inspiration_id, _ in new_pairs}
        affected_ids = [
            inspiration_id
            for inspiration_id in inspiration_ids
            if inspiration_id in affected_set
        ]
    skipped_existing = len(existing_pairs)

    await db.commit()

    # 标签变更后的文本向量重建走攒批队列（与 tag_service 的合并/删除/重命名
    # 路径一致）：批量打标可能涉及上百个素材，逐个同步重建会串行调用上百次
    # Ollama embedding 导致请求挂起。素材登记进待回填表，累计达阈值后由
    # worker 统一创建批量任务执行。
    if affected_ids:
        from app.services.task_runners.vector_backfill import enqueue_vector_backfills

        await enqueue_vector_backfills(db, affected_ids)
        await db.commit()

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
    strict_category: bool = False,
) -> dict:
    """手动给素材关联标签（按名称查找或创建，已关联的自动跳过）。

    Args:
        strict_category: 严格类别模式。为 True 时，若同名标签已存在但类别与 ``category``
            不同，**不建立关联**（只回报 ``category_mismatch``）。用于「穿搭大标签」这类
            需要类别精确的入口——否则会把一个身体部位标签当大标签静默挂上，用户以为没生效。
            AI 分析等批量打标保持 False（宁可关联上也不丢标签）。
    """
    from app.services.vector_service import rebuild_text_vector

    inspiration = await db.get(Inspiration, inspiration_id)
    if not inspiration:
        raise HTTPException(status_code=404, detail="素材未找到")

    if not isinstance(names, list) or not names:
        raise HTTPException(status_code=400, detail="请提供标签名称列表")

    added = []
    # 同名标签已存在但类别与本次请求不同：只复用、**不改类别**（改类别会把这个标签从原
    # 小标签体系里移走，影响其它已用到它的素材）。这种情况显式回报给前端提示，避免
    # 「提示已添加、但大标签区看不到」的静默失败。
    category_mismatch: list[dict] = []
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
            # 已关联过：仍要检查类别，否则用户完全收不到任何反馈
            if tag.category != category:
                category_mismatch.append(
                    {
                        "id": tag.id,
                        "name": tag.name,
                        "category": tag.category,
                        "requested": category,
                        "already_linked": True,
                    }
                )
            continue
        mismatched = tag.category != category
        if mismatched:
            category_mismatch.append(
                {
                    "id": tag.id,
                    "name": tag.name,
                    "category": tag.category,
                    "requested": category,
                    "already_linked": False,
                }
            )
            if strict_category:
                # 严格模式（穿搭大标签）：类别不符就不建链，避免「挂成别的类别」的隐性错关联
                continue
        db.add(
            InspirationTag(
                inspiration_id=inspiration_id, tag_id=tag.id, confidence=1.0,
                source="manual",
            )
        )
        added.append({"id": tag.id, "name": tag.name, "category": tag.category})

    await db.commit()

    # 标签变更后重建文本向量，保持语义搜索结果最新（LanceDB/Ollama 不可用时静默降级）
    await rebuild_text_vector(db, inspiration_id)

    return {"added": added, "count": len(added), "category_mismatch": category_mismatch}


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
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到该标签关联")
    await db.commit()

    # 标签变更后重建文本向量，保持语义搜索结果最新（LanceDB/Ollama 不可用时静默降级）
    await rebuild_text_vector(db, inspiration_id)
    return {"removed": 1}
