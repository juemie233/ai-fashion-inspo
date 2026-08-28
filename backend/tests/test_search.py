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


def test_search_with_blogger_and_model_links(client, upload, create_blogger, create_model):
    """回归：素材关联博主/模特后搜索不再 500。

    修复前 search.py 的查询只预加载 tags，inspiration_to_out 访问关联内层
    实体（t.blogger / t.model）触发懒加载，async 下抛 MissingGreenlet（500）；
    仅测试库素材无关联所以此前未暴露，真实库有关联即崩。
    """
    insp_id = upload().json()["id"]
    blogger = create_blogger(name="关联博主")
    model = create_model(name="关联模特")
    assert (
        client.post(
            f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [blogger["id"]]}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/inspirations/{insp_id}/models", json={"person_ids": [model["id"]]}
        ).status_code
        == 200
    )

    # 普通搜索：命中且返回关联的博主/模特简要信息
    r = client.get("/api/search", params={})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    item = r.json()["items"][0]
    assert item["id"] == insp_id
    assert [b["id"] for b in item["bloggers"]] == [blogger["id"]]
    assert [m["id"] for m in item["models"]] == [model["id"]]

    # 相似推荐链路（复用 _load_inspiration，同样需要关联链预加载）
    r2 = client.get(f"/api/search/similar/{insp_id}")
    assert r2.status_code == 200
    assert r2.json()["source"]["id"] == insp_id


# ============ 文本嵌入超长截断重试 ============


async def test_text_embedding_truncates_oversized(monkeypatch):
    """超长文本触发 Ollama context 错误时逐级截断重试，最终成功返回向量。

    回归：此前超长文本直接报 HTTP 500 被记失败，长文本素材的文本向量
    永久缺失（8/27 回填任务 61 条 text_skipped 即此原因）。
    """
    import httpx

    from app.services.vector import embedding as emb

    calls: list[str] = []

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def post(self, url, json=None, **kwargs):
            prompt = json["prompt"]
            calls.append(prompt)
            req = httpx.Request("POST", url)
            if len(prompt) > 300:
                return httpx.Response(
                    500,
                    text='{"error":"the input length exceeds the context length"}',
                    request=req,
                )
            return httpx.Response(200, json={"embedding": [0.1] * 384}, request=req)

    monkeypatch.setattr(emb.httpx, "AsyncClient", _FakeClient)

    vec = await emb.generate_text_embedding("穿" * 2000)
    assert vec is not None
    assert len(calls) >= 2  # 至少截断重试过一次
    assert len(calls[-1]) <= 300  # 最后一次在模型 context 内


async def test_text_embedding_short_text_single_call(monkeypatch):
    """短文本只调用一次 Ollama（不触发截断逻辑）。"""
    import httpx

    from app.services.vector import embedding as emb

    calls: list[str] = []

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def post(self, url, json=None, **kwargs):
            calls.append(json["prompt"])
            req = httpx.Request("POST", url)
            return httpx.Response(200, json={"embedding": [0.1] * 384}, request=req)

    monkeypatch.setattr(emb.httpx, "AsyncClient", _FakeClient)

    vec = await emb.generate_text_embedding("法式穿搭")
    assert vec is not None
    assert len(calls) == 1
