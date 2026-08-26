"""未匹配人脸聚合聚类服务：把大量未匹配人脸按「疑似同一人」聚合成组。

性能设计（核心约束：**必须避免 O(n²) 全量矩阵**）：
- 主路径：hnswlib（HNSW 图结构 ANN，近似最近邻）为每张脸查 Top-K 近邻，
  只在近邻之间建边，复杂度 O(n log n)，内存 O(n·d)；
- 降级路径：hnswlib 未安装时回退 numpy 分块矩阵（O(n²)，仅适合小规模，
  超过阈值明确报错提示安装 hnswlib——与项目「向量依赖未装则降级」惯例一致）；
- 聚类：近邻边按相似度阈值过滤 → 并查集连通成组（复用标签聚类 UnionFind 思路，
  独立实现避免跨模块耦合）；
- 数据前置：过滤非 512 维脏 embedding（实测真实库存在 388 条异常维度），
  避免 hnswlib 维度报错；同时排除已确认/人工「不匹配」记录。

聚类结果不落库（组由检测记录上的 matched_* 状态派生），整组指派复用
``/api/face-scan/confirm``，无新增写库链路。
"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.face import InspirationFaceDetection
from app.models.inspiration import Inspiration

logger = logging.getLogger(__name__)

# 人脸特征维度（insightface ArcFace 输出）
FACE_DIM = 512

# ANN 近邻数：取 Top-K 近邻建边（K 越大召回越高、建图越慢；30 为精度/性能平衡点）
ANN_K = 30
# HNSW 建图参数（与基准测试一致：0.13s 建 5k 索引、召回 98.9%）
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 200
# 查询时加大搜索宽度提升召回（基准：ef=100 时召回 98.9%）
HNSW_EF_SEARCH = 100
# 相似度阈值：低于该值的近邻边丢弃（与 face_match_threshold 语义一致）
CLUSTER_THRESHOLD = 0.5
# 组内最少人脸数：少于该数的组不展示（孤脸不成组）
MIN_GROUP_SIZE = 2
# 降级路径（O(n²) 分块矩阵）的安全上限：超过则报错提示安装 hnswlib
O2_MAX_FACES = 20_000


class FaceClusterError(RuntimeError):
    """人脸聚类失败（数据异常 / 规模超限等）。"""


class _UnionFind:
    """并查集：把相似近邻对连通成聚类组。"""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _is_hnswlib_available() -> bool:
    """hnswlib 是否已安装（主路径依赖；未安装时走降级路径）。"""
    try:
        import hnswlib  # noqa: F401

        return True
    except ImportError:
        return False


async def _load_unmatched_faces(db: AsyncSession) -> tuple[list[int], np.ndarray]:
    """加载全部未匹配人脸（排除已确认/已排除/空向量/脏维度），返回 (ids, embeddings)。

    - 未匹配定义：matched_blogger_id/model_id 均空；
    - 排除人工「不匹配」（match_excluded）与已确认（match_status=confirmed）；
    - 过滤非 512 维脏数据（实测真实库存在异常维度，直接丢弃并在日志记录）。
    """
    rows = (
        await db.execute(
            select(InspirationFaceDetection.id, InspirationFaceDetection.embedding).where(
                InspirationFaceDetection.embedding != b"",
                InspirationFaceDetection.match_excluded.is_(False),
                InspirationFaceDetection.matched_blogger_id.is_(None),
                InspirationFaceDetection.matched_model_id.is_(None),
            )
        )
    ).all()
    ids: list[int] = []
    valid: list[np.ndarray] = []
    dropped = 0
    for det_id, blob in rows:
        emb = np.frombuffer(blob, dtype=np.float32)
        if emb.shape[0] != FACE_DIM:
            dropped += 1
            continue
        ids.append(det_id)
        valid.append(emb)
    if dropped:
        logger.warning("人脸聚类跳过 %d 条非 %d 维脏数据", dropped, FACE_DIM)
    if not valid:
        return [], np.empty((0, FACE_DIM), dtype=np.float32)
    embs = np.stack(valid).astype(np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs = embs / norms  # 归一化：余弦 = 点积
    return ids, embs


def _cluster_from_edges(
    n: int,
    edges: list[tuple[int, int, float]],
    min_group_size: int,
) -> list[dict]:
    """按阈值过滤后的近邻边做并查集连通，产出分组。

    返回 [{group_id, size, detection_ids}]，仅含 size ≥ min_group_size 的组。
    """
    uf = _UnionFind(n)
    for a, b, _score in edges:
        uf.union(a, b)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = uf.find(i)
        groups.setdefault(root, []).append(i)

    out = []
    for members in groups.values():
        if len(members) < min_group_size:
            continue
        out.append(
            {
                "size": len(members),
                "detection_ids": members,
            }
        )
    out.sort(key=lambda g: g["size"], reverse=True)
    return out


def _cluster_hnsw(embs: np.ndarray, threshold: float, k: int) -> list[tuple[int, int, float]]:
    """hnswlib 主路径：每张脸查 Top-K 近邻，产出过滤后的近邻边 (a, b, score)。"""
    import hnswlib

    n, dim = embs.shape
    index = hnswlib.Index(space="cosine", dim=dim)
    index.init_index(max_elements=n, ef_construction=HNSW_EF_CONSTRUCTION, M=HNSW_M)
    index.add_items(embs)
    index.set_ef(HNSW_EF_SEARCH)
    labels, distances = index.knn_query(embs, k=min(k + 1, n))
    # hnswlib cosine 距离 = 1 - 余弦相似度
    edges: list[tuple[int, int, float]] = []
    for i in range(n):
        for j in range(labels.shape[1]):
            nb = int(labels[i][j])
            score = 1.0 - float(distances[i][j])
            if nb == i or score < threshold:
                continue
            edges.append((i, nb, score))
    return edges


def _cluster_o2(embs: np.ndarray, threshold: float, k: int) -> list[tuple[int, int, float]]:
    """降级路径：分块 O(n²) 矩阵取每行 Top-K（仅小规模 / hnswlib 未安装时）。"""
    n = embs.shape[0]
    if n > O2_MAX_FACES:
        raise FaceClusterError(
            f"未匹配人脸规模 {n} 超过降级路径上限 {O2_MAX_FACES}，"
            "请安装 hnswlib 以获得 O(n log n) 高性能聚类（pip install hnswlib）"
        )
    # 分块计算相似度矩阵，避免一次性申请 n×n 大矩阵的峰值内存
    edges: list[tuple[int, int, float]] = []
    chunk = 2000
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        block = embs[start:end] @ embs.T  # (chunk, n)
        # 每行取 top-k（排除自身与对角线）
        top_idx = np.argpartition(-block, k + 1, axis=1)[:, : k + 1]
        for local_i, row_idx in enumerate(top_idx):
            i = start + local_i
            for nb in row_idx:
                nb = int(nb)
                if nb == i:
                    continue
                score = float(block[local_i, nb])
                if score < threshold:
                    continue
                edges.append((i, nb, score))
    return edges


async def cluster_unmatched_faces(
    db: AsyncSession,
    threshold: float = CLUSTER_THRESHOLD,
    min_group_size: int = MIN_GROUP_SIZE,
) -> dict:
    """对全部未匹配人脸执行聚合聚类，返回分组统计与结果摘要。

    返回:
        {
          "total_faces": 参与聚类的人脸数,
          "method": "hnsw" | "o2"（实际使用的聚类路径）,
          "groups": [{size, detection_ids}],  # 仅 size ≥ min_group_size
          "group_count": 组数,
          "clustered_faces": 进入组的人脸数（含孤脸）,
          "singletons": 孤脸数（未进入任何组）,
          "threshold": 阈值,
        }
    """
    ids, embs = await _load_unmatched_faces(db)
    n = embs.shape[0]
    if n == 0:
        return {
            "total_faces": 0,
            "method": "none",
            "groups": [],
            "group_count": 0,
            "clustered_faces": 0,
            "singletons": 0,
            "threshold": threshold,
        }

    if _is_hnswlib_available():
        edges = _cluster_hnsw(embs, threshold, ANN_K)
        method = "hnsw"
    else:
        edges = _cluster_o2(embs, threshold, ANN_K)
        method = "o2"
        logger.warning("hnswlib 未安装，人脸聚类降级为 O(n²) 分块矩阵（仅适合小规模）")

    groups = _cluster_from_edges(n, edges, min_group_size)
    in_groups = sum(g["size"] for g in groups)
    # 输出时把组内索引映射回 detection_id
    groups_out = [
        {
            "size": g["size"],
            "detection_ids": [ids[i] for i in g["detection_ids"]],
        }
        for g in groups
    ]

    return {
        "total_faces": n,
        "method": method,
        "groups": groups_out,
        "group_count": len(groups_out),
        "clustered_faces": in_groups,
        "singletons": n - in_groups,
        "threshold": threshold,
    }


def cluster_faces_from_embeddings(
    ids: list[int],
    embs: np.ndarray,
    threshold: float = CLUSTER_THRESHOLD,
    min_group_size: int = MIN_GROUP_SIZE,
) -> dict:
    """同步版聚类入口（供单元测试直接调用，不经 DB）。

    与 ``cluster_unmatched_faces`` 的纯计算部分等价（加载→聚类→分组）。
    """
    embs = np.asarray(embs, dtype=np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs = embs / norms
    n = embs.shape[0]
    if n == 0:
        return {
            "total_faces": 0,
            "method": "none",
            "groups": [],
            "group_count": 0,
            "clustered_faces": 0,
            "singletons": 0,
            "threshold": threshold,
        }
    if _is_hnswlib_available():
        edges = _cluster_hnsw(embs, threshold, ANN_K)
        method = "hnsw"
    else:
        edges = _cluster_o2(embs, threshold, ANN_K)
        method = "o2"
    groups = _cluster_from_edges(n, edges, min_group_size)
    in_groups = sum(g["size"] for g in groups)
    groups_out = [
        {
            "size": g["size"],
            "detection_ids": [ids[i] for i in g["detection_ids"]],
        }
        for g in groups
    ]
    return {
        "total_faces": n,
        "method": method,
        "groups": groups_out,
        "group_count": len(groups_out),
        "clustered_faces": in_groups,
        "singletons": n - in_groups,
        "threshold": threshold,
    }


async def load_group_detections(
    db: AsyncSession, detection_ids: list[int], page: int = 1, size: int = 50
) -> tuple[list[dict], int]:
    """加载一组人脸的具体明细（含素材路径与缩略图），供分组展开展示。

    返回 (items, total)；items 按 detection_id 排序稳定。
    """
    if not detection_ids:
        return [], 0
    ids = sorted(set(detection_ids))
    total = len(ids)
    start = (page - 1) * size
    chunk = ids[start : start + size]
    if not chunk:
        return [], total
    rows = (
        await db.execute(
            select(
                InspirationFaceDetection.id.label("detection_id"),
                InspirationFaceDetection.inspiration_id,
                InspirationFaceDetection.confidence,
                Inspiration.file_path,
                Inspiration.thumbnail_path,
            )
            .join(Inspiration, Inspiration.id == InspirationFaceDetection.inspiration_id)
            .where(InspirationFaceDetection.id.in_(chunk))
        )
    ).all()
    by_id = {r.detection_id: r for r in rows}
    items = [
        {
            "detection_id": did,
            "inspiration_id": by_id[did].inspiration_id,
            "confidence": round(by_id[did].confidence, 4)
            if by_id[did].confidence is not None
            else None,
            "file_path": by_id[did].file_path,
            "thumbnail_path": by_id[did].thumbnail_path,
        }
        for did in chunk
        if did in by_id
    ]
    return items, total
