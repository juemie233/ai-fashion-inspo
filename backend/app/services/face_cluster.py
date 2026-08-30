"""未匹配人脸聚合聚类服务：把大量未匹配人脸按「疑似同一人」聚合成组。

性能设计（核心约束：**必须避免 O(n²) 全量矩阵**）：
- 主路径：hnswlib（HNSW 图结构 ANN，近似最近邻）为每张脸查 Top-K 近邻，
  只在近邻之间建边，复杂度 O(n log n)，内存 O(n·d)；
- 降级路径：hnswlib 未安装时回退 numpy 分块矩阵（O(n²)，仅适合小规模，
  超过阈值明确报错提示安装 hnswlib——与项目「向量依赖未装则降级」惯例一致）；
- 聚类：**平均链接合并**（组间平均相似度达标才合并），而非裸并查集传递闭包；
- 数据前置：过滤非 512 维脏 embedding（实测真实库存在 388 条异常维度），
  避免 hnswlib 维度报错；同时排除已确认/人工「不匹配」记录。

为什么不能用「并查集直连」（真实回归教训）：
    并查集把所有达标边做传递闭包，擦边弱边会链式合并不同人——实测
    0.5 阈值下 4068 张脸聚出 2446 张的巨型组，组内两两相似度中位数仅
    0.227（真同一人应在 0.45+），阈值提到 0.65 后该组碎成 1170 块。
    现行三层防线：
    1. 建边阈值 EDGE_THRESHOLD=0.6：两两直连相似度门槛（候选边）；
    2. 平均链接合并：组间平均相似度 ≥ 门槛才合并，单条擦边边拉不动
       整组平均，链式合并在机制上被杜绝；平均相似度用「组和向量」
       O(d) 判定（余弦相似度对求和线性），不引入 O(n²)；
    3. 巨型组保底拆分：仍超上限的组按更高阈值在组内重聚类。

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

# ── 聚类三层防线参数 ──

# 1) 建边阈值：ANN 近邻中两两直连相似度低于该值的边丢弃（候选边门槛）。
#    注意与 face_match_threshold 的语义区别：匹配是一对一比对（人工确认兜底），
#    聚类是成组合并（直接决定分组形态）。0.5 沿用匹配阈值曾导致不同人经
#    擦边弱边链成 2446 张巨型组（见模块 docstring），故提到 0.6。
EDGE_THRESHOLD = 0.6
# 2) 合并门槛（平均链接）：两组合并要求组间平均相似度 ≥ max(MERGE_MEAN_MIN,
#    建边阈值 - MERGE_MEAN_DELTA)。实测不同人簇间平均相似度 ≈0.2~0.3，
#    同一人簇内 ≈0.5+，默认 0.45 居中区分。
MERGE_MEAN_DELTA = 0.15
MERGE_MEAN_MIN = 0.40
# 3) 巨型组保底：平均链接后仍超过该大小的组按更高阈值在组内重聚类拆分；
#    逐级提高仍拆不动则按真实超大簇保留（宁大勿碎，打日志告警）。
MAX_GROUP_SIZE = 500
SPLIT_THRESHOLD_STEP = 0.05
SPLIT_THRESHOLD_MAX = 0.75

# 组内最少人脸数：少于该数的组不展示（孤脸不成组）
MIN_GROUP_SIZE = 2
# 降级路径（O(n²) 分块矩阵）的安全上限：超过则报错提示安装 hnswlib
O2_MAX_FACES = 20_000


class FaceClusterError(RuntimeError):
    """人脸聚类失败（数据异常 / 规模超限等）。"""


def _is_hnswlib_available() -> bool:
    """hnswlib 是否已安装（主路径依赖；未安装时走降级路径）。"""
    try:
        import hnswlib  # noqa: F401

        return True
    except ImportError:
        return False


def _merge_mean_threshold(edge_threshold: float) -> float:
    """由建边阈值推导平均链接合并门槛（建边 - 0.15，下限 0.40）。"""
    return max(MERGE_MEAN_MIN, edge_threshold - MERGE_MEAN_DELTA)


async def _load_unmatched_faces(db: AsyncSession) -> tuple[list[int], np.ndarray]:
    """加载全部未匹配人脸（排除已确认/已排除/空向量/脏维度），返回 (ids, embeddings)。

    - 未匹配定义：matched_blogger_id/model_id 均空；
    - 排除人工「不匹配」（match_excluded）与已确认（match_status=confirmed）；
    - 过滤非 512 维脏数据（实测真实库存在异常维度，直接丢弃并在日志记录）；
    - **按素材去重**：同一素材的多张人脸（同一人的不同检测框/姿态）本质上
      是同一个人，只保留 det_score 最高的一张参与聚类——避免同素材重复脸
      被聚进同一组导致组内出现同一素材的重复项。
    """
    rows = (
        await db.execute(
            select(
                InspirationFaceDetection.id,
                InspirationFaceDetection.inspiration_id,
                InspirationFaceDetection.det_score,
                InspirationFaceDetection.embedding,
            ).where(
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
    # 同素材去重：inspiration_id → (det_score, id, embedding)，保留 det_score 最高者
    best_by_insp: dict[str, tuple[float, int, np.ndarray]] = {}
    for det_id, insp_id, det_score, blob in rows:
        emb = np.frombuffer(blob, dtype=np.float32)
        if emb.shape[0] != FACE_DIM:
            dropped += 1
            continue
        score = det_score if det_score is not None else 0.0
        prev = best_by_insp.get(insp_id)
        if prev is None or score > prev[0]:
            best_by_insp[insp_id] = (score, det_id, emb)
    for _score, det_id, emb in best_by_insp.values():
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


class _AvgLinkageUnion:
    """并查集 + 组和向量：平均链接合并的载体。

    组间平均相似度利用「组和向量」O(d) 计算：mean_cross = (S_A·S_B)/(s_A·s_B)
    （向量已归一化，余弦相似度对求和线性），无需逐对遍历，合并全程 O(E·d)。
    """

    def __init__(self, embs: np.ndarray) -> None:
        self.parent = list(range(len(embs)))
        self.sum = embs.copy()  # 仅根节点持有权威组和向量
        self.size = [1] * len(embs)

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def mean_cross(self, ra: int, rb: int) -> float:
        """两组（根索引）之间的平均余弦相似度。"""
        return float(self.sum[ra] @ self.sum[rb]) / (self.size[ra] * self.size[rb])

    def union(self, ra: int, rb: int) -> None:
        """合并两组（传入根索引；小组并入大组，减少和向量搬移）。"""
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.sum[ra] += self.sum[rb]
        self.size[ra] += self.size[rb]
        self.parent[rb] = ra


def _merge_average_linkage(
    embs: np.ndarray, edges: list[tuple[int, int, float]], merge_mean: float
) -> tuple[_AvgLinkageUnion, int]:
    """平均链接贪心合并：边按相似度降序，合并须组间平均相似度达标。

    与裸并查集的本质区别：单条擦边边（如 0.6x）连接两组时，若两组平均
    相似度低（不同人 ≈0.2~0.3），合并被拒绝——链式合并在机制上被杜绝。

    返回 (union-find 载体, 被平均门槛拒绝的合并尝试次数)。
    """
    uf = _AvgLinkageUnion(embs)
    rejected = 0
    for a, b, _score in sorted(edges, key=lambda e: e[2], reverse=True):
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            continue
        if uf.mean_cross(ra, rb) < merge_mean:
            rejected += 1
            continue
        uf.union(ra, rb)
    return uf, rejected


def _collect_groups(
    uf: _AvgLinkageUnion, n: int, min_group_size: int
) -> list[list[int]]:
    """收集连通组（索引成员列表），仅保留 size ≥ min_group_size 的组。"""
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return [members for members in groups.values() if len(members) >= min_group_size]


def _build_edges(
    embs: np.ndarray, threshold: float, k: int
) -> tuple[list[tuple[int, int, float]], str]:
    """建候选边：hnswlib 主路径 / numpy 分块降级路径。返回 (edges, method)。"""
    if _is_hnswlib_available():
        return _cluster_hnsw(embs, threshold, k), "hnsw"
    return _cluster_o2(embs, threshold, k), "o2"


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
        # 每行取 top-k（排除自身与对角线）；argpartition 的 kth 必须在
        # [0, n-1] 内，人脸数不足 k+1 时截断（小库 / 测试场景，n ≤ k+1）。
        # 此前从未被执行过（本地有 hnswlib、CI 在 import 即崩），n ≤ 31 时
        # kth 越界直接 ValueError——降级路径首个真实 bug。
        kth = min(k + 1, n - 1)
        top_idx = np.argpartition(-block, kth, axis=1)[:, : min(k + 1, n)]
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


def _split_oversized_groups(
    groups: list[list[int]],
    embs: np.ndarray,
    edge_threshold: float,
    min_group_size: int,
    k: int,
    max_group_size: int = MAX_GROUP_SIZE,
) -> tuple[list[list[int]], int]:
    """巨型组保底拆分：超过 max_group_size 的组按递进阈值在组内重聚类。

    平均链接正常不应产出巨型组；若出现（特征空间异常/真实超大簇），
    按 建边阈值+0.05 逐级提高（上限 SPLIT_THRESHOLD_MAX）重跑组内聚类。
    - 拆开后所有子组 ≤ 上限 → 采用拆分结果；
    - 拆到上限阈值仍超限 → 视为真实超大簇保留原组（宁大勿碎），打告警；
    - 更高阈值下整组散架（无任何达标子组）→ 丢弃该组（成员视为孤脸）。

    返回 (最终组列表, 实际拆分的组数)。
    """
    final: list[list[int]] = []
    split_count = 0
    for members in groups:
        if len(members) <= max_group_size:
            final.append(members)
            continue
        sub = embs[members]
        subs: list[list[int]] = []
        t = edge_threshold + SPLIT_THRESHOLD_STEP
        while t <= SPLIT_THRESHOLD_MAX:
            sub_edges, _method = _build_edges(sub, t, k)
            sub_uf, _rejected = _merge_average_linkage(
                sub,
                sub_edges,
                merge_mean=_merge_mean_threshold(t),
            )
            subs = _collect_groups(sub_uf, len(members), min_group_size)
            if not subs or max(len(m) for m in subs) <= max_group_size:
                break
            t += SPLIT_THRESHOLD_STEP
        if subs and max(len(m) for m in subs) <= max_group_size:
            split_count += 1
            final.extend(subs)
            logger.warning(
                "聚类巨型组 %d 张触发保底拆分（阈值 %.2f）→ %d 个子组",
                len(members),
                t,
                len(subs),
            )
        elif subs:
            logger.warning(
                "聚类组 %d 张逐级拆分至阈值 %.2f 仍超上限 %d，按真实超大簇保留",
                len(members),
                t,
                max_group_size,
            )
            final.append(members)
        else:
            logger.warning(
                "聚类组 %d 张在更高阈值下无达标子组，整组丢弃（成员视为孤脸）",
                len(members),
            )
    return final, split_count


def _run_clustering(
    ids: list[int],
    embs: np.ndarray,
    threshold: float,
    min_group_size: int,
    max_group_size: int,
) -> dict:
    """聚类核心（纯计算）：建边 → 平均链接合并 → 巨组保底拆分。

    ids 与 embs 行一一对应（embs 已归一化）。
    """
    n = embs.shape[0]
    merge_mean = _merge_mean_threshold(threshold)
    edges, method = _build_edges(embs, threshold, ANN_K)
    uf, rejected = _merge_average_linkage(embs, edges, merge_mean)
    groups = _collect_groups(uf, n, min_group_size)
    groups, split_count = _split_oversized_groups(
        groups, embs, threshold, min_group_size, ANN_K, max_group_size
    )
    groups.sort(key=len, reverse=True)
    in_groups = sum(len(g) for g in groups)
    groups_out = [
        {
            "size": len(g),
            "detection_ids": [ids[i] for i in g],
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
        # 兼容旧字段：threshold 即建边阈值
        "threshold": threshold,
        "edge_threshold": threshold,
        "merge_mean_threshold": merge_mean,
        "rejected_merges": rejected,
        "split_groups": split_count,
    }


def _empty_result(threshold: float) -> dict:
    """空输入的统一空结果（字段与正常结果一致）。"""
    return {
        "total_faces": 0,
        "method": "none",
        "groups": [],
        "group_count": 0,
        "clustered_faces": 0,
        "singletons": 0,
        "threshold": threshold,
        "edge_threshold": threshold,
        "merge_mean_threshold": _merge_mean_threshold(threshold),
        "rejected_merges": 0,
        "split_groups": 0,
    }


async def cluster_unmatched_faces(
    db: AsyncSession,
    threshold: float = EDGE_THRESHOLD,
    min_group_size: int = MIN_GROUP_SIZE,
    max_group_size: int = MAX_GROUP_SIZE,
) -> dict:
    """对全部未匹配人脸执行聚合聚类，返回分组统计与结果摘要。

    参数:
        threshold: 建边阈值（两两直连相似度门槛；平均链接合并门槛由它推导）
        min_group_size: 组内最少人脸数
        max_group_size: 巨型组保底拆分上限

    返回:
        {
          "total_faces": 参与聚类的人脸数,
          "method": "hnsw" | "o2"（实际使用的建边路径）,
          "groups": [{size, detection_ids}],  # 仅 size ≥ min_group_size
          "group_count": 组数,
          "clustered_faces": 进入组的人脸数（含孤脸）,
          "singletons": 孤脸数（未进入任何组）,
          "threshold" / "edge_threshold": 建边阈值,
          "merge_mean_threshold": 平均链接合并门槛,
          "rejected_merges": 被合并门槛拒绝的合并尝试次数,
          "split_groups": 触发保底拆分的组数,
        }
    """
    ids, embs = await _load_unmatched_faces(db)
    if embs.shape[0] == 0:
        return _empty_result(threshold)
    return _run_clustering(ids, embs, threshold, min_group_size, max_group_size)


def cluster_faces_from_embeddings(
    ids: list[int],
    embs: np.ndarray,
    threshold: float = EDGE_THRESHOLD,
    min_group_size: int = MIN_GROUP_SIZE,
    max_group_size: int = MAX_GROUP_SIZE,
) -> dict:
    """同步版聚类入口（供单元测试直接调用，不经 DB）。

    与 ``cluster_unmatched_faces`` 的纯计算部分等价（归一化→建边→合并→拆分）。
    """
    embs = np.asarray(embs, dtype=np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs = embs / norms
    if embs.shape[0] == 0:
        return _empty_result(threshold)
    return _run_clustering(ids, embs, threshold, min_group_size, max_group_size)


async def load_group_detections(
    db: AsyncSession, detection_ids: list[int], page: int = 1, size: int = 50
) -> tuple[list[dict], int]:
    """加载一组人脸的具体明细（含素材路径与缩略图），供分组展开展示。

    返回 (items, total)；items 按 detection_id 排序稳定。

    注意：只返回未确认的人脸（matched_blogger_id 和 matched_model_id 都为空），
    并排除人工「不匹配」（match_excluded）的人脸。
    """
    if not detection_ids:
        return [], 0
    # 先过滤出所有未确认的人脸
    unmatched_rows = (
        await db.execute(
            select(InspirationFaceDetection.id)
            .where(
                InspirationFaceDetection.id.in_(detection_ids),
                InspirationFaceDetection.matched_blogger_id.is_(None),
                InspirationFaceDetection.matched_model_id.is_(None),
                InspirationFaceDetection.match_excluded.is_(False),
            )
        )
    ).all()
    unmatched_ids = sorted([row[0] for row in unmatched_rows])
    total = len(unmatched_ids)
    start = (page - 1) * size
    chunk = unmatched_ids[start : start + size]
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
