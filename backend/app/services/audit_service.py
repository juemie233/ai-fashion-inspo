"""操作审计日志服务：记录破坏性操作的统一入口。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def record_audit_log(
    db: AsyncSession,
    *,
    action: str,
    target_type: str = "inspirations",
    count: int = 0,
    freed_bytes: int = 0,
    detail: str | None = None,
) -> None:
    """写入一条审计记录并立即提交（不依赖外层事务，失败不影响主流程语义）。

    调用方在破坏性操作完成后调用本函数；审计写入失败时抛异常由调用方兜底，
    但通常不应因审计失败而回滚已完成的业务操作。
    """
    db.add(
        AuditLog(
            action=action,
            target_type=target_type,
            count=count,
            freed_bytes=freed_bytes,
            detail=detail,
        )
    )
    await db.commit()
