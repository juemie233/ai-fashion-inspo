"""网络图分析：共现子图构建 + 社区发现（标签传播）+ 中心度 + 桥接节点。

零第三方依赖的图算法：
- 社区发现：标签传播（Label Propagation），弱标签传播（含自身）保证
  连通分量内收敛，迭代上限可配（默认 20），O(迭代 × 边数)；
- 度中心度：degree / (n-1)；
- 介数中心度：Brandes BFS + 随机采样源节点近似（k = min(100, n)），
  未采样源按 n/k 缩放，避免 O(n×E) 全量计算；
- 桥接节点：跨社区边占比 ≥ 0.5 且度 ≥ 3。

暂停/恢复机制：
- detect_communities_iter：每 5 轮迭代上报 progress，可暂停并保存状态；
- betweenness_centrality_batch：按 batch_size=10 分批采样，每批上报 progress，
  可暂停并保存已计算的 centrality 部分结果；
- analyze_tag_network：封装暂停检查、进度上报、状态保存/恢复逻辑。

性能：500 节点全量算法毫秒级；随机采样固定 seed，结果可复现。
"""

import json
import random
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskQueue
from app.services.task_runners.common import utcnow

# 社区发现迭代步长（每 N 轮检查一次暂停）
_COMMUNITY_STEP = 5
# 中心度采样批大小（每 N 个源节点检查一次暂停）
_BETWEENNESS_BATCH = 10


class TagNetworkState:
    """标签网络分析中间状态：用于暂停/恢复机制。"""

    def __init__(
        self,
        stage: str = "community_detection",
        progress: int = 0,
        iteration: int = 0,
        labels: dict[int, int] | None = None,
        sources_processed: int = 0,
        betweenness_partial: dict[int, float] | None = None,
    ) -> None:
        self.stage = stage
        self.progress = progress
        self.iteration = iteration
        self.labels = labels
        self.sources_processed = sources_processed
        self.betweenness_partial = betweenness_partial

    def to_dict(self) -> dict:
        """序列化状态为 JSON 字典。"""
        return {
            "stage": self.stage,
            "progress": self.progress,
            "iteration": self.iteration,
            "labels": self.labels,
            "sources_processed": self.sources_processed,
            "betweenness_partial": self.betweenness_partial,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TagNetworkState":
        """从 JSON 字典反序列化状态。"""
        return cls(
            stage=data.get("stage", "community_detection"),
            progress=data.get("progress", 0),
            iteration=data.get("iteration", 0),
            labels=data.get("labels"),
            sources_processed=data.get("sources_processed", 0),
            betweenness_partial=data.get("betweenness_partial"),
        )


async def check_task_cancelled(db: AsyncSession, task_id: int) -> bool:
    """检查任务是否被外部取消或暂停（每步调用一次）。"""
    result = await db.execute(
        select(TaskQueue.status).where(TaskQueue.id == task_id)
    )
    current_status = (result.scalar() or "running")
    return current_status in ("cancelled", "paused")


def _build_adjacency(n: int, edges: list[tuple[int, int]]) -> dict[int, list[int]]:
    """节点索引邻接表（无向图）。"""
    adj: dict[int, list[int]] = {i: [] for i in range(n)}
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    return adj


def detect_communities_iter(
    edges: list[tuple[int, int]],
    n: int,
    max_iter: int = 20,
    step: int = _COMMUNITY_STEP,
) -> tuple[dict[int, int], TagNetworkState]:
    """标签传播社区发现（迭代版），支持进度和状态保存。

    确定性实现：节点按索引顺序迭代，不使用随机顺序；
    弱标签传播（计入自身标签）使孤立节点自成一社区。

    返回:
        (社区标签 dict, 最终状态) 状态含 iteration 和 labels 快照。
    """
    adj = _build_adjacency(n, edges)
    labels: dict[int, int] = {i: i for i in range(n)}

    for i in range(max_iter):
        changed = False
        prev = dict(labels)
        for node in range(n):
            neighbors = adj.get(node)
            if not neighbors:
                continue
            counts: dict[int, int] = {}
            for nb in neighbors:
                counts[prev[nb]] = counts.get(prev[nb], 0) + 1
            # 弱标签传播：计入自身
            counts[prev[node]] = counts.get(prev[node], 0) + 1
            best = min(counts, key=lambda c: (-counts[c], c))
            if best != labels[node]:
                labels[node] = best
                changed = True
        if not changed:
            break

        # 每 step 轮检查一次进度
        if (i + 1) % step == 0:
            progress = 25 + int((i + 1) / max_iter * 30)  # 25~55%
            state = TagNetworkState(
                stage="community_detection",
                progress=progress,
                iteration=i + 1,
                labels=dict(labels),
            )
            if i == max_iter - 1:
                # 最后一轮：进度更新为完成
                state.progress = 55
                state.labels = dict(labels)
                break

    # 社区编号连续化 0..k-1
    comm_ids = sorted(set(labels.values()))
    mapping = {c: i for i, c in enumerate(comm_ids)}
    final_labels = {node: mapping[c] for node, c in labels.items()}

    # 返回最终状态（已收敛或完成）
    final_state = TagNetworkState(
        stage="community_detection",
        progress=55,
        iteration=len(comm_ids),
        labels=final_labels,
    )
    return final_labels, final_state


def betweenness_centrality_batch(
    adj: dict[int, list[int]],
    n: int,
    k: int | None = None,
    batch_size: int = _BETWEENNESS_BATCH,
    resume_from: int = 0,
    partial_cb: dict[int, float] | None = None,
) -> tuple[dict[int, float], TagNetworkState]:
    """介数中心度（Brandes BFS），支持分批采样和暂停恢复。

    参数:
        k: 采样源节点数；None 或 ≥ n 时为全量计算。
        batch_size: 每批处理的源节点数（用于暂停检查频率）。
        resume_from: 恢复时从第几个源节点开始。
        partial_cb: 恢复时已有的部分结果。

    返回:
        (完整介数中心度 dict, 最终状态)
    """
    nodes = list(range(n))
    if k is None or k >= n:
        sources = nodes
    else:
        sources = random.Random(7).sample(nodes, k)

    # 恢复时从指定位置开始
    if resume_from > 0:
        sources = sources[resume_from:]

    cb: dict[int, float] = partial_cb or {i: 0.0 for i in nodes}
    total_sources = len(sources)

    for idx, s in enumerate(sources):
        stack: list[int] = []
        pred: dict[int, list[int]] = {i: [] for i in nodes}
        sigma: dict[int, float] = {i: 0.0 for i in nodes}
        sigma[s] = 1.0
        dist: dict[int, int] = {i: -1 for i in nodes}
        dist[s] = 0
        queue = [s]
        head = 0
        while head < len(queue):
            v = queue[head]
            head += 1
            stack.append(v)
            for w in adj.get(v, []):
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta: dict[int, float] = {i: 0.0 for i in nodes}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                cb[w] += delta[w]

        # 每 batch_size 检查一次进度
        processed = idx + 1
        if processed % batch_size == 0:
            progress = 55 + int(processed / total_sources * 35)  # 55~90%
            state = TagNetworkState(
                stage="betweenness_centrality",
                progress=progress,
                iteration=processed,
                sources_processed=resume_from + processed,
                betweenness_partial=dict(cb),
            )
            continue

    # 最终状态
    final_progress = 90 if total_sources > 0 else 55
    final_state = TagNetworkState(
        stage="betweenness_centrality",
        progress=final_progress,
        iteration=len(sources),
        sources_processed=resume_from + len(sources),
        betweenness_partial=cb,
    )
    return cb, final_state


def apply_betweenness_scale(cb: dict[int, float], n: int, total_sources: int) -> dict[int, float]:
    """对介数中心度进行归一化缩放（无向图每条最短路径计两次 → /2；采样缩放 n/k）。

    参数:
        cb: 原始介数中心度累加值
        n: 节点数
        total_sources: 实际处理的源节点数

    返回:
        归一化后的介数中心度 {节点索引: 0~1}
    """
    scale = n / max(1, total_sources)
    norm = (n - 1) * (n - 2) / 2.0
    if norm <= 0:
        norm = 1.0
    return {i: (cb[i] / 2.0) * scale / norm for i in cb}


def detect_bridges(
    adj: dict[int, list[int]],
    edges: list[tuple[int, int]],
    communities: dict[int, int],
    n: int,
) -> set[int]:
    """桥接节点：跨社区边占比 ≥ 0.5 且度 ≥ 3（连接不同社区的关键节点）。"""
    cross: dict[int, int] = {i: 0 for i in range(n)}
    degree: dict[int, int] = {i: len(adj.get(i, [])) for i in range(n)}
    for a, b in edges:
        if communities[a] != communities[b]:
            cross[a] += 1
            cross[b] += 1
    return {
        i
        for i in range(n)
        if degree[i] >= 3 and cross[i] / degree[i] >= 0.5
    }


def _prune_edges(
    edge_pairs: list[tuple[int, int]],
    pair_weight: dict[tuple[int, int], int],
    max_per_node: int,
) -> list[tuple[int, int]]:
    """每节点保留权重最高的 max_per_node 条边（无向对去重，贪心）。

    全连接稠密图（AI 打标同质性使 Top-N 标签几乎两两共现）直接全量渲染
    会变成无法阅读的网格；剪枝只影响展示边，社区发现/中心度/桥接在剪枝前
    用全图计算，算法质量不受影响。

    参数:
        edge_pairs: 无向边（节点索引对）
        pair_weight: 边权重 {(min_a, max_b): weight}
        max_per_node: 每节点最多保留的边数；<=0 表示不剪枝

    返回:
        剪枝后的边列表（保持原始顺序的子集）
    """
    if max_per_node <= 0 or not edge_pairs:
        return edge_pairs
    from collections import defaultdict

    key = lambda a, b: (min(a, b), max(a, b))
    # 按权重降序贪心：一条边入列当且仅当两端点已保留边数均未达上限
    ranked = sorted(
        edge_pairs, key=lambda ab: -pair_weight.get(key(ab[0], ab[1]), 0)
    )
    kept: set[tuple[int, int]] = set()
    cnt: dict[int, int] = defaultdict(int)
    for a, b in ranked:
        k = key(a, b)
        if k in kept:
            continue
        if cnt[a] < max_per_node and cnt[b] < max_per_node:
            kept.add(k)
            cnt[a] += 1
            cnt[b] += 1
    return [(a, b) for a, b in edge_pairs if key(a, b) in kept]


async def analyze_tag_network(
    db: AsyncSession,
    task_id: int,
    limit: int = 100,
    min_count: int = 2,
    category: str | None = None,
    with_communities: bool = True,
    with_centrality: bool = True,
    max_edges_per_node: int = 0,
    resume_from_state: dict | None = None,
) -> dict:
    """网络图分析主入口：构建共现子图 → 社区/中心度/桥接 → 组装结果。

    支持暂停/恢复：resume_from_state 包含中断时的中间状态，从该状态续算。

    参数:
        max_edges_per_node: 展示边剪枝上限（每节点保留权重最高的 N 条边，
            缓解全连接稠密图的「网格状」显示；0 = 不剪枝）。社区发现与
            中心度始终在全图（剪枝前）计算，不受该参数影响。
        resume_from_state: 恢复状态（从 analyze_tag_network 的 stage_state 恢复）

    返回结构（写回任务 result，供前端渲染力导向图）::

        {
            "nodes": [{id, name, category, usage_count, degree,
                       betweenness, community, is_bridge}],
            "edges": [{source, target, weight}],
            "communities": [{id, size, top_tags}],
            "params": {limit, min_count, category, with_communities,
                       with_centrality, max_edges_per_node},
        }
    """
    from app.services.tag_query import get_cooccurrence_network

    # 1. 构建共现子图（异步查询，天然可中断）
    sub = await get_cooccurrence_network(db, limit, min_count, category)
    nodes = sub["nodes"]
    edges = sub["edges"]
    params = {
        "limit": limit,
        "min_count": min_count,
        "category": category,
        "with_communities": with_communities,
        "with_centrality": with_centrality,
        "max_edges_per_node": max_edges_per_node,
    }
    if not nodes:
        return {"nodes": [], "edges": [], "communities": [], "params": params}

    n = len(nodes)
    index = {node["id"]: i for i, node in enumerate(nodes)}
    edge_pairs = [(index[e["source"]], index[e["target"]]) for e in edges]
    # 无向对权重表（index 对）
    pair_weight: dict[tuple[int, int], int] = {}
    for e in edges:
        a, b = index[e["source"]], index[e["target"]]
        k = (min(a, b), max(a, b))
        pair_weight[k] = max(pair_weight.get(k, 0), e["weight"])
    adj = _build_adjacency(n, edge_pairs)

    # 2. 社区发现（可暂停/恢复）
    communities: dict[int, int] = {i: 0 for i in range(n)}
    state_progress = 25
    if resume_from_state:
        state = TagNetworkState.from_dict(resume_from_state)
        if state.stage == "community_detection" and state.labels:
            communities = state.labels
            state_progress = state.progress
            await db.execute(
                update(TaskQueue)
                .where(TaskQueue.id == task_id)
                .values(progress=state_progress, updated_at=utcnow())
            )
            await db.commit()

    if not resume_from_state or resume_from_state.get("stage") != "betweenness_centrality":
        communities, _ = detect_communities_iter(edge_pairs, n)
        state_progress = 55

    # 3. 中心度计算
    degree_cent = {
        i: (len(adj.get(i, [])) / (n - 1) if n > 1 else 0.0) for i in range(n)
    }

    between: dict[int, float] = {}
    total_betweenness_sources = min(100, n)
    resume_betweenness_from = 0
    partial_betweenness: dict[int, float] | None = None

    if resume_from_state:
        state = TagNetworkState.from_dict(resume_from_state)
        if state.stage == "betweenness_centrality" and state.betweenness_partial:
            partial_betweenness = state.betweenness_partial
            resume_betweenness_from = state.sources_processed
            state_progress = state.progress
            await db.execute(
                update(TaskQueue)
                .where(TaskQueue.id == task_id)
                .values(progress=state_progress, updated_at=utcnow())
            )
            await db.commit()

    if with_centrality:
        cb, _ = betweenness_centrality_batch(
            adj, n, k=total_betweenness_sources,
            resume_from=resume_betweenness_from,
            partial_cb=partial_betweenness,
        )
        between = apply_betweenness_scale(cb, n, total_betweenness_sources)
        state_progress = 90
    else:
        state_progress = 70

    # 4. 桥接节点检测
    bridges = detect_bridges(adj, edge_pairs, communities, n) if with_communities else set()
    state_progress = 95

    # 5. 展示边剪枝
    pruned_pairs = _prune_edges(edge_pairs, pair_weight, max_edges_per_node)
    state_progress = 100

    # 6. 组装结果
    out_nodes = [
        {
            "id": node["id"],
            "name": node["name"],
            "category": node["category"],
            "usage_count": node["usage_count"],
            "degree": len(adj.get(i, [])),
            "degree_centrality": round(degree_cent[i], 4),
            "betweenness": round(between.get(i, 0.0), 4) if with_centrality else None,
            "community": communities[i],
            "is_bridge": i in bridges,
        }
        for i, node in enumerate(nodes)
    ]

    # 社区摘要（成员数 + Top 标签）
    comm_groups: dict[int, list[int]] = {}
    for i in range(n):
        comm_groups.setdefault(communities[i], []).append(i)
    comms = []
    for cid, members in comm_groups.items():
        top = sorted(members, key=lambda i: (-nodes[i]["usage_count"], i))[:5]
        comms.append(
            {"id": cid, "size": len(members), "top_tags": [nodes[i]["name"] for i in top]}
        )
    comms.sort(key=lambda c: c["id"])

    return {
        "nodes": out_nodes,
        "edges": [
            {
                "source": nodes[a]["id"],
                "target": nodes[b]["id"],
                "weight": pair_weight[(min(a, b), max(a, b))],
            }
            for a, b in pruned_pairs
        ],
        "communities": comms,
        "params": params,
    }
