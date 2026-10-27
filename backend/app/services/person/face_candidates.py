"""人物详情页的「人脸注册候选」：已匹配素材 + 未匹配候选脸聚类。

为什么需要它（2026-10 实测）：全库 498 位博主里只有 **22 位**注册了人脸，而「已匹配
到博主」的检测只存在于已注册的博主身上（468 位一条都没有）——所以「拿已匹配素材一键
注册」只能覆盖那 22~30 位，对大盘无解。

真正能放量的路是第二条：博主的素材里**未被匹配**的人脸，按「同一张脸反复出现」聚成
簇（实测「单脸素材内 ≥3 条未匹配检测」的博主有 141 位），把簇的封面给用户确认后取该
簇里质量最高的 3 张注册。

为什么不自动注册：采集素材里会出现路人、买家秀、其他博主/模特；把别人的脸注册成她，
会让整库匹配持续误判（代价远高于「没注册」）。所以本模块只**产出候选**，注册动作由
用户确认后调用既有的 ``POST /api/bloggers/{id}/face``。
"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.face import InspirationFaceDetection
from app.models.inspiration import Inspiration
from app.models.person import InspirationBlogger
from app.services.face_cluster import EDGE_THRESHOLD, FACE_DIM, _run_clustering

logger = logging.getLogger(__name__)

# 参与候选的检测数上限：一位头部博主可能有几千条检测（最大 3,426 条素材），
# 全量加载会把一次「打开候选面板」变成几百 MB 内存。取 det_score 最高的一批即可
# ——聚类要的是「反复出现的那张脸」，前 1500 条足够覆盖。
MAX_CANDIDATE_FACES = 1500
# 单簇最少出现次数：少于 3 次不足以判断是「同一个人」，只会是路人
MIN_CLUSTER_SIZE = 3
# 单簇上限：太大说明素材里有大量重复/合影，拆开以免把多个人合成一簇
MAX_CLUSTER_SIZE = 50
# 检测置信度下限：低于此值的脸（模糊/侧脸/小脸）不参与候选
MIN_DET_SCORE = 0.5
# 返回给前端的簇数上限（面板一屏能看多少）
MAX_CLUSTERS = 8


async def _single_face_inspiration_ids(
    db: AsyncSession, inspiration_ids: list[str]
) -> set[str]:
    """筛出「整张素材只检出 1 张脸」的素材 ID（多脸素材的候选无法判断哪张是她）。"""
    if not inspiration_ids:
        return set()
    rows = await db.execute(
        select(InspirationFaceDetection.inspiration_id)
        .where(InspirationFaceDetection.inspiration_id.in_(inspiration_ids))
        .group_by(InspirationFaceDetection.inspiration_id)
        .having(func.count() == 1)
    )
    return set(rows.scalars().all())


async def list_face_candidates(db: AsyncSession, blogger_id: int) -> dict:
    """列出某博主的注册候选：已匹配素材（一键注册）+ 未匹配候选脸簇。

    Args:
        db: 数据库会话。
        blogger_id: 博主 ID。

    Returns:
        ``{"matched": [...], "clusters": [...], "candidate_faces": N}``：

        - ``matched``：已匹配到 TA 且**人工确认过**（或历史 null 状态）的检测，
          按 ``det_score`` 降序，最多 12 条，前端做「一键注册」的默认选图；
        - ``clusters``：TA 素材里未匹配人脸聚出的簇（出现 ≥3 次），每簇给封面、
          成员数、平均置信度与前 3 张素材 ID（前端确认后直接注册）；
        - ``candidate_faces``：参与聚类的未匹配人脸数（用于面板说明「基于多少张脸」）。
    """
    # 注意：这里必须是**子查询**（IN (SELECT ...)），不能 .scalar_subquery()——
    # 标量子查询会退化成 `IN (单个值)`，一条候选都查不出来（踩过：全返回 0）。
    single_face_subq = (
        select(InspirationFaceDetection.inspiration_id)
        .group_by(InspirationFaceDetection.inspiration_id)
        .having(func.count() == 1)
    )

    # ── 1) 已匹配素材（A3a 一键注册的来源）──
    matched_rows = (
        await db.execute(
            select(
                InspirationFaceDetection.inspiration_id,
                InspirationFaceDetection.det_score,
                Inspiration.thumbnail_path,
                Inspiration.file_path,
            )
            .join(
                Inspiration,
                Inspiration.id == InspirationFaceDetection.inspiration_id,
            )
            .where(
                InspirationFaceDetection.matched_blogger_id == blogger_id,
                # 只用「人工确认过」或「历史单素材检测结果（match_status 为 NULL）」：
                # ⚠ 不能写 .in_((None, "confirmed"))——SQL 里 `IN (NULL, ...)` 对 NULL
                # 行恒为 UNKNOWN，历史数据（全库 1 条）会被静默漏掉。
                or_(
                    InspirationFaceDetection.match_status.is_(None),
                    InspirationFaceDetection.match_status == "confirmed",
                ),
                # 人工标过「不匹配」的检测不能当注册素材（复核时被否掉的脸）
                InspirationFaceDetection.match_excluded.is_not(True),
                Inspiration.deleted_at.is_(None),
                InspirationFaceDetection.inspiration_id.in_(single_face_subq),
            )
            .order_by(InspirationFaceDetection.det_score.desc())
            .limit(12)
        )
    ).all()
    matched = [
        {
            "inspiration_id": row[0],
            "det_score": round(float(row[1] or 0.0), 4),
            "thumbnail_path": row[2] or row[3],
        }
        for row in matched_rows
    ]

    # ── 2) 未匹配候选脸（A3b 聚类）──
    candidate_rows = (
        await db.execute(
            select(
                InspirationFaceDetection.id,
                InspirationFaceDetection.inspiration_id,
                InspirationFaceDetection.det_score,
                InspirationFaceDetection.embedding,
                Inspiration.thumbnail_path,
            )
            .join(
                InspirationBlogger,
                InspirationBlogger.inspiration_id == InspirationFaceDetection.inspiration_id,
            )
            .join(
                Inspiration,
                Inspiration.id == InspirationFaceDetection.inspiration_id,
            )
            .where(
                InspirationBlogger.blogger_id == blogger_id,
                InspirationFaceDetection.matched_blogger_id.is_(None),
                # IS NOT TRUE：历史行该列可能为 NULL，用 IS FALSE 会把它们全排掉
                InspirationFaceDetection.match_excluded.is_not(True),
                Inspiration.deleted_at.is_(None),
                InspirationFaceDetection.det_score >= MIN_DET_SCORE,
                InspirationFaceDetection.inspiration_id.in_(single_face_subq),
            )
            .order_by(InspirationFaceDetection.det_score.desc())
            .limit(MAX_CANDIDATE_FACES)
        )
    ).all()

    clusters: list[dict] = []
    if candidate_rows:
        meta = {row[0]: row for row in candidate_rows}
        embs = np.stack(
            [np.frombuffer(row[3], dtype=np.float32) for row in candidate_rows]
        ).astype(np.float32)
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        embs = embs / np.clip(norms, 1e-8, None)
        result = _run_clustering(
            [row[0] for row in candidate_rows],
            embs,
            EDGE_THRESHOLD,
            MIN_CLUSTER_SIZE,
            MAX_CLUSTER_SIZE,
        )
        for idx, group in enumerate(result["groups"][:MAX_CLUSTERS]):
            members = [meta[did] for did in group["detection_ids"] if did in meta]
            if not members:
                continue
            # 按置信度排序：封面取最高的那张，注册取前 3 张
            members.sort(key=lambda r: float(r[2] or 0.0), reverse=True)
            top = members[:3]
            scores = [float(r[2] or 0.0) for r in members]
            clusters.append(
                {
                    "cluster_id": idx,
                    "size": len(members),
                    "avg_score": round(sum(scores) / len(scores), 4),
                    "thumbnail_path": members[0][4],
                    "inspiration_ids": [r[1] for r in top],
                    "candidates": [
                        {
                            "inspiration_id": r[1],
                            "det_score": round(float(r[2] or 0.0), 4),
                            "thumbnail_path": r[4],
                        }
                        for r in top
                    ],
                }
            )

    return {
        "matched": matched,
        "clusters": clusters,
        "candidate_faces": len(candidate_rows),
        "face_dim": FACE_DIM,
    }
