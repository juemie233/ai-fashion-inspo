"""向量嵌入服务：通过 Ollama 生成图片 CLIP 嵌入向量，实现以图搜图。

使用 Ollama 的 minicpm-v 或 llava 模型生成视觉嵌入向量。
如果视觉嵌入模型不可用，回退到使用图片的标签文本生成嵌入。
"""

import json
import logging
from pathlib import Path

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import Inspiration

logger = logging.getLogger(__name__)

# 嵌入向量的维度（取决于所选模型）
EMBEDDING_DIM = 512


async def generate_embedding(file_path: str) -> list[float] | None:
    """
    为图片生成嵌入向量。

    策略：
    1. 尝试使用 Ollama 视觉模型生成描述文本，再对该文本生成嵌入
    2. 如果失败，返回 None
    """
    try:
        full_path = settings.storage_root / file_path
        if not full_path.exists():
            logger.warning(f"图片不存在，无法生成嵌入: {full_path}")
            return None

        # 读取图片为 base64
        import base64

        with open(full_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        # 使用 Ollama 的视觉模型生成简短描述
        async with httpx.AsyncClient(timeout=30) as client:
            describe_resp = await client.post(
                f"{settings.ollama_base_url}/api/chat",
                json={
                    "model": settings.ollama_vision_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": "用简短的中文描述这张穿搭图片的关键视觉特征（颜色、风格、单品），不超过50字。",
                            "images": [image_data],
                        }
                    ],
                    "stream": False,
                },
            )

            if describe_resp.status_code != 200:
                logger.error(f"视觉模型描述失败: {describe_resp.text}")
                return None

            description = describe_resp.json()["message"]["content"].strip()

            # 对描述文本生成嵌入向量
            embed_resp = await client.post(
                f"{settings.ollama_base_url}/api/embeddings",
                json={
                    "model": settings.ollama_embedding_model,
                    "prompt": description,
                },
            )

            if embed_resp.status_code != 200:
                logger.error(f"嵌入模型失败: {embed_resp.text}")
                return None

            embedding = embed_resp.json()["embedding"]
            return embedding

    except Exception as e:
        logger.error(f"生成嵌入向量失败: {e}")
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    if len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


async def find_similar_images(
    db: AsyncSession,
    inspiration_id: str,
    top_k: int = 10,
) -> list[dict]:
    """
    在素材库中搜索与指定素材最相似的图片。

    当前实现使用标签相似度作为代理（标签重叠越多越相似）。
    完整嵌入向量方案在 Ollama 视觉嵌入 API 可用后启用。
    """
    from app.models.tag import InspirationTag, Tag

    # 获取源素材的所有标签
    result = await db.execute(
        select(InspirationTag).where(
            InspirationTag.inspiration_id == inspiration_id
        )
    )
    source_tags = result.scalars().all()
    source_tag_ids = {t.tag_id for t in source_tags}

    if not source_tag_ids:
        return []

    # 查找共享标签的素材
    result = await db.execute(
        select(
            Inspiration,
            text("COUNT(inspiration_tags.tag_id) AS shared_count"),
        )
        .join(InspirationTag, Inspiration.id == InspirationTag.inspiration_id)
        .where(
            InspirationTag.tag_id.in_(source_tag_ids),
            Inspiration.id != inspiration_id,
        )
        .group_by(Inspiration.id)
        .order_by(text("shared_count DESC"))
        .limit(top_k)
    )

    similar = []
    for row in result:
        insp = row[0]
        shared = row[1]
        similarity = shared / len(source_tag_ids) if source_tag_ids else 0
        similar.append({
            "id": insp.id,
            "file_path": insp.file_path,
            "thumbnail_path": insp.thumbnail_path,
            "shared_tags": int(shared),
            "similarity": round(similarity, 3),
        })

    return similar
