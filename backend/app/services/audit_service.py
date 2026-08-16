"""操作审计日志服务：记录破坏性操作的统一入口。"""

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
