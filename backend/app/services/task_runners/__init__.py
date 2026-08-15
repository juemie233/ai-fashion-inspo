"""后台任务执行器包：按任务类型拆分到独立子模块。

- common.py: 多任务共用的辅助（错误类型、重试策略、分片、时间戳、并发常量）
- batch_analyze.py: 批量分析任务（逐张调用 AI 标签分析）
- quality_check.py: 质量审核任务（逐张调用轻量 AI 审核）
- batch_delete.py: 批量删除任务（删文件、写墓碑、删数据库记录）
- deduplicate.py: 智能去重任务（全库 MD5 扫描 + 评分保留 + 物理删除）

对外公共入口仍为 app.services.task_runner（re-export），既有调用方无需改动。
"""

from app.services.task_runners.common import (
    PermanentTaskError,
    RecoverableTaskError,
    _ANALYZE_CONCURRENCY,
    _chunked,
    _delete_inspiration_vectors,
    _is_recoverable_error,
    _retry_delay,
    _schedule_retry,
    _utcnow,
)

__all__ = [
    "PermanentTaskError",
    "RecoverableTaskError",
    "_ANALYZE_CONCURRENCY",
    "_chunked",
    "_delete_inspiration_vectors",
    "_is_recoverable_error",
    "_retry_delay",
    "_schedule_retry",
    "_utcnow",
]
