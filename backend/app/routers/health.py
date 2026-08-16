"""服务健康检查路由：后端/前端/worker 状态 + 资源占用 + 告警。

与 main.py 中的基础 ``/api/health`` 互补：
- ``/api/health``        基础存活 + schema 版本握手（前端启动时比对）
- ``/api/health/services``  服务守护与监控的完整健康状态（管理页/脚本使用）
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import health_service

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/services")
async def services_health(db: AsyncSession = Depends(get_db)):
    """返回各服务（后端/前端/worker）健康状态、资源占用与告警列表。"""
    return await health_service.collect_health(db)
