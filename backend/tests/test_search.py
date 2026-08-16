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


def test_search_excludes_trashed(client, upload):
    """搜索结果排除已软删除素材。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/trash")
    r = client.get("/api/search", params={}).json()
    assert r["total"] == 0
