"""人脸聚合聚类测试：算法正确性 + 任务/接口集成（模拟不依赖真实 GPU）。

覆盖：
- 纯算法：构造已知相似向量组，验证聚类分组正确（hnsw / o2 双路径）
- 阈值边界：低于阈值的脸不进组（孤脸）
- 脏数据过滤：非 512 维 embedding 被跳过
- 集成：POST /api/face-scan/cluster/run 创建任务 → 执行 → groups 接口返回
- 接口：未执行聚类时 groups/detections 的 404/空态
"""

import numpy as np
import pytest

from app.services.face_cluster import cluster_faces_from_embeddings


# ═══════════════════════════════════════════════════════════════
#  纯算法（不经 DB，直接调用同步入口）
# ═══════════════════════════════════════════════════════════════


def test_cluster_groups_similar_faces():
    """构造 3 组两两相似的向量，验证分组正确（三组互不混入）。"""
    ids = [1, 2, 3, 4, 5, 6]
    embs = np.array(
        [
            [1.0, 0, 0, 0],
            [0.99, 0.01, 0, 0],
            [0, 1.0, 0, 0],
            [0, 0.98, 0.02, 0],
            [0, 0, 1.0, 0],
            [0, 0, 0.97, 0.03],
        ],
        dtype=np.float32,
    )
    result = cluster_faces_from_embeddings(ids, embs, threshold=0.8)
    assert result["group_count"] == 3
    assert result["clustered_faces"] == 6
    assert result["singletons"] == 0
    groups = {tuple(sorted(g["detection_ids"])) for g in result["groups"]}
    assert groups == {(1, 2), (3, 4), (5, 6)}
    assert result["method"] in ("hnsw", "o2")


def test_cluster_threshold_isolates_singletons():
    """低于阈值的人脸不进任何组（孤脸），clustered_faces 只含成组脸。"""
    ids = [1, 2, 3]
    embs = np.array(
        [
            [1.0, 0, 0, 0],
            [0.99, 0.01, 0, 0],
            [0, 0, 1.0, 0],  # 与前两张正交，相似度 ~0
        ],
        dtype=np.float32,
    )
    result = cluster_faces_from_embeddings(ids, embs, threshold=0.8)
    assert result["group_count"] == 1
    assert result["groups"][0]["detection_ids"] == [1, 2]
    assert result["clustered_faces"] == 2
    assert result["singletons"] == 1


def test_cluster_min_group_size():
    """min_group_size=3 时 2 人组被过滤。"""
    ids = [1, 2, 3, 4]
    embs = np.array(
        [
            [1.0, 0, 0, 0],
            [0.99, 0.01, 0, 0],
            [0.95, 0.05, 0, 0],
            [0, 0, 1.0, 0],
        ],
        dtype=np.float32,
    )
    result = cluster_faces_from_embeddings(ids, embs, threshold=0.8, min_group_size=3)
    assert result["group_count"] == 1
    assert result["groups"][0]["size"] == 3


def test_cluster_empty_input():
    """空输入返回空结果（不报错）。"""
    result = cluster_faces_from_embeddings([], np.empty((0, 4), dtype=np.float32))
    assert result["total_faces"] == 0
    assert result["groups"] == []
    assert result["group_count"] == 0


# ── 链式合并回归（真实案例：0.5 并查集曾聚出 2446 张巨组）──


def _bridge_fixture():
    """构造「两组人 + 桥脸」：桥脸与两组各 ≈0.65 相似（超过 0.6 建边阈值）。

    u/v 为 512 维正交基；w = normalize(0.65u + 0.65v + c·e)，与 u/v 各 0.65
    相似（0.65² + 0.65² + c² = 1）。旧并查集算法会把两组链成一个组；
    平均链接应在跨组平均相似度 ≈0.2 处拒绝合并。
    """
    rng = np.random.default_rng(7)
    u = np.zeros(512, dtype=np.float32)
    u[0] = 1.0
    v = np.zeros(512, dtype=np.float32)
    v[1] = 1.0
    e = np.zeros(512, dtype=np.float32)
    e[2] = 1.0
    c = float(np.sqrt(1 - 2 * 0.65**2))
    w = (0.65 * u + 0.65 * v + c * e).astype(np.float32)
    w /= np.linalg.norm(w)

    def _face(base: np.ndarray) -> np.ndarray:
        f = base + rng.standard_normal(512).astype(np.float32) * 0.01
        return (f / np.linalg.norm(f)).astype(np.float32)

    return u, v, w, _face


def test_cluster_rejects_chained_bridge_merge():
    """桥脸直连边（0.65）不再把两组人链成一个组（平均链接拒绝跨组合并）。

    桥脸并入其中一组后，两组间平均相似度 ≈0.2（大量 u-v 正交对稀释），
    低于合并门槛 0.45——旧并查集算法此处会产出 5 张混合组。
    """
    u, v, w, _face = _bridge_fixture()
    ids = [1, 2, 3, 4, 5]
    embs = np.stack([_face(u), _face(u), _face(v), _face(v), w])

    result = cluster_faces_from_embeddings(ids, embs)  # 默认建边阈值 0.6

    assert result["group_count"] >= 2
    assert max(g["size"] for g in result["groups"]) <= 3
    # 每个组内只含同一「人」的脸（轴 1/轴 2），桥脸(5)不构成跨组桥接
    for g in result["groups"]:
        axes = {1 if i in (1, 2) else 2 for i in g["detection_ids"] if i != 5}
        assert len(axes) <= 1
    assert result["rejected_merges"] >= 1


def test_cluster_split_oversized_group():
    """巨型组保底拆分：合并结果超上限时按递进阈值在组内重聚类。"""
    u, v, w, _face = _bridge_fixture()
    # 4 张 u 轴脸 + 4 张 v 轴脸 + 桥脸：合并阶段产出 {u×4+w} 5 张组，
    # 超 cap=4 → 拆分阈值 0.65/0.70 逐级重聚类 → 桥边断开 → 两个 4 人组
    ids = list(range(1, 10))
    embs = np.stack([_face(u) for _ in range(4)] + [_face(v) for _ in range(4)] + [w])

    result = cluster_faces_from_embeddings(ids, embs, max_group_size=4)

    assert result["split_groups"] == 1
    assert result["group_count"] == 2
    assert sorted(g["size"] for g in result["groups"]) == [4, 4]
    for g in result["groups"]:
        axes = {1 if i in (1, 2, 3, 4) else 2 for i in g["detection_ids"] if i != 9}
        assert len(axes) <= 1


def test_load_unmatched_dedupes_same_inspiration(client, upload):
    """同一素材的多张相似脸只保留一张参与聚类（按素材去重）。

    复现线上问题：一张素材内同一人的多张检测框（高相似度）被聚进同一组，
    导致组内出现同一素材重复。修复后同一素材只取一张代表脸参与聚类。
    """
    from app.database import async_session
    from app.models.face import InspirationFaceDetection
    from app.services.face_cluster import _load_unmatched_faces

    insp_id = upload().json()["id"]

    def _vec(main_axis: int) -> np.ndarray:
        v = np.zeros(512, dtype=np.float32)
        v[main_axis] = 1.0
        return v

    # 同一素材 4 张脸（前 3 张同一人高相似、第 4 张不同人）：
    # 修复前全部参与聚类 → 同素材重复脸会聚进同一组；
    # 修复后按素材去重 → 只保留 det_score 最高的一张参与聚类。
    async def _seed():
        async with async_session() as db:
            for i, vec in enumerate([_vec(0), _vec(0), _vec(0), _vec(1)]):
                db.add(
                    InspirationFaceDetection(
                        inspiration_id=insp_id,
                        face_index=i,
                        embedding=vec.tobytes(),
                        det_score=0.9 - i * 0.1,
                        match_status=None,
                    )
                )
            await db.commit()

    import asyncio

    asyncio.run(_seed())

    async def _load():
        async with async_session() as db:
            return await _load_unmatched_faces(db)

    ids, embs = asyncio.run(_load())
    # 同一素材去重后只剩 1 张（保留 det_score 最高 0.9 的那张）
    assert len(ids) == 1

    async def _check():
        async with async_session() as db:
            row = (
                await db.execute(
                    InspirationFaceDetection.__table__.select().where(
                        InspirationFaceDetection.id.in_(ids)
                    )
                )
            ).first()
            assert row.det_score == 0.9

    asyncio.run(_check())


# ═══════════════════════════════════════════════════════════════
#  集成（TestClient：任务创建 → 执行 → 结果查询）
# ═══════════════════════════════════════════════════════════════


def test_cluster_run_task_and_groups(client, upload, fake_face_db):
    """创建聚类任务 → 手动执行 → groups 接口返回分组与汇总。"""
    # 先造 4 张未匹配人脸（两两相似），跳过真实扫描（fake_face_db 注入）
    _seed_unmatched_detections(client, upload)

    r = client.post("/api/face-scan/cluster/run", json={})
    assert r.status_code == 201
    task_id = r.json()["task_id"]

    # 手动执行任务（模拟 worker 分发）
    from app.database import async_session
    from app.models.task import TaskQueue
    from app.services.task_runners.face_cluster import execute_face_cluster

    async def _run():
        async with async_session() as db:
            task = await db.get(TaskQueue, task_id)
            task.status = "running"
            await db.commit()
            await execute_face_cluster(db, task)
            # 模拟 worker 收尾：执行器只写结果，status 由 worker 统一置 success
            await db.refresh(task)
            task.status = "success"
            task.progress = 100
            await db.commit()

    import asyncio

    asyncio.run(_run())

    # groups 接口返回分组
    r = client.get("/api/face-scan/cluster/groups")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert data["summary"]["total_faces"] >= 4
    first = data["items"][0]
    assert first["size"] >= 2
    assert first["detection_ids"]
    assert first["rep_file_path"] or first["rep_thumbnail_path"]


def test_cluster_groups_before_run(client):
    """未执行过聚类：groups 返回空态而非报错。"""
    r = client.get("/api/face-scan/cluster/groups")
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["summary"] is None


def test_cluster_group_detections_404_before_run(client):
    """未执行过聚类：组明细返回 404。"""
    r = client.get("/api/face-scan/cluster/groups/0/detections")
    assert r.status_code == 404
    assert "尚未执行" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════
#  工具
# ═══════════════════════════════════════════════════════════════


def _seed_unmatched_detections(client, upload, count: int = 4):
    """上传 count 个素材，写入两两相似的未匹配人脸检测记录（embedding 直接落库）。

    向量设计：0/1 相似、2/3 相似、其余正交 → 聚类应产出 ≥1 个 2 人组。
    注意：检测表过滤要求 512 维 embedding，构造 512 维向量。
    """
    from app.database import async_session
    from app.models.face import InspirationFaceDetection

    insp_ids = []
    for _ in range(count):
        insp_ids.append(upload().json()["id"])

    def _vec(main_axis: int, noise: float = 0.01) -> np.ndarray:
        """512 维单位向量：主轴上 1.0，其余加微小噪声（两两相似需主轴对齐）。"""
        v = np.zeros(512, dtype=np.float32)
        v[main_axis] = 1.0
        v = v + np.random.RandomState(main_axis).randn(512).astype(np.float32) * noise
        v /= np.linalg.norm(v)
        return v

    vectors = [
        _vec(0),  # 与 1 相似
        _vec(0),
        _vec(1),  # 与 3 相似
        _vec(1),
    ]

    async def _seed():
        async with async_session() as db:
            for insp_id, vec in zip(insp_ids, vectors):
                db.add(
                    InspirationFaceDetection(
                        inspiration_id=insp_id,
                        face_index=0,
                        embedding=vec.tobytes(),
                        match_status=None,
                    )
                )
            await db.commit()

    import asyncio

    asyncio.run(_seed())


@pytest.fixture
def fake_face_db():
    """占位 fixture：明确聚类集成测试不依赖真实人脸识别服务（无操作）。"""
    yield None
