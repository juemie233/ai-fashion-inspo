"""操作审计日志服务：记录破坏性操作的统一入口。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.utils.time import format_utc
import logging

from app.database import async_session
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


async def record_audit_log(
    *,
    action: str,
    target_type: str = "inspirations",
    count: int = 0,
    freed_bytes: int = 0,
    detail: str | None = None,
) -> None:
    """写入一条审计记录（独立会话、独立事务，失败不影响主流程）。

    破坏性操作已完成后再调用本函数：审计落库失败仅记日志、不抛异常，避免
    让整个请求因此报错；同时使用独立会话，不共享调用方会话（防止把调用方
    尚未提交的内容一并提交/回滚）。
    """
    try:
        async with async_session() as db:
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
    except Exception as e:
        logger.warning(f"写入审计日志失败（忽略）: action={action} — {e}")

async def query_audit_logs(
    db: AsyncSession,
    limit: int = 50,
) -> list[dict]:
    """按时间倒序返回破坏性操作审计日志。"""
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "action": r.action,
            "target_type": r.target_type,
            "count": r.count,
            "freed_bytes": r.freed_bytes,
            "detail": r.detail,
            "created_at": format_utc(r.created_at),
        }
        for r in rows
    ]
