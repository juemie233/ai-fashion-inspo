"""任务事件 WebSocket 广播（跨模块共享的安全广播入口）。

供 worker / task_runners 在任务生命周期关键点（创建、开始执行、进度更新、
成功/失败/取消）向前端推送实时事件，替代轮询的主要数据来源。

事件契约（前端 web/src 按此消费，字段向后兼容只增不改）：

    {
        "type": "task_event",
        "event": "running" | "progress" | "success" | "failed" | "cancelled",
        "task_id": int,
        "task_type": str,          # batch_analyze / multi_analyze / vector_backfill / ...
        "status": str,             # 任务当前状态（pending/running/success/...）
        "progress": int,           # 0~100（已知时携带）
        "done": int, "total": int, # 完成计数（已知时携带）
        "error": str | None,       # 失败原因（仅 failed）
    }

本模块刻意做成「永不抛错」：广播失败只记 debug 日志，绝不影响任务主流程；
WS 服务不可用 / 无连接 / 广播异常时前端自动降级为轮询（见前端 useTaskEvents）。
"""

import logging

logger = logging.getLogger(__name__)


async def broadcast_task_event(payload: dict) -> None:
    """向所有 WS 客户端广播一条任务事件（安全入口，任何异常静默降级）。

    参数:
        payload: 事件体，须符合本模块 docstring 的事件契约
            （调用方至少携带 type/task_event/task_id/task_type/status）。
    """
    try:
        from app.routers.ws import manager

        await manager.broadcast(payload)
    except Exception:
        # worker 进程 / WS 未启用 / 无连接等场景：静默忽略，不影响任务执行
        logger.debug("任务事件广播失败（忽略，前端走轮询降级）", exc_info=True)
