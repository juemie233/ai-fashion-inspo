"""标签层级树测试：懒加载、批量移动、循环检测、历史回滚。"""

from app.database import async_session
from app.services.tag_history_service import rollback_history


def _create_tag(client, name: str, category: str = "free") -> dict:
    r = client.post("/api/tags", json={"name": name, "category": category})
    assert r.status_code == 201, r.text
    return r.json()


async def test_tree_lazy_loading(client):
    """根节点 + 子节点懒加载；has_children 标记正确。"""
    a = _create_tag(client, "树根甲")
    b = _create_tag(client, "树根乙")
    c = _create_tag(client, "树子丙")
    r = client.post("/api/tags/move", json={"moves": [{"tag_id": c["id"], "parent_id": a["id"]}]})
    assert r.json()["moved"] == 1

    roots = client.get("/api/tags/tree").json()
    assert roots["total"] == 2
    root_a = next(i for i in roots["items"] if i["id"] == a["id"])
    assert root_a["has_children"] is True
    root_b = next(i for i in roots["items"] if i["id"] == b["id"])
    assert root_b["has_children"] is False

    children = client.get("/api/tags/tree", params={"parent_id": a["id"]}).json()
    assert children["total"] == 1
    assert children["items"][0]["id"] == c["id"]


async def test_move_writes_history_and_rollback(client):
    """移动写操作历史；回滚恢复原层级。"""
    a = _create_tag(client, "移父甲")
    b = _create_tag(client, "移子乙")
    r = client.post("/api/tags/move", json={"moves": [{"tag_id": b["id"], "parent_id": a["id"]}]})
    assert r.json()["moved"] == 1

    history = client.get("/api/tags/history", params={"operation": "move"}).json()
    assert history["total"] == 1
    row = history["items"][0]
    assert row["before"][str(b["id"])]["parent_id"] is None
    assert row["after"][str(b["id"])]["parent_id"] == a["id"]

    async with async_session() as db:
        await rollback_history(db, row["id"])

    roots = client.get("/api/tags/tree").json()
    assert any(i["id"] == b["id"] for i in roots["items"])


async def test_move_cycle_rejected(client):
    """把祖先移动到自己的后代下 → 循环拒绝。"""
    a = _create_tag(client, "环父甲")
    b = _create_tag(client, "环子乙")
    c = _create_tag(client, "环孙丙")
    client.post("/api/tags/move", json={"moves": [{"tag_id": b["id"], "parent_id": a["id"]}]})
    client.post("/api/tags/move", json={"moves": [{"tag_id": c["id"], "parent_id": b["id"]}]})

    r = client.post("/api/tags/move", json={"moves": [{"tag_id": a["id"], "parent_id": c["id"]}]})
    data = r.json()
    assert data["moved"] == 0
    assert "后代" in data["errors"][0]["message"]


async def test_move_batch_cycle_rejected(client):
    """批内互移（A→B 且 B→A）→ 后一个被拒绝，最终不成环。

    修复前两个移动各自基于旧快照检测都通过，执行后 A、B 互为父子成环；
    修复后环检测沿「批内已计划的 parent 关系」上溯，第二个移动被拒绝。
    """
    a = _create_tag(client, "互移甲")
    b = _create_tag(client, "互移乙")
    r = client.post(
        "/api/tags/move",
        json={"moves": [{"tag_id": a["id"], "parent_id": b["id"]},
                        {"tag_id": b["id"], "parent_id": a["id"]}]},
    )
    data = r.json()
    # 第一个移动（A→B）无环合法执行；第二个（B→A）在已计划关系上成环被拒
    assert data["moved"] == 1
    assert len(data["errors"]) == 1
    assert "后代" in data["errors"][0]["message"]

    # 执行后不成环：B 在根层，A 在 B 下
    roots = client.get("/api/tags/tree").json()
    root_ids = {i["id"] for i in roots["items"]}
    assert b["id"] in root_ids
    assert a["id"] not in root_ids


def test_move_missing_parent_error(client):
    """父标签不存在 → 错误汇总。"""
    a = _create_tag(client, "孤甲")
    r = client.post("/api/tags/move", json={"moves": [{"tag_id": a["id"], "parent_id": 999999}]})
    data = r.json()
    assert data["moved"] == 0
    assert "不存在" in data["errors"][0]["message"]


async def test_move_to_root(client):
    """parent_id=null 移到根。"""
    a = _create_tag(client, "根父甲")
    b = _create_tag(client, "根子乙")
    client.post("/api/tags/move", json={"moves": [{"tag_id": b["id"], "parent_id": a["id"]}]})
    r = client.post("/api/tags/move", json={"moves": [{"tag_id": b["id"], "parent_id": None}]})
    assert r.json()["moved"] == 1

    roots = client.get("/api/tags/tree").json()
    assert any(i["id"] == b["id"] for i in roots["items"])
