"""LanceDB 向量存储封装（兼容薄壳）。

实际实现已迁移至 ``app.services.vector.store``，本模块保留原路径，
仅做 re-export，保证旧调用方（``search.py``、``task_runners/common.py``、
``scraper_service.py``、``inspiration_service.py`` 等）无需改动即可继续使用。
"""

from app.services.vector.store import (
    batch_upsert_vectors,
    count_vectors,
    delete_inspiration_vectors,
    delete_inspiration_vectors_batch,
    get_status,
    get_vector,
    is_lancedb_available,
    list_vector_ids,
    search_vectors,
    upsert_vector,
)

__all__ = [
    "is_lancedb_available",
    "upsert_vector",
    "batch_upsert_vectors",
    "search_vectors",
    "get_vector",
    "count_vectors",
    "list_vector_ids",
    "delete_inspiration_vectors",
    "delete_inspiration_vectors_batch",
    "get_status",
]
