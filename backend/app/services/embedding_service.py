"""向量嵌入服务（兼容薄壳）。

实际实现已迁移至 ``app.services.vector.embedding``（编码）与
``app.services.vector.similarity``（相似度相关：``cosine_similarity``、
``find_similar_images``）。本模块保留原路径，仅做 re-export，
保证旧调用方（``search.py`` 等）无需改动即可继续使用。
"""

from app.services.vector.embedding import (
    IMAGE_EMBEDDING_DIM,
    TEXT_EMBEDDING_DIM,
    build_inspiration_text,
    generate_image_embedding,
    generate_text_embedding,
    get_image_embedding_status,
    get_text_embedding_status,
)
from app.services.vector.similarity import (
    cosine_similarity,
    find_similar_images,
)

__all__ = [
    "TEXT_EMBEDDING_DIM",
    "IMAGE_EMBEDDING_DIM",
    "generate_text_embedding",
    "get_text_embedding_status",
    "get_image_embedding_status",
    "generate_image_embedding",
    "cosine_similarity",
    "build_inspiration_text",
    "find_similar_images",
]
