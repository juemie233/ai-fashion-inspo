"""全库人脸-人物矩阵匹配服务：一次矩阵乘完成所有检测人脸 × 人物特征库比对。

背景：原 blogger_face.detect_inspiration_faces 用 Python 逐条循环点积匹配
（人脸 × 全部博主），人物规模上千后慢。本模块改为 numpy 矩阵乘：

- 博主与模特特征库合并为一个矩阵（ArcFace 特征同一空间，余弦可比），
  每张脸取全库最高分者——两者互斥，一张人脸至多命中一种人物；
- 批量场景产出「待审核候选」（match_status=pending），人工确认由扫描审核
  接口完成（写人物关联表 + 置 confirmed）；
- 单素材「检测并匹配」复用同一矩阵函数但保持原行为（match_status=NULL，
  直接展示，不经过审核流程）。
"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.face import (
    BloggerFaceEmbedding,
    InspirationFaceDetection,
    ModelFaceEmbedding,
)

logger = logging.getLogger(__name__)

# 人脸分块大小：控制峰值内存（F×P 相似度矩阵 float32）
MATCH_CHUNK_SIZE = 10_000


def _bytes_to_embedding(data: bytes) -> np.ndarray:
    """BLOB bytes → float32 数组（校验维度）。"""
    emb = np.frombuffer(data, dtype=np.float32)
    if emb.shape[0] != 512:
        raise ValueError(f"特征维度异常: {emb.shape[0]}（期望 512）")
    return emb


async def load_person_library(
    db: AsyncSession,
    scope: str = "all",
    person_ids: list[int] | None = None,
) -> list[dict]:
    """加载人物特征库（博主/模特按 scope 过滤）。

    scope: "all"（博主+模特合并）/ "bloggers" / "models"；
    person_ids: 限定具体人物（建议配合单一 scope 使用，避免博主/模特 id 撞号歧义）；
    None 或空列表表示不限。
    """
    library: list[dict] = []
    if scope in ("all", "bloggers"):
        rows = await db.execute(select(BloggerFaceEmbedding))
        for r in rows.scalars().all():
            if person_ids and r.blogger_id not in person_ids:
                continue
            try:
                emb = _bytes_to_embedding(r.embedding)
            except ValueError as e:
                # 异常维度特征跳过（与检测表脏数据同策略）：单条脏数据不让
                # 整个匹配任务失败，缺失人物只影响其本人的匹配结果
                logger.warning("全库匹配跳过异常维度博主特征（blogger_id=%s）: %s", r.blogger_id, e)
                continue
            library.append(
                {
                    "person_type": "blogger",
                    "person_id": r.blogger_id,
                    "embedding": emb,
                }
            )
    if scope in ("all", "models"):
        rows = await db.execute(select(ModelFaceEmbedding))
        for r in rows.scalars().all():
            if person_ids and r.model_id not in person_ids:
                continue
            try:
                emb = _bytes_to_embedding(r.embedding)
            except ValueError as e:
                logger.warning("全库匹配跳过异常维度模特特征（model_id=%s）: %s", r.model_id, e)
                continue
            library.append(
                {
                    "person_type": "model",
                    "person_id": r.model_id,
                    "embedding": emb,
                }
            )
    return library


def matrix_match_faces(
    face_embeddings: np.ndarray,
    library: list[dict],
    threshold: float,
) -> list[dict | None]:
    """矩阵匹配：每张脸对全库取最高分者，低于阈值视为未匹配。

    参数:
        face_embeddings: (F, 512) float32，已归一化
        library: load_person_library 输出（空库返回全 None）
        threshold: 余弦相似度阈值（低于视为未知人脸）

    返回:
        与 F 等长的列表：{"person_type", "person_id", "score"} 或 None。
    """
    f_count = face_embeddings.shape[0]
    if f_count == 0 or not library:
        return [None] * f_count
    persons = np.stack([item["embedding"] for item in library], axis=0)  # (P, 512)
    scores = face_embeddings @ persons.T  # (F, P)
    best_idx = scores.argmax(axis=1)
    best_scores = scores[np.arange(f_count), best_idx]
    results: list[dict | None] = []
    for i in range(f_count):
        if best_scores[i] < threshold:
            results.append(None)
            continue
        item = library[int(best_idx[i])]
        results.append(
            {
                "person_type": item["person_type"],
                "person_id": item["person_id"],
                "score": float(best_scores[i]),
            }
        )
    return results


async def match_all_faces(
    db: AsyncSession,
    scope: str = "all",
    person_ids: list[int] | None = None,
    threshold: float | None = None,
    chunk_size: int = MATCH_CHUNK_SIZE,
) -> dict:
    """全库候选匹配：所有检测人脸 × 人物特征库矩阵比对，写入 pending 候选。

    - 产出为「待审核候选」（match_status=pending），不写人物关联表——
      最终关联由扫描页人工审核确认完成；
    - 已确认（锁定，match_status=confirmed）的记录整条跳过，不会被重新
      匹配或覆盖；
    - 只更新命中结果有变化的行（diff 后批量 UPDATE），避免无谓写库；
    - 分块执行控制峰值内存（默认每 1 万张人脸一块）。

    返回统计: total_faces / matched / unmatched / updated / library_size。
    """
    thr = threshold if threshold is not None else settings.face_match_threshold
    library = await load_person_library(db, scope=scope, person_ids=person_ids)
    rows = await db.execute(
        select(
            InspirationFaceDetection.id,
            InspirationFaceDetection.embedding,
            InspirationFaceDetection.matched_blogger_id,
            InspirationFaceDetection.matched_model_id,
            InspirationFaceDetection.confidence,
            InspirationFaceDetection.match_status,
        ).where(
            InspirationFaceDetection.embedding != b"",
            # 人工「不匹配」的人脸不再参与后续全库匹配（决定持久化）
            InspirationFaceDetection.match_excluded.is_(False),
            # 锁定（已确认）记录不参与全库匹配，避免覆盖人工确认结果
            # （!= 'confirmed' 对 NULL 不成立，需显式补 NULL 分支）
            or_(
                InspirationFaceDetection.match_status != "confirmed",
                InspirationFaceDetection.match_status.is_(None),
            ),
        )
    )
    detections = rows.all()

    # 数据前置：解析并过滤脏嵌入。真实库曾在检测表出现过非 512 维异常数据
    # （face_cluster 同策略），单条脏数据会把整个匹配任务打成失败——
    # 这里跳过并计数（bad_embeddings），有效人脸数 = total_faces，保证
    # matched + unmatched == total_faces 的统计恒等式。
    valid: list[tuple[InspirationFaceDetection, np.ndarray]] = []
    bad_embeddings = 0
    for d in detections:
        emb = np.frombuffer(d.embedding, dtype=np.float32)
        if emb.shape[0] != 512:
            bad_embeddings += 1
            logger.warning(
                "全库匹配跳过异常维度嵌入（detection_id=%s, dim=%d）",
                d.id,
                emb.shape[0],
            )
            continue
        valid.append((d, emb))
    total = len(valid)
    matched = 0
    unmatched = 0
    changes: list[dict] = []

    for start in range(0, total, chunk_size):
        chunk = valid[start : start + chunk_size]
        faces = np.stack([emb for _det, emb in chunk], axis=0).astype(np.float32)
        results = matrix_match_faces(faces, library, thr)
        for (det, _emb), result in zip(chunk, results):
            if result is None:
                new_blogger: int | None = None
                new_model: int | None = None
                new_conf: float | None = None
            elif result["person_type"] == "blogger":
                new_blogger = result["person_id"]
                new_model = None
                new_conf = round(result["score"], 4)
            else:
                new_blogger = None
                new_model = result["person_id"]
                new_conf = round(result["score"], 4)
            if (
                det.matched_blogger_id != new_blogger
                or det.matched_model_id != new_model
                or det.confidence != new_conf
                or det.match_status != "pending"
            ):
                changes.append(
                    {
                        "id": det.id,
                        "matched_blogger_id": new_blogger,
                        "matched_model_id": new_model,
                        "confidence": new_conf,
                    }
                )
            if result is None:
                unmatched += 1
            else:
                matched += 1

    if changes:
        await db.execute(
            update(InspirationFaceDetection),
            [
                {
                    **c,
                    "match_status": "pending",
                }
                for c in changes
            ],
        )
        await db.commit()

    return {
        "total_faces": total,
        "matched": matched,
        "unmatched": unmatched,
        "updated": len(changes),
        "library_size": len(library),
        "threshold": thr,
        "scope": scope,
        # 被跳过的异常维度嵌入数（脏数据不参与比对，也不计入 total_faces）
        "bad_embeddings": bad_embeddings,
    }
