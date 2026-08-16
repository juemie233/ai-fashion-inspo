"""标签服务高级功能集成测试：合并/批量编辑/未使用删除/别名/导入导出/排序/统计。"""


def _tag_names(client) -> list[str]:
    """返回库内全部标签名（扁平化）。"""
    return [t["name"] for g in client.get("/api/tags").json() for t in g["tags"]]


def test_merge_tags(client, upload):
    """合并标签：源标签删除、关联转移到目标标签。"""
    s = client.post("/api/tags", json={"name": "法式", "category": "style"}).json()
    t = client.post("/api/tags", json={"name": "法式风", "category": "style"}).json()
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})

    r = client.post("/api/tags/merge", json={"source_tag_id": s["id"], "target_tag_id": t["id"]})
    assert r.status_code == 200

    names = _tag_names(client)
    assert "法式" not in names
    assert "法式风" in names
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert any(x["tag"]["name"] == "法式风" for x in detail["tags"])


def test_merge_self_rejected(client):
    """标签不能合并到自身 → 400。"""
    t = client.post("/api/tags", json={"name": "法式", "category": "style"}).json()
    r = client.post("/api/tags/merge", json={"source_tag_id": t["id"], "target_tag_id": t["id"]})
    assert r.status_code == 400


def test_batch_change_category(client):
    """批量修改标签类别。"""
    a = client.post("/api/tags", json={"name": "法式", "category": "style"}).json()
    b = client.post("/api/tags", json={"name": "日系", "category": "style"}).json()

    r = client.patch("/api/tags/batch-category", json={"tag_ids": [a["id"], b["id"]], "category": "free"})
    assert r.status_code == 200
    assert r.json()["updated"] == 2

    groups = client.get("/api/tags").json()
    free = next((g for g in groups if g["category"] == "free"), None)
    assert free is not None
    assert {t["name"] for t in free["tags"]} == {"法式", "日系"}


def test_batch_rename(client):
    """批量重命名（查找替换）。"""
    a = client.post("/api/tags", json={"name": "白色系", "category": "color"}).json()
    b = client.post("/api/tags", json={"name": "白色连衣裙", "category": "color"}).json()

    r = client.patch("/api/tags/batch-rename", json={"tag_ids": [a["id"], b["id"]], "find": "白色", "replace": "奶白"})
    assert r.status_code == 200
    assert r.json()["updated"] == 2

    names = _tag_names(client)
    assert "奶白系" in names
    assert "奶白连衣裙" in names


def test_delete_unused_tags(client, upload):
    """删除未使用标签，保留已使用标签。"""
    client.post("/api/tags", json={"name": "法式", "category": "style"})
    client.post("/api/tags", json={"name": "孤儿", "category": "free"})
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})

    r = client.delete("/api/tags/unused")
    assert r.status_code == 200
    assert r.json()["count"] >= 1

    names = _tag_names(client)
    assert "孤儿" not in names
    assert "法式" in names


def test_batch_delete_tags(client):
    """批量删除标签。"""
    a = client.post("/api/tags", json={"name": "甲", "category": "free"}).json()
    b = client.post("/api/tags", json={"name": "乙", "category": "free"}).json()

    r = client.post("/api/tags/batch-delete", json={"tag_ids": [a["id"], b["id"]]})
    assert r.status_code == 200
    assert r.json()["count"] == 2

    names = _tag_names(client)
    assert "甲" not in names and "乙" not in names


def test_alias_crud(client):
    """别名：创建 → 列表 → 删除。"""
    tag = client.post("/api/tags", json={"name": "法式", "category": "style"}).json()

    r = client.post(f"/api/tags/{tag['id']}/aliases", json={"alias": "法式风"})
    assert r.status_code == 201
    alias_id = r.json()["id"]

    aliases = client.get("/api/tags/aliases").json()
    assert any(a["alias"] == "法式风" for a in aliases)

    assert client.delete(f"/api/tags/aliases/{alias_id}").status_code == 200
    assert client.delete(f"/api/tags/aliases/{alias_id}").status_code == 404


def test_alias_duplicate_conflict(client):
    """同名别名重复创建 → 409。"""
    a = client.post("/api/tags", json={"name": "法式", "category": "style"}).json()
    b = client.post("/api/tags", json={"name": "日系", "category": "style"}).json()
    assert client.post(f"/api/tags/{a['id']}/aliases", json={"alias": "法式风"}).status_code == 201
    r = client.post(f"/api/tags/{b['id']}/aliases", json={"alias": "法式风"})
    assert r.status_code == 409


def test_import_export_tags(client):
    """导入导出：导出含已有标签；导入跳过已存在、新增缺失。"""
    client.post("/api/tags", json={"name": "法式", "category": "style"})

    exp = client.get("/api/tags/export").json()
    assert any(t["name"] == "法式" for t in exp["tags"])

    r = client.post("/api/tags/import", json={
        "tags": [
            {"name": "法式", "category": "style"},
            {"name": "日系", "category": "style"},
        ]
    })
    assert r.status_code == 200
    data = r.json()
    assert data["imported"] == 1
    assert data["skipped"] == 1

    names = _tag_names(client)
    assert "日系" in names


def test_reorder_tags(client):
    """批量更新自定义排序权重。"""
    a = client.post("/api/tags", json={"name": "甲", "category": "free"}).json()
    b = client.post("/api/tags", json={"name": "乙", "category": "free"}).json()

    r = client.post("/api/tags/reorder", json={
        "items": [{"id": a["id"], "sort_order": 5}, {"id": b["id"], "sort_order": 1}]
    })
    assert r.status_code == 200
    assert r.json()["updated"] == 2


def test_top_tags_and_network(client, upload):
    """热门标签排行与共现网络：两个标签共现于同一素材。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式", "日系"]})

    top = client.get("/api/tags/top").json()
    assert len(top) >= 2
    assert all(t["usage_count"] >= 1 for t in top)

    net = client.get("/api/tags/cooccurrence-network", params={"limit": 10}).json()
    names = {n["name"] for n in net["nodes"]}
    assert {"法式", "日系"} <= names
    assert len(net["edges"]) >= 1


def test_batch_remove_tag_inspirations(client, upload):
    """批量解除标签与多个素材的关联。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    client.post(f"/api/inspirations/{a}/tags", json={"names": ["法式"]})
    client.post(f"/api/inspirations/{b}/tags", json={"names": ["法式"]})

    tag_id = next(t["id"] for t in client.get("/api/tags").json()[0]["tags"] if t["name"] == "法式")

    r = client.post(f"/api/tags/{tag_id}/inspirations/batch-remove", json={"inspiration_ids": [a, b]})
    assert r.status_code == 200
    assert r.json()["removed"] == 2

    detail = client.get(f"/api/inspirations/{a}").json()
    assert detail["tags"] == []


def test_tag_stats_exclude_trash(client, upload):
    """标签素材列表/使用次数/热门排行均排除垃圾桶素材。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})

    tag_id = next(
        t["id"]
        for g in client.get("/api/tags").json()
        for t in g["tags"]
        if t["name"] == "法式"
    )

    # 未删除：素材计入
    assert client.get(f"/api/tags/{tag_id}/inspirations").json()["total"] == 1
    top = client.get("/api/tags/top").json()
    assert any(t["name"] == "法式" and t["usage_count"] == 1 for t in top)

    # 移入垃圾桶：素材不再计入列表与热门排行
    client.post(f"/api/inspirations/{insp_id}/trash")
    assert client.get(f"/api/tags/{tag_id}/inspirations").json()["total"] == 0
    top = client.get("/api/tags/top").json()
    assert not any(t["name"] == "法式" for t in top)
