"""人物模块回归测试：CRUD、类型区分、素材关联、风格画像、删除。"""


def test_create_and_list_persons(client, create_person):
    blogger = create_person(name="小美", person_type="blogger", platform="xiaohongshu")
    model = create_person(name="Anna", person_type="model")

    lst = client.get("/api/persons").json()
    assert lst["total"] == 2

    # 内容类型筛选（UI 区分的后端支撑）
    r = client.get("/api/persons", params={"person_type": "model"}).json()
    assert r["total"] == 1
    assert r["items"][0]["name"] == "Anna"

    # 搜索
    r = client.get("/api/persons", params={"search": "小美"}).json()
    assert r["total"] == 1


def test_create_person_validation(client):
    """空白名称 422；缺失名称 422。"""
    assert client.post("/api/persons", json={"name": "   "}).status_code == 422
    assert client.post("/api/persons", json={}).status_code == 422


def test_person_detail_with_style_profile(client, create_person, upload):
    """详情含素材数 + 风格画像（标签聚合）。"""
    person = create_person(name="风格博主")
    insp_id = upload().json()["id"]

    # 关联人物
    r = client.post(
        f"/api/inspirations/{insp_id}/persons", json={"person_ids": [person["id"]]}
    )
    assert r.status_code == 200
    assert r.json()["count"] == 1

    # 给素材打标签（画像数据源）
    client.post(
        f"/api/inspirations/{insp_id}/tags",
        json={"names": ["法式", "白色"], "category": "style"},
    )

    detail = client.get(f"/api/persons/{person['id']}").json()
    assert detail["inspiration_count"] == 1
    assert detail["person_type"] == "blogger"
    assert detail["style_profile"]["top_tags"]
    assert {t["name"] for t in detail["style_profile"]["top_tags"]} == {"法式", "白色"}


def test_person_inspirations(client, create_person, upload):
    """人物素材列表端点。"""
    person = create_person(name="素材博主")
    insp_id = upload().json()["id"]
    client.post(
        f"/api/inspirations/{insp_id}/persons", json={"person_ids": [person["id"]]}
    )

    r = client.get(f"/api/persons/{person['id']}/inspirations").json()
    assert r["total"] == 1
    assert r["items"][0]["inspiration_id"] == insp_id


def test_link_person_missing_inspiration_404(client, create_person):
    person = create_person(name="孤儿人物")
    r = client.post(
        "/api/inspirations/no-such-id/persons", json={"person_ids": [person["id"]]}
    )
    assert r.status_code == 404


def test_unlink_person(client, create_person, upload):
    person = create_person(name="解除人物")
    insp_id = upload().json()["id"]
    client.post(
        f"/api/inspirations/{insp_id}/persons", json={"person_ids": [person["id"]]}
    )

    r = client.delete(f"/api/inspirations/{insp_id}/persons/{person['id']}")
    assert r.status_code == 200

    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["persons"] == []


def test_delete_person(client, create_person, upload):
    """删除人物后素材保留，仅解除关联。"""
    person = create_person(name="待删除")
    insp_id = upload().json()["id"]
    client.post(
        f"/api/inspirations/{insp_id}/persons", json={"person_ids": [person["id"]]}
    )

    r = client.delete(f"/api/persons/{person['id']}")
    assert r.status_code == 204

    assert client.get("/api/persons").json()["total"] == 0
    # 素材仍存在
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["id"] == insp_id
    assert detail["persons"] == []


def test_person_suggestions_and_top(client, create_person, upload):
    create_person(name="热门博主")
    r = client.get("/api/persons/suggestions", params={"name": "热门"}).json()
    assert len(r) == 1

    top = client.get("/api/persons/top").json()
    assert len(top) == 1


def test_update_person_clear_nullable(client, create_person):
    """PATCH 显式传 null 可清空可空字段（bio）。"""
    person = create_person(name="更新博主", bio="旧简介")
    r = client.patch(f"/api/persons/{person['id']}", json={"bio": None})
    assert r.status_code == 200
    assert r.json()["bio"] is None
    assert r.json()["name"] == "更新博主"
