"""质量审核「误判反馈」服务（审查 P0：误判反馈闭环）。

把「AI 判错了」这件事落成结构化记录，并顺手把素材状态改正：

- 记录原判定 / 纠正结果 / 错在哪类（复用 quality_reason_code 枚举）/ 备注；
- 素材状态同步改过来，并置 `quality_reviewed_by='human'` —— 随机复审从此不会覆盖它；
- 纠正是「人工判定」，因此与翻案走同一套保护，不另开一条状态通道。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration, QualityCorrection
from app.services.ai_service.quality import REASON_CODES

VALID_STATUS = ("approved", "rejected")


async def record_correction(
    db: AsyncSession,
    inspiration_id: str,
    corrected_status: str,
    reason_code: str | None = None,
    note: str | None = None,
) -> dict:
    """记录一次误判纠正并同步素材状态。

    Args:
        db: 数据库会话
        inspiration_id: 素材 UUID
        corrected_status: 人工认为正确的判定（approved / rejected）
        reason_code: 错在哪一类（可选，复用 quality_reason_code 枚举）
        note: 自由备注（可选）

    Returns:
        记录详情（含原判定）

    Raises:
        LookupError: 素材不存在
        ValueError: corrected_status 非法
    """
    if corrected_status not in VALID_STATUS:
        raise ValueError("corrected_status 只能是 approved 或 rejected")
    if reason_code is not None and reason_code not in REASON_CODES:
        # 白名单校验：否则任意字符串会写进 quality_reason_code，前端「按原因筛选」永远匹配不上
        raise ValueError(f"reason_code 不合法：{reason_code}")

    inspiration = await db.get(Inspiration, inspiration_id)
    if not inspiration:
        raise LookupError("素材未找到")

    original_status = inspiration.quality_status
    correction = QualityCorrection(
        inspiration_id=inspiration_id,
        original_status=original_status,
        corrected_status=corrected_status,
        reason_code=reason_code,
        note=note,
    )
    db.add(correction)

    # 同步状态：这是人工判定，必须置 human（随机复审不得覆盖）
    inspiration.quality_status = corrected_status
    inspiration.quality_reviewed_by = "human"
    if corrected_status == "approved":
        # 通过时同时清掉「原因码」与「原因文本」，避免「已通过却带着拒绝原因」的矛盾展示
        inspiration.quality_reason_code = None
        inspiration.quality_reason = None

    await db.commit()
    await db.refresh(correction)
    return {
        "id": correction.id,
        "inspiration_id": inspiration_id,
        "original_status": original_status,
        "corrected_status": corrected_status,
        "reason_code": reason_code,
        "note": note,
    }


async def list_corrections(
    db: AsyncSession, page: int = 1, size: int = 50
) -> tuple[list[dict], int]:
    """分页列出误判反馈（按时间倒序）。"""
    total = (
        await db.execute(select(func.count(QualityCorrection.id)))
    ).scalar() or 0
    rows = (
        await db.execute(
            select(QualityCorrection)
            .order_by(QualityCorrection.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).scalars().all()
    items = [
        {
            "id": r.id,
            "inspiration_id": r.inspiration_id,
            "original_status": r.original_status,
            "corrected_status": r.corrected_status,
            "reason_code": r.reason_code,
            "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return items, total


async def correction_stats(db: AsyncSession) -> dict:
    """误判反馈统计：按错误类型与纠正方向分组，供质量看板展示。"""
    by_code = (
        await db.execute(
            select(QualityCorrection.reason_code, func.count(QualityCorrection.id)).group_by(
                QualityCorrection.reason_code
            )
        )
    ).all()
    by_direction = (
        await db.execute(
            select(
                QualityCorrection.original_status,
                QualityCorrection.corrected_status,
                func.count(QualityCorrection.id),
            ).group_by(QualityCorrection.original_status, QualityCorrection.corrected_status)
        )
    ).all()
    return {
        "total": sum(c for _k, c in by_code),
        "by_reason_code": {(k or "unspecified"): c for k, c in by_code},
        "by_direction": {f"{o}->{c}": n for o, c, n in by_direction},
    }
