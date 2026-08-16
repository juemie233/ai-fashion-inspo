"""服务健康状态采集：探测前端 / 判断 worker 存活 / 资源占用 / 告警。

供「服务守护与监控」方案的健康检查端点使用：
- 后端自身：能响应本端点即视为健康；
- 前端：后端主动发起 HTTP 探测（dev server 端口 17777）；
- worker：读 service_heartbeats 表的最新心跳，超时未刷新视为已死；
- 资源：磁盘 / 内存 / 日志目录占用，超阈值生成告警。
"""

import time
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.service_heartbeat import ServiceHeartbeat
from app.utils.system_stats import disk_usage, memory_usage
from app.utils.time import utcnow

# 前端 dev server 端口（与 scripts/restart.sh、scripts/ensure-services.sh 约定一致）
_FRONTEND_PORT = 17777

# worker 心跳超时阈值（秒）：超过该时长无心跳视为 worker 已死
_WORKER_STALE_SECONDS = 30

# 资源告警阈值（百分比）
_DISK_ALERT_PERCENT = 90.0
_MEMORY_ALERT_PERCENT = 90.0


def _logs_dir() -> Path:
    """返回日志目录（storage/logs，采集器与其它组件统一写这里）。"""
    return settings.storage_root / "logs"


async def _probe_frontend() -> dict:
    """探测前端 dev server 是否可访问，返回状态与延迟。"""
    url = f"http://127.0.0.1:{_FRONTEND_PORT}/"
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
        latency_ms = round((time.perf_counter() - start) * 1000)
        return {"status": "ok", "latency_ms": latency_ms, "http_status": resp.status_code}
    except Exception as e:
        return {"status": "down", "latency_ms": None, "detail": str(e)[:120]}


async def _worker_status(db: AsyncSession) -> dict:
    """从 service_heartbeats 表读取 worker 存活状态。"""
    result = await db.execute(
        select(ServiceHeartbeat)
        .where(ServiceHeartbeat.service_type == "worker")
        .order_by(ServiceHeartbeat.last_heartbeat_at.desc())
    )
    rows = result.scalars().all()
    now = utcnow()
    alive = [
        r for r in rows
        if r.last_heartbeat_at is not None
        and (now - r.last_heartbeat_at).total_seconds() < _WORKER_STALE_SECONDS
    ]
    if alive:
        latest = max(alive, key=lambda r: r.last_heartbeat_at)
        return {
            "status": "ok",
            "count": len(alive),
            "last_heartbeat_at": latest.last_heartbeat_at.isoformat()
            if latest.last_heartbeat_at else None,
            "workers": [r.service_id for r in alive],
        }
    return {"status": "down", "count": 0, "last_heartbeat_at": None, "workers": []}


def _logs_stats() -> dict:
    """统计 logs/ 目录下各日志文件的大小（供日志轮转与告警参考）。"""
    log_dir = _logs_dir()
    files: list[dict] = []
    total = 0
    if log_dir.exists():
        for fpath in log_dir.rglob("*.log"):
            try:
                size = fpath.stat().st_size
            except OSError:
                continue
            total += size
            files.append({"name": fpath.name, "size_bytes": size})
    files.sort(key=lambda f: -f["size_bytes"])
    return {"dir": str(log_dir), "total_bytes": total, "files": files}


def _compute_alerts(
    frontend: dict, worker: dict, disk: dict, memory: dict | None
) -> list[str]:
    """根据各服务与资源状态生成告警文案列表。"""
    alerts: list[str] = []
    if frontend.get("status") != "ok":
        alerts.append("前端服务未响应")
    if worker.get("status") != "ok":
        alerts.append("worker 未运行或心跳超时")
    if disk.get("used_percent", 0) >= _DISK_ALERT_PERCENT:
        alerts.append(f"磁盘使用率超过 {_DISK_ALERT_PERCENT:.0f}%")
    if memory and memory.get("used_percent", 0) >= _MEMORY_ALERT_PERCENT:
        alerts.append(f"内存使用率超过 {_MEMORY_ALERT_PERCENT:.0f}%")
    return alerts


async def collect_health(db: AsyncSession) -> dict:
    """汇总各服务健康状态、资源占用与告警。"""
    frontend = await _probe_frontend()
    worker = await _worker_status(db)
    disk = disk_usage(settings.storage_root.parent)
    memory = memory_usage()
    alerts = _compute_alerts(frontend, worker, disk, memory)

    return {
        "services": {
            "backend": {"status": "ok"},  # 能响应本端点即健康
            "frontend": frontend,
            "worker": worker,
        },
        "resources": {
            "disk": disk,
            "memory": memory,
            "logs": _logs_stats(),
        },
        "alerts": alerts,
        "checked_at": utcnow().isoformat(),
    }
