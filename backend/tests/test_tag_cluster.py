"""自动聚类测试：候选组生成、应用合并（保留别名）、group_id 解析与错误处理。"""

from app.database import async_session
from app.models.task import TaskQueue
from app.services.tag_cluster import scan_tag_clusters
from app.services.task_runners.tag_cluster import execute_tag_cluster_scan


def _create_tag(client, name: str, category: str = "free") -> dict:
    r = client.post("/api/tags", json={"name": name, "category": category})
    assert r.status_code == 201, r.text
    return r.json()


def _tag_names(client) -> list[str]:
    return [t["name"] for g in client.get("/api/tags").json() for t in g["tags"]]


def _aliases_of(client) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for a in client.get("/api/tags/aliases").json():
        aliases.setdefault(a["tag_name"], []).append(a["alias"])
    return aliases


async def _run_cluster_scan(client, **params) -> int:
    """创建聚类任务并同步执行（模拟 worker 成功标记），返回 task_id。"""
    r = client.post("/api/tags/clusters/scan", json=params)
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        await execute_tag_cluster_scan(db, task)
        task.status = "success"
        task.progress = 100
        await db.commit()
    return task_id


async def test_cluster_scan_finds_group(client, upload):
    """相似名标签聚成一组，建议主标签为使用次数最高者。"""
    a = _create_tag(client, "法式", category="style")
    _create_tag(client, "法式风", category="style")
    _create_tag(client, "法式风格", category="style")
    # 法式使用 2 次 → 建议主标签
    for _ in range(2):
        insp_id = upload().json()["id"]
        client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})

    async with async_session() as db:
        result = await scan_tag_clusters(db, threshold=0.75)

    assert result["total"] == 1
    group = result["groups"][0]
    assert len(group["members"]) == 3
    assert group["suggested_target"]["id"] == a["id"]
    assert group["suggested_target"]["name"] == "法式"
    assert "相似度" in group["reason"]


async def test_cluster_apply_merges_with_alias(client):
    """apply 显式指定 target/source：合并 + 保留源名为别名，历史同批次。"""
    target = _create_tag(client, "目标甲")
    s1 = _create_tag(client, "源甲")
    s2 = _create_tag(client, "源乙")

    r = client.post(
        "/api/tags/clusters/apply",
        json={
            "groups": [
                {
                    "group_id": "g-test",
                    "target_tag_id": target["id"],
                    "source_tag_ids": [s1["id"], s2["id"]],
                    "keep_as_alias": True,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["applied"] == 1
    assert data["merged"] == 2
    assert data["aliases_created"] == 2
    assert not data["errors"]
    assert data["batch_id"]

    names = _tag_names(client)
    assert "目标甲" in names
    assert "源甲" not in names and "源乙" not in names

    # 源名已成为目标的别名
    alias_map = _aliases_of(client)
    assert "源甲" in alias_map.get("目标甲", [])
    assert "源乙" in alias_map.get("目标甲", [])

    # 历史按批次分组：merge + alias_add 同属一个 batch_id
    history = client.get("/api/tags/history", params={"batch_id": data["batch_id"]}).json()
    ops = [h["operation"] for h in history["items"]]
    assert ops.count("merge") == 2
    assert ops.count("alias_add") == 2


async def test_cluster_apply_group_id_resolution(client):
    """apply 只传 group_id：从最近一次聚类扫描结果解析成员。"""
    _create_tag(client, "法式", category="style")
    _create_tag(client, "法式风", category="style")
    await _run_cluster_scan(client, threshold=0.75)

    r = client.post(
        "/api/tags/clusters/apply",
        json={"groups": [{"group_id": "g1", "keep_as_alias": False}]},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["merged"] == 1
    names = _tag_names(client)
    assert "法式" in names and "法式风" not in names


def test_cluster_apply_group_id_without_scan_400(client):
    """没有聚类扫描结果时，仅传 group_id 的 apply 返回 400。"""
    r = client.post(
        "/api/tags/clusters/apply",
        json={"groups": [{"group_id": "g1"}]},
    )
    assert r.status_code == 400


async def test_cluster_apply_missing_source_error(client):
    """源标签不存在：汇总到 errors，不阻断其它组。"""
    target = _create_tag(client, "目标乙")
    r = client.post(
        "/api/tags/clusters/apply",
        json={
            "groups": [
                {
                    "target_tag_id": target["id"],
                    "source_tag_ids": [999999],
                    "keep_as_alias": False,
                }
            ]
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["applied"] == 0
    assert data["merged"] == 0
    assert len(data["errors"]) == 1
    assert "源标签不存在" in data["errors"][0]["message"]


async def test_cluster_scan_empty_db(client):
    """空库聚类：候选组为 0。"""
    async with async_session() as db:
        result = await scan_tag_clusters(db)
    assert result["total"] == 0
    assert result["groups"] == []
