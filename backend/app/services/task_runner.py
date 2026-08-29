"""任务队列执行逻辑：供独立 worker 进程（app/worker.py）调用。

本模块只负责「执行」：读取 task_queue 记录、逐张调用现有 AI 分析服务、
更新进度与结果，不包含任何 HTTP/API 层逻辑。

代码已按任务类型拆分到 app.services.task_runners 包，本文件仅做统一 re-export，
保证既有 `from app.services.task_runner import ...` 调用方无需改动。
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
from app.services.task_runners.enrich_blogger_profile import (
    create_enrich_blogger_profile_task,
    execute_enrich_blogger_profile,
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
from app.services.task_runners.vector_backfill import (
    VECTOR_BACKFILL_BATCH_SIZE,
    create_vector_backfill_task,
    enqueue_vector_backfills,
    execute_vector_backfill,
    flush_pending_vector_backfills,
    purge_small_backfill_tasks,
)

# 任务类型 → 执行函数的分发表：worker 按 task.type 分发到对应执行器。
# 新增任务类型时，在此注册对应的 execute_xxx 函数即可，worker 无需改动。
TASK_HANDLERS = {
    "batch_analyze": execute_batch_analyze,
    "multi_analyze": execute_multi_analyze,
    "quality_check": execute_quality_check,
    "batch_delete": execute_batch_delete,
    "deduplicate": execute_deduplicate,
    "vector_backfill": execute_vector_backfill,
    "face_scan": execute_face_scan,
    "face_match": execute_face_match,
    "face_cluster": execute_face_cluster,
    "enrich_blogger_profile": execute_enrich_blogger_profile,
    "tag_health_scan": execute_tag_health_scan,
    "tag_cluster_scan": execute_tag_cluster_scan,
    "tag_network_analyze": execute_tag_network_analyze,
}
