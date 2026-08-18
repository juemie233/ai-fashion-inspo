"""垃圾桶状态机：软删除三字段的单点写入、转移校验与不变量检查。

本模块是垃圾桶领域的**最底层**：只依赖数据模型与常量定义，
不依赖任何业务服务模块，供 inspiration_trash 及其余移入/恢复路径调用。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration, utcnow
from app.schemas.inspiration import TRASH_REASONS


def _resolve_trash_reason(reason: str | None, inspiration: Inspiration) -> str:
    """解析删除原因：显式传入的合法值优先；否则按素材状态自动推断。

    - 质量审核被拒绝（rejected）→ 「质量差」（垃圾桶素材全部作为负样本学习输入）
    - 其余 → 「不喜欢」
    """
    if reason in TRASH_REASONS:
        return reason
    return "质量差" if inspiration.quality_status == "rejected" else "不喜欢"


def _assert_trash_transition(
    inspiration: Inspiration,
    target: str,
    *,
    reason: str | None = None,
    source: str | None = None,
) -> None:
    """校验软删除状态转移合法性，非法转移抛 ValueError（内部防御，暴露代码缺陷）。

    合法转移:
        active → trashed：素材必须不在垃圾桶，且携带合法删除原因（TRASH_REASONS）
            与移入来源（manual/auto）
        trashed → active：素材必须在垃圾桶中（恢复需清空三字段由调用方负责）

    其余任何状态组合都视为代码缺陷——立即抛错而非静默留下半状态
    （如「已删除但缺原因」「未删除却残留来源」这类隐形 bug 的温床）。
    """
    if target == "trashed":
        if inspiration.deleted_at is not None:
            raise ValueError(f"素材 {inspiration.id} 已在垃圾桶中，非法重复移入")
        if reason not in TRASH_REASONS:
            raise ValueError(f"素材 {inspiration.id} 移入垃圾桶缺少合法删除原因: {reason!r}")
        if source not in ("manual", "auto"):
            raise ValueError(f"素材 {inspiration.id} 移入来源非法: {source!r}")
    elif target == "active":
        if inspiration.deleted_at is None:
            raise ValueError(f"素材 {inspiration.id} 不在垃圾桶中，非法恢复")
    else:
        raise ValueError(f"未知状态转移目标: {target!r}")


def _mark_trashed(inspiration: Inspiration, reason: str, source: str) -> None:
    """将素材标记为垃圾桶状态：软删除三字段单点写入（含合法性断言）。

    所有移入垃圾桶的路径（手动/批量/质量审核自动/疑似 AI 自动）必须经由此函数，
    杜绝散落字段赋值导致的「三字段不同步」。
    """
    _assert_trash_transition(inspiration, "trashed", reason=reason, source=source)
    inspiration.deleted_at = utcnow()
    inspiration.trash_reason = reason
    inspiration.trash_source = source


def _mark_restored(inspiration: Inspiration) -> None:
    """清除素材软删除标记：三字段单点清除（含合法性断言）。"""
    _assert_trash_transition(inspiration, "active")
    inspiration.deleted_at = None
    inspiration.trash_reason = None
    inspiration.trash_source = None


async def verify_trash_invariants(db: AsyncSession) -> list[dict]:
    """扫描全库校验垃圾桶状态不变量，返回违规清单（空列表 = 健康）。

    规则（软删除三字段必须同真同假，杜绝半状态）：
        R1: deleted_at 非空 ⇒ trash_reason 必须非空（缺原因无法按原因筛选与负样本统计）
        R2: deleted_at 非空 ⇒ trash_source 必须非空（manual/auto）
        R3: deleted_at 为空 ⇒ trash_reason / trash_source 必须为空（残留说明
            恢复/清理路径未清干净）

    返回值: [{"id": str, "rule": "R1|R2|R3", "detail": str}, ...]

    用途：测试断言 + 管理页完整性检查（integrity-check），让「新代码破坏旧约定」
    在测试/巡检时立刻暴露，而非等到线上数据烂掉。
    """
    rows = (
        await db.execute(
            select(
                Inspiration.id,
                Inspiration.deleted_at,
                Inspiration.trash_reason,
                Inspiration.trash_source,
            )
        )
    ).all()

    violations: list[dict] = []
    for rid, deleted_at, reason, source in rows:
        if deleted_at is not None:
            if not reason:
                violations.append(
                    {"id": rid, "rule": "R1", "detail": "垃圾桶素材缺少删除原因"}
                )
            if not source:
                violations.append(
                    {"id": rid, "rule": "R2", "detail": "垃圾桶素材缺少移入来源"}
                )
        else:
            if reason is not None:
                violations.append(
                    {"id": rid, "rule": "R3", "detail": f"未删除素材残留删除原因: {reason}"}
                )
            if source is not None:
                violations.append(
                    {"id": rid, "rule": "R3", "detail": f"未删除素材残留移入来源: {source}"}
                )
    return violations
