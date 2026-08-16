"""标签核心链路：创建、分组列表、素材关联、批量操作。"""


def test_create_and_list_tags(client):
    r = client.post("/api/tags", json={"name": "法式", "category": "style"})
    assert r.status_code == 201
    tag = r.json()
    assert tag["name"] == "法式"
    assert tag["category"] == "style"

    groups = client.get("/api/tags").json()
    style_group = next((g for g in groups if g["category"] == "style"), None)
    assert style_group is not None
    assert any(t["name"] == "法式" for t in style_group["tags"])


def test_create_duplicate_tag_conflict(client):
    assert client.post("/api/tags", json={"name": "日系"}).status_code == 201
    r = client.post("/api/tags", json={"name": "日系"})
    assert r.status_code == 409


def test_add_tags_to_inspiration(client, upload):
    """给素材关联标签（按名称查找或创建），并出现在素材详情。"""
    insp_id = upload().json()["id"]

    r = client.post(
        f"/api/inspirations/{insp_id}/tags",
        json={"names": ["法式", "白色"], "category": "style", "source": "manual"},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 2

    detail = client.get(f"/api/inspirations/{insp_id}").json()
    names = {t["tag"]["name"] for t in detail["tags"]}
    assert names == {"法式", "白色"}


def test_add_tags_idempotent(client, upload):
    """重复关联同一标签应跳过（幂等）。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})
    r = client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})
    assert r.json()["count"] == 0

    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert len(detail["tags"]) == 1


def test_remove_tag_from_inspiration(client, upload):
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})
    tag_id = client.get(f"/api/inspirations/{insp_id}").json()["tags"][0]["tag"]["id"]

    r = client.delete(f"/api/inspirations/{insp_id}/tags/{tag_id}")
    assert r.status_code == 200
    assert client.get(f"/api/inspirations/{insp_id}").json()["tags"] == []


def test_tag_inspirations_list(client, upload):
    """按标签查询素材列表（tag_inspirations 端点）。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["日系"]})
    tag = client.get("/api/tags").json()
    flat = [t for g in tag for t in g["tags"]]
    tag_id = next(t["id"] for t in flat if t["name"] == "日系")

    r = client.get(f"/api/tags/{tag_id}/inspirations").json()
    assert r["total"] == 1
    assert r["items"][0]["inspiration_id"] == insp_id


def test_tag_suggestions(client):
    """名称相似标签建议（去重用）："法式" 与 "法式风" 相似度达阈值。"""
    client.post("/api/tags", json={"name": "法式"})
    r = client.get("/api/tags/suggestions/法式风")
    assert r.status_code == 200
    assert any(s["name"] == "法式" for s in r.json())


def test_tag_stats(client, upload):
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})
    stats = client.get("/api/tags/stats").json()
    assert stats["total"] == 1
    assert stats["total_links"] == 1
