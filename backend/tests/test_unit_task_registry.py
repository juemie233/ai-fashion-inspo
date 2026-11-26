"""任务注册表完整性：GPU 独占标记与执行器必须一次登记。

新增任务类型时，这个测试会强制作者在 ``TaskSpec(gpu_exclusive=...)`` 与
下方的非独占白名单之间做一次显式选择，避免只登记 handler 却漏掉 worker 的
互斥组（实测并发抢 GPU 会让检查点从 5.4 秒退化到 12.7 分钟）。
"""

from app.services.task_runner import (
    GPU_EXCLUSIVE_TASK_TYPES,
    TASK_HANDLERS,
    TASK_REGISTRY,
    TASK_TYPES,
)
from app.worker import GPU_EXCLUSIVE_TASK_TYPES as WORKER_GPU_EXCLUSIVE


# 明确「不吃 GPU、允许与重负载任务并发」的任务类型白名单。
# 新增任务若不在 GPU_EXCLUSIVE 里，必须加到这里并确认原因。
NON_GPU_TASK_TYPES = frozenset(
    {
        "batch_delete",
        "deduplicate",
        "phash_backfill",
        "face_cluster",
        "enrich_blogger_profile",
        "blogger_avatar_fill",
        "f2_import",
        "tag_health_scan",
        "tag_cluster_scan",
        "tag_network_analyze",
    }
)


def test_task_registry_and_handlers_are_consistent():
    assert TASK_TYPES == frozenset(TASK_REGISTRY)
    assert TASK_HANDLERS == {
        task_type: spec.handler for task_type, spec in TASK_REGISTRY.items()
    }
    assert all(callable(spec.handler) for spec in TASK_REGISTRY.values())


def test_gpu_exclusive_set_is_derived_and_complete_in_worker():
    assert WORKER_GPU_EXCLUSIVE is GPU_EXCLUSIVE_TASK_TYPES
    assert GPU_EXCLUSIVE_TASK_TYPES <= TASK_TYPES
    assert TASK_TYPES - GPU_EXCLUSIVE_TASK_TYPES == NON_GPU_TASK_TYPES
