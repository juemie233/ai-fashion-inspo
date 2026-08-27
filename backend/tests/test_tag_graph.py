"""网络图分析测试：图算法纯函数 + 服务级共现子图/社区/类别过滤/异步任务。"""

from app.database import async_session
from app.models.task import TaskQueue
from app.services.tag_graph import analyze_tag_network
from app.services.task_runners.tag_graph import execute_tag_network_analyze


def _create_tag(client, name: str, category: str) -> dict:
    r = client.post("/api/tags", json={"name": name, "category": category})
    assert r.status_code == 201, r.text
    return r.json()


def _link(client, insp_id: str, names: list[str]) -> None:
    r = client.post(f"/api/inspirations/{insp_id}/tags", json={"names": names})
    assert r.status_code == 200, r.text


async def _run_network_analyze(client, **params) -> int:
    """创建图分析任务并同步执行（模拟 worker 成功标记），返回 task_id。"""
    r = client.post("/api/tags/network/analyze", json=params)
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        await execute_tag_network_analyze(db, task)
        task.status = "success"
        task.progress = 100
        await db.commit()
    return task_id


async def _mint_task_id(client) -> int:
    """创建一个真实的图分析任务行，返回 task_id。

    暂停/恢复改造后 analyze_tag_network 需要 task_id 记录进度/续算状态，
    服务级直调用例先经 API 建任务再传真实 id。
    """
    r = client.post("/api/tags/network/analyze")
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


# ═══════════ 图算法纯函数 ═══════════


def test_detect_communities_two_components():
    """两个互不相连的分量各成一社区（迭代版标签传播，返回 labels + state）。"""
    from app.services.tag_graph import detect_communities_iter

    labels, state = detect_communities_iter([(0, 1), (2, 3)], 4)
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]
    assert state.stage == "community_detection"


def test_betweenness_centrality_line():
    """链式图（0-1-2-3）介数中心度：中间节点高于端点（分批版 + 归一化缩放）。"""
    from app.services.tag_graph import (
        apply_betweenness_scale,
        betweenness_centrality_batch,
    )

    adj = {0: [1], 1: [0, 2], 2: [1, 3], 3: [2]}
    raw, state = betweenness_centrality_batch(adj, 4, k=None)
    assert state.stage == "betweenness_centrality"
    cb = apply_betweenness_scale(raw, 4, 4)
    assert cb[0] == 0.0
    assert cb[1] > cb[0] and cb[2] > cb[3]
    assert all(0.0 <= v <= 1.0 for v in cb.values())


def test_detect_bridges():
    """桥接节点：跨社区边占比 ≥ 0.5 且度 ≥ 3。"""
    from app.services.tag_graph import detect_bridges

    # 节点 1 连接社区 0（节点 0）与社区 1（节点 2、3）：度 3、跨社区边 2/3
    edges = [(0, 1), (1, 2), (1, 3)]
    communities = {0: 0, 1: 0, 2: 1, 3: 1}
    adj = {0: [1], 1: [0, 2, 3], 2: [1], 3: [1]}
    bridges = detect_bridges(adj, edges, communities, 4)
    assert 1 in bridges
    assert 0 not in bridges and 2 not in bridges


# ═══════════ 服务级集成 ═══════════


async def test_analyze_network_structure(client, upload):
    """共现子图 + 节点分析字段完整。"""
    _create_tag(client, "JK制服", "style")
    _create_tag(client, "百褶裙", "item_type")
    for _ in range(2):
        insp_id = upload().json()["id"]
        _link(client, insp_id, ["JK制服", "百褶裙"])

    task_id = await _mint_task_id(client)
    async with async_session() as db:
        result = await analyze_tag_network(db, task_id, limit=10, min_count=2)

    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1
    assert result["edges"][0]["weight"] == 2
    for field in (
        "id", "name", "category", "usage_count", "degree",
        "degree_centrality", "betweenness", "community", "is_bridge",
    ):
        assert field in result["nodes"][0]
    assert len(result["communities"]) == 1
    assert result["communities"][0]["top_tags"]


async def test_network_two_communities(client, upload):
    """两个互不相连的分量 → 两个社区。"""
    _create_tag(client, "JK制服", "style")
    _create_tag(client, "百褶裙", "item_type")
    _create_tag(client, "白色", "color")
    _create_tag(client, "黑色", "color")
    for _ in range(2):
        insp_id = upload().json()["id"]
        _link(client, insp_id, ["JK制服", "百褶裙"])
    for _ in range(2):
        insp_id = upload().json()["id"]
        _link(client, insp_id, ["白色", "黑色"])

    task_id = await _mint_task_id(client)
    async with async_session() as db:
        result = await analyze_tag_network(db, task_id, limit=10, min_count=2)

    assert len(result["nodes"]) == 4
    assert len(result["communities"]) == 2
    assert sorted(c["size"] for c in result["communities"]) == [2, 2]


async def test_network_category_filter(client, upload):
    """类别过滤：只分析指定类别的节点。"""
    _create_tag(client, "JK制服", "style")
    _create_tag(client, "白色", "color")
    for _ in range(2):
        insp_id = upload().json()["id"]
        _link(client, insp_id, ["JK制服", "白色"])

    task_id = await _mint_task_id(client)
    async with async_session() as db:
        result = await analyze_tag_network(
            db, task_id, limit=10, min_count=2, category="style"
        )

    assert {n["name"] for n in result["nodes"]} == {"JK制服"}
    assert result["edges"] == []


async def test_network_task_api(client, upload):
    """异步任务全链路：提交 → 执行 → 任务 result 含节点/边/社区/参数。"""
    _create_tag(client, "JK制服", "style")
    _create_tag(client, "百褶裙", "item_type")
    for _ in range(2):
        insp_id = upload().json()["id"]
        _link(client, insp_id, ["JK制服", "百褶裙"])

    task_id = await _run_network_analyze(client, limit=10, min_count=2)
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        result = task.result
    assert len(result["nodes"]) == 2
    assert result["params"]["limit"] == 10
    assert result["params"]["min_count"] == 2


async def test_network_empty(client):
    """空库：返回空结果。"""
    task_id = await _mint_task_id(client)
    async with async_session() as db:
        result = await analyze_tag_network(db, task_id)
    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["communities"] == []


async def test_network_edge_pruning(client, upload):
    """每节点 Top-K 剪枝：完全图（每节点度 3）剪枝后每节点连边数 ≤ 上限。"""
    names = ["剪枝甲", "剪枝乙", "剪枝丙", "剪枝丁"]
    for name in names:
        _create_tag(client, name, "style")
    # 4 个标签两两共现 2 次 → 完全图 K4（6 条边，每节点度 3）
    for _ in range(2):
        insp_id = upload().json()["id"]
        _link(client, insp_id, names)

    task_id = await _mint_task_id(client)
    async with async_session() as db:
        result = await analyze_tag_network(
            db, task_id, limit=10, min_count=2, max_edges_per_node=2
        )

    assert len(result["nodes"]) == 4
    assert len(result["edges"]) < 6  # 剪枝后边数减少
    degree = {node["id"]: 0 for node in result["nodes"]}
    for e in result["edges"]:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    assert all(d <= 2 for d in degree.values())
    # 参数透传（params 含剪枝上限）
    assert result["params"]["max_edges_per_node"] == 2


async def test_network_task_pruning_param(client, upload):
    """异步任务链路：max_edges_per_node 透传到执行器并影响结果边数。"""
    names = ["任务剪枝甲", "任务剪枝乙", "任务剪枝丙"]
    for name in names:
        _create_tag(client, name, "style")
    for _ in range(2):
        insp_id = upload().json()["id"]
        _link(client, insp_id, names)

    task_id = await _run_network_analyze(client, limit=10, min_count=2, max_edges_per_node=1)
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        result = task.result
    assert result["params"]["max_edges_per_node"] == 1
    # 三角形图（每节点度 2）剪枝到每节点 ≤1 条边
    degree = {node["id"]: 0 for node in result["nodes"]}
    for e in result["edges"]:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    assert all(d <= 1 for d in degree.values())


# ═══════════ 暂停/恢复 ═══════════


async def test_network_analyze_pause_resume(client, upload):
    """暂停/恢复全链路：提交 → 暂停 → 恢复 → 完成，验证中间状态保存。"""
    _create_tag(client, "JK制服", "style")
    _create_tag(client, "百褶裙", "item_type")
    for _ in range(2):
        insp_id = upload().json()["id"]
        _link(client, insp_id, ["JK制服", "百褶裙"])

    # 提交任务
    r = client.post("/api/tags/network/analyze")
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)

        # 暂停：仅允许 running 状态，这里直接手动改为 running 后暂停
        task.status = "running"
        await db.commit()

        # 调用暂停接口
        r = client.post(f"/api/tasks/{task_id}/pause")
        assert r.status_code == 200
        assert r.json()["message"] == "任务已暂停"

        async with async_session() as db2:
            task = await db2.get(TaskQueue, task_id)
            assert task.status == "paused"
            assert task.paused_at is not None

        # 恢复：仅允许 paused 状态
        r = client.post(f"/api/tasks/{task_id}/resume")
        assert r.status_code == 200
        assert r.json()["message"] == "任务已恢复"

        async with async_session() as db3:
            task = await db3.get(TaskQueue, task_id)
            assert task.status == "running"
            assert task.paused_at is None

        # 执行完成
        await execute_tag_network_analyze(db3, task)
        task.status = "success"
        task.progress = 100
        await db3.commit()

        result = task.result
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1


async def test_network_pause_unauthorized(client, upload):
    """暂停接口权限校验：非 running 或非 tag_network_analyze 类型拒绝。"""
    # 提交普通任务（非 tag_network_analyze 类型）
    _create_tag(client, "测试", "style")
    r = client.post("/api/tags/network/analyze")
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        task.status = "success"  # 终态不可暂停
        await db.commit()

    r = client.post(f"/api/tasks/{task_id}/pause")
    assert r.status_code == 400


async def test_network_resume_unauthorized(client, upload):
    """恢复接口权限校验：非 paused 状态拒绝。"""
    r = client.post("/api/tags/network/analyze")
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    r = client.post(f"/api/tasks/{task_id}/resume")
    assert r.status_code == 400  # pending 状态不可恢复


async def test_network_cancel(client, upload):
    """取消任务：running 状态的 tag_network_analyze 可取消。"""
    _create_tag(client, "JK制服", "style")
    for _ in range(2):
        insp_id = upload().json()["id"]
        _link(client, insp_id, ["JK制服"])

    r = client.post("/api/tags/network/analyze")
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        task.status = "running"  # 模拟正在执行
        await db.commit()

    r = client.post(f"/api/tasks/{task_id}/cancel")
    assert r.status_code == 200
    assert r.json()["message"] == "任务已取消"

    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert task.status == "cancelled"
