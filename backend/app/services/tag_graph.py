"""网络图分析：共现子图构建 + 社区发现（标签传播）+ 中心度 + 桥接节点。

零第三方依赖的图算法：
- 社区发现：标签传播（Label Propagation），弱标签传播（含自身）保证
  连通分量内收敛，迭代上限可配（默认 20），O(迭代 × 边数)；
- 度中心度：degree / (n-1)；
- 介数中心度：Brandes BFS + 随机采样源节点近似（k = min(100, n)），
  未采样源按 n/k 缩放，避免 O(n×E) 全量计算；
- 桥接节点：跨社区边占比 ≥ 0.5 且度 ≥ 3。

性能：500 节点全量算法毫秒级；随机采样固定 seed，结果可复现。
"""

import random

from sqlalchemy.ext.asyncio import AsyncSession


def _build_adjacency(n: int, edges: list[tuple[int, int]]) -> dict[int, list[int]]:
    """节点索引邻接表（无向图）。"""
    adj: dict[int, list[int]] = {i: [] for i in range(n)}
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    return adj


def detect_communities(
    edges: list[tuple[int, int]], n: int, max_iter: int = 20
) -> dict[int, int]:
    """标签传播社区发现，返回 {节点索引: 社区编号（从 0 连续编号）}。

    确定性实现：节点按索引顺序迭代，不使用随机顺序；
    弱标签传播（计入自身标签）使孤立节点自成一社区。
    """
    adj = _build_adjacency(n, edges)
    labels = {i: i for i in range(n)}

    for _ in range(max_iter):
        changed = False
        prev = dict(labels)  # 使用上一轮快照，避免同轮级联偏置
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

    # 社区编号连续化 0..k-1
    comm_ids = sorted(set(labels.values()))
    mapping = {c: i for i, c in enumerate(comm_ids)}
    return {node: mapping[c] for node, c in labels.items()}


def betweenness_centrality(
    adj: dict[int, list[int]], n: int, k: int | None = None
) -> dict[int, float]:
    """介数中心度（Brandes BFS），返回 {节点索引: 0~1 归一化值}。

    参数:
        k: 采样源节点数；None 或 ≥ n 时为全量计算。默认调用方传 min(100, n)。
    """
    nodes = list(range(n))
    if k is None or k >= n:
        sources = nodes
    else:
        sources = random.Random(7).sample(nodes, k)

    cb = {i: 0.0 for i in nodes}
    for s in sources:
        stack: list[int] = []
        pred: dict[int, list[int]] = {i: [] for i in nodes}
        sigma = {i: 0.0 for i in nodes}
        sigma[s] = 1.0
        dist = {i: -1 for i in nodes}
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
        delta = {i: 0.0 for i in nodes}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                cb[w] += delta[w]

    # 无向图每条最短路径计两次 → /2；采样缩放 n/k；按最大可能值归一化
    scale = n / max(1, len(sources))
    norm = (n - 1) * (n - 2) / 2.0
    if norm <= 0:
        norm = 1.0
    return {i: (cb[i] / 2.0) * scale / norm for i in nodes}


def detect_bridges(
    adj: dict[int, list[int]],
    edges: list[tuple[int, int]],
    communities: dict[int, int],
    n: int,
) -> set[int]:
    """桥接节点：跨社区边占比 ≥ 0.5 且度 ≥ 3（连接不同社区的关键节点）。"""
    cross = {i: 0 for i in range(n)}
    degree = {i: len(adj.get(i, [])) for i in range(n)}
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
    limit: int = 100,
    min_count: int = 2,
    category: str | None = None,
    with_communities: bool = True,
    with_centrality: bool = True,
    max_edges_per_node: int = 0,
) -> dict:
    """网络图分析主入口：构建共现子图 → 社区/中心度/桥接 → 组装结果。

    参数:
        max_edges_per_node: 展示边剪枝上限（每节点保留权重最高的 N 条边，
            缓解全连接稠密图的「网格状」显示；0 = 不剪枝）。社区发现与
            中心度始终在全图（剪枝前）计算，不受该参数影响。

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

    # 社区发现（全图）
    communities = (
        detect_communities(edge_pairs, n) if with_communities else {i: 0 for i in range(n)}
    )
    # 中心度（度中心度恒算，介数中心度可关）
    degree_cent = {
        i: (len(adj.get(i, [])) / (n - 1) if n > 1 else 0.0) for i in range(n)
    }
    between = (
        betweenness_centrality(adj, n, k=min(100, n)) if with_centrality else {}
    )
    # 桥接节点（需要社区划分）
    bridges = detect_bridges(adj, edge_pairs, communities, n) if with_communities else set()

    # 展示边剪枝（不影响上方算法；剪枝后每节点连边数 ≤ max_edges_per_node）
    pruned_pairs = _prune_edges(edge_pairs, pair_weight, max_edges_per_node)

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
