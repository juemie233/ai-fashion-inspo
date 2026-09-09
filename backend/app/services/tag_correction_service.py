"""AI 打标纠错服务：记录「标错了」反馈，并按原因执行对应动作。

设计要点（见 AI 打标质量闭环 P1）：

- 反馈即动作：``multi``（AI 多标）删除该素材上该标签的关联；``missing``
  （AI 漏标）补建关联；``wrong_category`` / ``bad_name`` 仅记录不改动数据；
- 关联变更后复用既有文本向量重建链路（与手动打标一致，不可用时静默降级）；
- 记录时冗余当次分析的 ``log_id`` / ``prompt_version`` / ``model_name``，
  供提示词版本质量看板按版本聚合纠错率（避免聚合时 join 日志表）。
"""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import AIAnalysisLog, Inspiration, analysis_log_filter
from app.models.tag import InspirationTag, Tag
from app.models.tag_correction import TagCorrection
from app.services.tag_service import get_or_create_tag
from app.utils.time import utcnow

# 反馈原因 -> 执行动作（noted = 仅记录）
REASON_ACTIONS: dict[str, str] = {
    "multi": "removed",           # AI 多标 → 删除关联
    "missing": "added",           # AI 漏标 → 补建关联
    "wrong_category": "noted",    # 类别错 → 仅记录
    "bad_name": "noted",          # 名称不规范 → 仅记录
}

REASON_LABELS: dict[str, str] = {
    "multi": "AI 多标",
    "missing": "AI 漏标",
    "wrong_category": "类别错",
    "bad_name": "名称不规范",
}


async def _latest_analysis_log(
    db: AsyncSession, inspiration_id: str
) -> AIAnalysisLog | None:
    """取素材最近一次标签分析日志（无则 None）。"""
    result = await db.execute(
        select(AIAnalysisLog)
        .where(
            AIAnalysisLog.inspiration_id == inspiration_id,
            analysis_log_filter(),
        )
        .order_by(AIAnalysisLog.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def record_tag_correction(
    db: AsyncSession,
    inspiration_id: str,
    tag_name: str,
    reason: str,
    note: str | None = None,
    category: str = "free",
) -> dict:
    """记录一次打标纠错反馈，并按原因执行动作。

    参数:
        inspiration_id: 素材 ID（不存在时抛 404）
        tag_name: 被纠正的标签名（漏标场景为要补充的标签名）
        reason: multi / missing / wrong_category / bad_name
        note: 可选备注
        category: 漏标新建标签时的类别（已存在标签沿用原类别）

    返回:
        记录结果：recorded / reason / action / applied（是否真的改了关联）
        / tag_id / tag_name / category。
    """
    if reason not in REASON_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"未知反馈原因: {reason}（可选：{'/'.join(REASON_ACTIONS)}）",
        )

    name = (tag_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="标签名不能为空")

    inspiration = await db.get(Inspiration, inspiration_id)
    if inspiration is None:
        raise HTTPException(status_code=404, detail="素材未找到")

    action = REASON_ACTIONS[reason]
    tag: Tag | None = None
    applied = False
    final_category = category

    if reason == "missing":
        # 漏标：补建关联（已关联则只记录，不重复插入）
        tag = await get_or_create_tag(db, name, category, "manual")
        final_category = tag.category
        existing = await db.execute(
            select(InspirationTag).where(
                InspirationTag.inspiration_id == inspiration_id,
                InspirationTag.tag_id == tag.id,
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(
                InspirationTag(
                    inspiration_id=inspiration_id,
                    tag_id=tag.id,
                    confidence=1.0,
                    source="manual",
                )
            )
            applied = True
    else:
        # 多标/类别错/名称不规范：按名查已有标签（不创建新标签）
        found = await db.execute(select(Tag).where(Tag.name == name))
        tag = found.scalar_one_or_none()
        if tag is not None:
            final_category = tag.category
        if reason == "multi" and tag is not None:
            result = await db.execute(
                delete(InspirationTag).where(
                    InspirationTag.inspiration_id == inspiration_id,
                    InspirationTag.tag_id == tag.id,
                )
            )
            applied = (result.rowcount or 0) > 0

    log = await _latest_analysis_log(db, inspiration_id)
    db.add(
        TagCorrection(
            inspiration_id=inspiration_id,
            tag_id=tag.id if tag is not None else None,
            tag_name=name,
            category=final_category,
            reason=reason,
            action=action,
            note=(note or "").strip() or None,
            log_id=log.id if log is not None else None,
            prompt_version=log.prompt_version if log is not None else None,
            model_name=log.model_name if log is not None else None,
        )
    )
    await db.commit()

    # 关联真的变了才重建文本向量（与手动打标路径一致，不可用时静默降级）
    if applied:
        from app.services.vector_service import rebuild_text_vector

        await rebuild_text_vector(db, inspiration_id)

    return {
        "recorded": 1,
        "reason": reason,
        "action": action,
        "applied": applied,
        "tag_id": tag.id if tag is not None else None,
        "tag_name": name,
        "category": final_category,
    }


async def list_tag_corrections(
    db: AsyncSession,
    page: int = 1,
    size: int = 20,
    reason: str | None = None,
) -> dict:
    """分页查询纠错记录（按时间倒序，可按原因筛选）。"""
    query = select(TagCorrection)
    if reason:
        query = query.where(TagCorrection.reason == reason)
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(TagCorrection.id.desc()).offset((page - 1) * size).limit(size)
    rows = (await db.execute(query)).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "inspiration_id": row.inspiration_id,
                "tag_id": row.tag_id,
                "tag_name": row.tag_name,
                "category": row.category,
                "reason": row.reason,
                "reason_label": REASON_LABELS.get(row.reason, row.reason),
                "action": row.action,
                "note": row.note,
                "log_id": row.log_id,
                "prompt_version": row.prompt_version,
                "model_name": row.model_name,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "size": size,
    }


async def tag_correction_stats(db: AsyncSession, days: int = 30) -> dict:
    """纠错统计：按原因 / 动作 / 提示词版本聚合（供数据洞察页展示）。"""
    since = utcnow() - timedelta(days=days)

    by_reason_rows = (
        await db.execute(
            select(TagCorrection.reason, func.count())
            .where(TagCorrection.created_at >= since)
            .group_by(TagCorrection.reason)
        )
    ).all()
    by_action_rows = (
        await db.execute(
            select(TagCorrection.action, func.count())
            .where(TagCorrection.created_at >= since)
            .group_by(TagCorrection.action)
        )
    ).all()
    by_version_rows = (
        await db.execute(
            select(
                TagCorrection.prompt_version,
                TagCorrection.model_name,
                func.count().label("total"),
                func.sum(case((TagCorrection.action == "removed", 1), else_=0)).label("removed"),
                func.sum(case((TagCorrection.action == "added", 1), else_=0)).label("added"),
            )
            .where(TagCorrection.created_at >= since)
            .group_by(TagCorrection.prompt_version, TagCorrection.model_name)
            .order_by(func.count().desc())
            .limit(20)
        )
    ).all()

    total = sum(count for _reason, count in by_reason_rows)
    return {
        "days": days,
        "total": total,
        "by_reason": [
            {"reason": reason, "label": REASON_LABELS.get(reason, reason), "count": count}
            for reason, count in by_reason_rows
        ],
        "by_action": [{"action": action, "count": count} for action, count in by_action_rows],
        "by_prompt_version": [
            {
                "prompt_version": version,
                "model_name": model,
                "total": count,
                "removed": removed or 0,
                "added": added or 0,
            }
            for version, model, count, removed, added in by_version_rows
        ],
    }
