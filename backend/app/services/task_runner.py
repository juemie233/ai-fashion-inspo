"""任务队列门面：任务执行器注册表 + 创建函数 re-export。

- 执行器按任务类型拆分到 ``app.services.task_runners`` 包；
- 本文件维护 ``TASK_REGISTRY``（类型 → 执行器 + 是否 GPU 独占），worker 只读这张表；
- 同时 re-export 既有创建/查询函数，保证 `from app.services.task_runner import ...`
  的旧调用方无需改动。

新增任务类型只改本文件：在 ``TASK_REGISTRY`` 登记执行器并标记是否 GPU 独占，
不要再到 worker 里维护第二份互斥名单。
"""

from app.services.task_runners.batch_analyze import (
    MAX_MULTI_COMBINATIONS,
    _analyze_one,
    _analyze_one_multi,
    _last_analysis_error,
    create_batch_analyze_task,
    create_multi_analyze_task,
    execute_batch_analyze,
    execute_multi_analyze,
)
from app.services.task_runners.batch_delete import (
    create_batch_delete_task,
    execute_batch_delete,
)
from app.services.task_runners.common import (
    PermanentTaskError,
    RecoverableTaskError,
    _ANALYZE_CONCURRENCY,
    _analyze_concurrency,
    _broadcast_task_event,
    _chunked,
    _delete_inspiration_vectors,
    _is_recoverable_error,
    _retry_delay,
    _schedule_retry,
    utcnow,
)
from app.services.task_runners.deduplicate import (
    create_deduplicate_task,
    execute_deduplicate,
)
from app.services.task_runners.blogger_avatar_fill import (
    create_avatar_fill_task,
    execute_avatar_fill,
)
from app.services.task_runners.enrich_blogger_profile import (
    create_enrich_blogger_profile_task,
    execute_enrich_blogger_profile,
)
from app.services.task_runners.f2_import import (
    create_f2_import_task,
    create_f2_import_task_if_idle,
    execute_f2_import,
    f2_import_status,
    get_f2_auto_status,
    maybe_schedule_auto_import,
)
from app.services.task_runners.face_scan import (
    create_face_match_task,
    create_face_scan_task,
    execute_face_match,
    execute_face_scan,
)
from app.services.task_runners.face_cluster import (
    create_face_cluster_task,
    execute_face_cluster,
)
from app.services.task_runners.tag_cluster import (
    create_tag_cluster_scan_task,
    execute_tag_cluster_scan,
)
from app.services.task_runners.tag_graph import (
    create_tag_network_analyze_task,
    execute_tag_network_analyze,
)
from app.services.task_runners.quality_check import (
    _quality_check_one,
    create_quality_check_task,
    execute_quality_check,
)
from app.services.task_runners.tag_health import (
    create_tag_health_scan_task,
    execute_tag_health_scan,
)
from app.services.task_runners.phash_backfill import (
    create_phash_backfill_task,
    execute_phash_backfill,
)
from app.services.task_runners.vector_backfill import (
    VECTOR_BACKFILL_BATCH_SIZE,
    create_vector_backfill_task,
    enqueue_vector_backfills,
    execute_vector_backfill,
    flush_pending_vector_backfills,
    purge_small_backfill_tasks,
)
from app.services.task_runners.image_gen import execute_image_gen
from dataclasses import dataclass
from collections.abc import Awaitable, Callable



@dataclass(frozen=True)
class TaskSpec:
    """任务类型的执行元数据。

    gpu_exclusive 是资源约束的唯一真源：worker 的互斥组直接由它派生，避免
    「新任务登记了 handler，却忘了登记 worker 的 GPU_EXCLUSIVE_TASK_TYPES」。
    """

    handler: Callable[..., Awaitable[None]]
    gpu_exclusive: bool = False


# 任务类型 → 执行器。新增类型只改这里；gpu_exclusive=True 的类型之间全局串行。
# 实测：向量回填与质量审核并发时，同一个检查点从 5.4 秒退化到 12.7 分钟。
TASK_REGISTRY: dict[str, TaskSpec] = {
    "batch_analyze": TaskSpec(execute_batch_analyze, gpu_exclusive=True),
    "multi_analyze": TaskSpec(execute_multi_analyze, gpu_exclusive=True),
    "quality_check": TaskSpec(execute_quality_check, gpu_exclusive=True),
    "batch_delete": TaskSpec(execute_batch_delete),
    "deduplicate": TaskSpec(execute_deduplicate),
    "vector_backfill": TaskSpec(execute_vector_backfill, gpu_exclusive=True),
    "phash_backfill": TaskSpec(execute_phash_backfill),
    "face_scan": TaskSpec(execute_face_scan, gpu_exclusive=True),
    "face_match": TaskSpec(execute_face_match, gpu_exclusive=True),
    "face_cluster": TaskSpec(execute_face_cluster),
    "enrich_blogger_profile": TaskSpec(execute_enrich_blogger_profile),
    "blogger_avatar_fill": TaskSpec(execute_avatar_fill),
    "f2_import": TaskSpec(execute_f2_import),
    "tag_health_scan": TaskSpec(execute_tag_health_scan),
    "tag_cluster_scan": TaskSpec(execute_tag_cluster_scan),
    "tag_network_analyze": TaskSpec(execute_tag_network_analyze),
    "image_gen": TaskSpec(execute_image_gen, gpu_exclusive=True),
}

TASK_HANDLERS: dict[str, Callable[..., Awaitable[None]]] = {
    task_type: spec.handler for task_type, spec in TASK_REGISTRY.items()
}
GPU_EXCLUSIVE_TASK_TYPES: frozenset[str] = frozenset(
    task_type
    for task_type, spec in TASK_REGISTRY.items()
    if spec.gpu_exclusive
)
TASK_TYPES: frozenset[str] = frozenset(TASK_REGISTRY)

__all__ = [
    "MAX_MULTI_COMBINATIONS",
    "TASK_HANDLERS",
    "TASK_REGISTRY",
    "TASK_TYPES",
    "GPU_EXCLUSIVE_TASK_TYPES",
    "TaskSpec",
    "_analyze_one",
    "_analyze_one_multi",
    "_last_analysis_error",
    "create_batch_analyze_task",
    "create_multi_analyze_task",
    "execute_batch_analyze",
    "execute_multi_analyze",
    "create_batch_delete_task",
    "execute_batch_delete",
    "PermanentTaskError",
    "RecoverableTaskError",
    "_ANALYZE_CONCURRENCY",
    "_analyze_concurrency",
    "_broadcast_task_event",
    "_chunked",
    "_delete_inspiration_vectors",
    "_is_recoverable_error",
    "_retry_delay",
    "_schedule_retry",
    "utcnow",
    "create_deduplicate_task",
    "execute_deduplicate",
    "create_avatar_fill_task",
    "execute_avatar_fill",
    "create_enrich_blogger_profile_task",
    "execute_enrich_blogger_profile",
    "create_f2_import_task",
    "create_f2_import_task_if_idle",
    "execute_f2_import",
    "f2_import_status",
    "get_f2_auto_status",
    "maybe_schedule_auto_import",
    "create_face_match_task",
    "create_face_scan_task",
    "execute_face_match",
    "execute_face_scan",
    "create_face_cluster_task",
    "execute_face_cluster",
    "create_tag_cluster_scan_task",
    "execute_tag_cluster_scan",
    "create_tag_network_analyze_task",
    "execute_tag_network_analyze",
    "_quality_check_one",
    "create_quality_check_task",
    "execute_quality_check",
    "create_tag_health_scan_task",
    "execute_tag_health_scan",
    "create_phash_backfill_task",
    "execute_phash_backfill",
    "VECTOR_BACKFILL_BATCH_SIZE",
    "create_vector_backfill_task",
    "enqueue_vector_backfills",
    "execute_vector_backfill",
    "flush_pending_vector_backfills",
    "purge_small_backfill_tasks",
    "execute_image_gen",
]
