"""向量检索服务编排层（兼容薄壳）。

实际实现已迁移至 ``app.services.vector.similarity``，本模块保留原路径，
仅做 re-export，保证旧调用方（``inspiration_service.py``、
``scripts/backfill_vectors.py`` 等）无需改动即可继续使用。
"""

from app.services.vector.similarity import (
    backfill_all_vectors,
    find_similar_hybrid,
    rebuild_image_vector,
    rebuild_inspiration_vectors,
    rebuild_text_vector,
)

__all__ = [
    "backfill_all_vectors",
    "rebuild_text_vector",
    "rebuild_image_vector",
    "rebuild_inspiration_vectors",
    "find_similar_hybrid",
]
