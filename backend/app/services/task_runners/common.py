"""后台任务共用辅助：错误类型、重试策略、分片、时间戳、并发常量等。

本模块被各任务执行器（batch_analyze / quality_check / batch_delete / deduplicate）
及 worker 进程（app/worker.py）共用，避免跨模块重复定义。
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskQueue

logger = logging.getLogger(__name__)

# 批内并发度：批量分析任务量大，固定为 1（逐张串行）对单卡最稳。
# 注意：worker 进程与 API 进程（routers/ai_shared.py 的 _analysis_semaphore=2）各自持有
# 独立的进程内信号量，互不感知、无法跨进程共享。串行后最坏情况为 1（worker）+ 2（API）= 3 路，
# 相比原 2+2=4 路进一步降低单卡显存溢出风险。如需整体调整，请同步修改两处。
_ANALYZE_CONCURRENCY = 1


class RecoverableTaskError(Exception):
    """可恢复错误：任务应自动重试（模型超时 / Ollama 连接失败 / SQLite 数据库锁）。"""


class PermanentTaskError(Exception):
    """永久错误：任务不应重试（图片损坏 / 文件不存在等）。"""


def _utcnow() -> datetime:
    """返回当前 UTC 时间（naive datetime）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_recoverable_error(message: str) -> bool:
    """判断错误消息是否属于「可恢复错误」。

    可恢复错误包括：模型响应超时、无法连接 Ollama、Ollama 服务不可用、
    SQLite 数据库被锁（database is locked）。其余错误（图片损坏、文件不存在等）
    视为永久错误，不重试。

    参数:
        message: 错误消息

    返回:
        True 表示可恢复，False 表示永久错误
    """
    if not message:
        return False
    keywords = (
        "超时",
        "无法连接 Ollama",
        "无法连接",
        "Ollama 服务",
        "Ollama 返回",
        "返回空内容",
        "返回格式异常",
        "缺少 message",
        "暂时不可用",
        "请求过于频繁",
        "database is locked",
        "locked",
    )
    return any(k in message for k in keywords)


def _retry_delay(retry_count: int) -> int:
    """指数退避延迟（秒）：第 1 次约 30s，第 2 次约 2min。

    参数:
        retry_count: 当前已重试次数（1 表示第 1 次重试）
    """
    return min(120, 30 * (4 ** (retry_count - 1)))


def _chunked(values: list, size: int = 500) -> list[list]:
    """将列表按指定大小分片，避免 SQLite IN(...) 超过变量上限（SQLite 默认约 999 个变量）。

    参数:
        values: 待分片的列表
        size: 每片大小（默认 500，留足余量）

    返回:
        分片后的列表
    """
    return [values[i:i + size] for i in range(0, len(values), size)]


async def _delete_inspiration_vectors(inspiration_ids: list[str]) -> None:
    """批量删除素材在 LanceDB 中的向量（素材物理删除后调用）。

    删除函数由 app.services.vector_store 提供（并行 agent 实现），LanceDB 未安装时
    静默返回；此处额外兜底捕获异常并降级为警告，避免向量清理失败影响任务结果。
    """
    if not inspiration_ids:
        return
    try:
        from app.services.vector_store import delete_inspiration_vectors_batch
        await delete_inspiration_vectors_batch(inspiration_ids)
    except Exception as e:
        logger.warning(f"批量删除素材向量失败（忽略，不影响任务结果）: {e}")


async def _schedule_retry(db: AsyncSession, task: TaskQueue, error_msg: str) -> None:
    """安排任务自动重试（指数退避）；超过最大重试次数则标记失败。

    参数:
        db: 任务生命周期会话
        task: 任务记录
        error_msg: 本次失败原因
    """
    task.retry_count += 1
    task.error = error_msg
    # 重试前重置进度，避免重试任务显示 100% 却处于 pending
    task.progress = 0
    task.done = 0
    if task.retry_count <= task.max_retries:
        delay = _retry_delay(task.retry_count)
        task.status = "pending"
        task.next_retry_at = _utcnow() + timedelta(seconds=delay)
        logger.warning(
            f"任务将自动重试 #{task.id}，第 {task.retry_count}/{task.max_retries} 次，{delay} 秒后"
        )
    else:
        task.status = "failed"
        task.next_retry_at = None
        logger.error(f"任务已超过最大重试次数，标记失败: #{task.id}")
