"""服务心跳模型：记录长驻进程（worker / supervisor）的存活心跳。

用于「服务守护与监控」：
- worker 定期写入心跳，供健康检查端点判断 worker 是否存活；
- worker 异常退出后心跳停止，重启时据此精确重置遗留的 running 任务
  （替代原先「启动时无条件重置 running 任务」的粗暴做法）。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ServiceHeartbeat(Base):
    """长驻服务心跳：每个服务实例一行，按 service_id 唯一。

    service_id 由服务实例自行生成（如 ``worker-{pid}-{uuid前8位}``），
    进程重启后生成新 id，旧行成为「僵尸心跳」，由 worker 启动清理逻辑回收。
    """

    __tablename__ = "service_heartbeats"

    service_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    service_type: Mapped[str] = mapped_column(
        String(32), index=True
    )  # worker / supervisor 等
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 附加信息（主机名等）

    def __repr__(self) -> str:
        return (
            f"<ServiceHeartbeat(service_id={self.service_id!r}, "
            f"type={self.service_type!r})>"
        )
