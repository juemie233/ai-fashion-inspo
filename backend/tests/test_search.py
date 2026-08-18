"""搜索接口回归测试（关键词 / 标签组合筛选）。"""


def test_keyword_search_by_tag(client, upload):
    """关键词命中标签名。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})

    r = client.get("/api/search", params={"keyword": "法式"}).json()
    assert r["total"] == 1
    assert r["items"][0]["id"] == insp_id


def test_keyword_search_by_author(client, upload):
    """关键词命中作者名。"""
    insp_id = upload(source_author="小美").json()["id"]
    r = client.get("/api/search", params={"keyword": "小美"}).json()
    assert r["total"] == 1
    assert r["items"][0]["id"] == insp_id


def test_include_tags_filter(client, upload):
    """标签组合筛选（include_tags AND）。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    for insp_id in (a, b):
        client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})
    client.post(f"/api/inspirations/{a}/tags", json={"names": ["白色"]})

    r = client.get(
        "/api/search", params={"include_tags": "法式,白色"}
    ).json()
    assert r["total"] == 1
    assert r["items"][0]["id"] == a


def test_source_type_filter(client, upload):
    insp_id = upload(source_type="scraper").json()["id"]
    r = client.get("/api/search", params={"source_type": "scraper"}).json()
    assert r["total"] == 1
    assert r["items"][0]["id"] == insp_id


def test_search_rating_filter_and_sort(client, upload):
    """搜索支持评分筛选（rating_min）与评分排序（rating / rating_asc）。"""
    a = upload(color=(200, 100, 50)).json()["id"]  # 默认 0 分
    b = upload(color=(30, 40, 50)).json()["id"]
    c = upload(color=(90, 10, 200)).json()["id"]
    client.patch(f"/api/inspirations/{b}", json={"rating": 5})
    client.patch(f"/api/inspirations/{c}", json={"rating": 2})

    # rating_min 筛选：>= 2 → b(5)、c(2)；a(0) 排除
    r = client.get("/api/search", params={"rating_min": 2}).json()
    assert r["total"] == 2
    assert {i["id"] for i in r["items"]} == {b, c}

    # 评分降序：b(5) → c(2) → a(0)
    ids = [i["id"] for i in client.get("/api/search", params={"sort": "rating"}).json()["items"]]
    assert ids == [b, c, a]

    # 评分升序：a(0) → c(2) → b(5)
    ids_asc = [
        i["id"] for i in client.get("/api/search", params={"sort": "rating_asc"}).json()["items"]
    ]
    assert ids_asc == [a, c, b]


def test_search_excludes_trashed(client, upload):
    """搜索结果排除已软删除素材。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/trash")
    r = client.get("/api/search", params={}).json()
    assert r["total"] == 0


def test_similar_tag_fallback(client, upload):
    """相似素材推荐：无向量时回退到标签匹配。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    client.post(f"/api/inspirations/{a}/tags", json={"names": ["法式"]})
    client.post(f"/api/inspirations/{b}/tags", json={"names": ["法式"]})

    r = client.get(f"/api/search/similar/{a}")
    assert r.status_code == 200
    data = r.json()
    assert any(s["inspiration"]["id"] == b for s in data["similar"])
