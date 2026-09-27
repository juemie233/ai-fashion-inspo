"""读素材 → 序列化的端点矩阵：数据必须**关系填满**，且素材要充当「候选」。

为什么单独成文件（2026-09-27 的教训）：
「素材 4ee7f266 没有相似推荐」的真因是**候选序列化**时的人物关联懒加载
（`MissingGreenlet` → 接口 500 → 前端静默吞成「暂无相似素材」）。它有两条结构性
漏测通道，本文件专门堵它们：

1. **空关系数据**：老用例都用 `upload` 造的「三无素材」（无标签/无博主/无模特），
   而 `inspiration_to_out` 只在**真的有内层实体**时才会触发懒加载 → 永远绿。
   → 这里统一用 `rich_inspiration` 夹具（标签 + 博主 + 模特都填上）。
2. **空列表假通过**：`test_search.py` 那条「相似推荐链路」用例只上传了一个素材，
   相似列表恒为空，**候选序列化从未被执行**，注释却写着「同样需要关联链预加载」。
   → 这里每条用例都断言「返回的素材里至少有一个带人物关联」，数据不够就红，
   不允许空跑通过。

覆盖的端点（都返回 `InspirationOut`，即都经过 `inspiration_to_out`）：
素材列表 / 素材详情 / 搜索列表 / **相似推荐（候选角色）** / CSV 导出。
其余同类端点（`POST /api/search/vector` 以图搜图、收藏合集明细）走同一套加载器，
未在此覆盖——如需扩展，照下面的 CASES 加一行即可。
"""

import pytest

# 判定「这条素材的对象里带了人物关联」
def _has_person_links(item: dict) -> bool:
    return bool(item.get("bloggers")) or bool(item.get("models"))


def _assert_person_links_serialized(items: list[dict]) -> None:
    """每个素材对象的人物关联都要能序列化出来（而不是抛错或空壳）。"""
    assert items, "端点没有返回任何素材，用例无法证明序列化路径可用"
    linked = [item for item in items if _has_person_links(item)]
    # 关键断言：至少一个素材带人物关联——否则本用例等于在空关系上跑（老用例的坑）
    assert linked, (
        "返回的素材里没有一个是带博主/模特关联的：数据不足，本用例没验证到"
        "「内层实体序列化」这条路径（请检查夹具是否真的挂了人物关联）"
    )
    for item in linked:
        for blogger in item["bloggers"]:
            assert blogger["id"] and blogger["name"]
        for model in item["models"]:
            assert model["id"] and model["name"]


def test_inspiration_list_serializes_person_links(client, rich_inspiration):
    """素材列表：带人物关联的素材能正常序列化。"""
    created = rich_inspiration()
    r = client.get("/api/inspirations")
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    _assert_person_links_serialized(items)
    mine = next(item for item in items if item["id"] == created["id"])
    assert [b["id"] for b in mine["bloggers"]] == [created["blogger_id"]]
    assert [m["id"] for m in mine["models"]] == [created["model_id"]]


def test_inspiration_detail_serializes_person_links(client, rich_inspiration):
    """素材详情：同上。"""
    created = rich_inspiration()
    r = client.get(f"/api/inspirations/{created['id']}")
    assert r.status_code == 200, r.text

    _assert_person_links_serialized([r.json()])
    assert [b["id"] for b in r.json()["bloggers"]] == [created["blogger_id"]]


def test_search_list_serializes_person_links(client, rich_inspiration):
    """搜索列表：同上（搜索走的是 services/inspiration_query 的加载器）。"""
    rich_inspiration()
    r = client.get("/api/search")
    assert r.status_code == 200, r.text
    _assert_person_links_serialized(r.json()["items"])


def test_similar_recommendation_serializes_person_linked_candidate(client, rich_inspiration):
    """相似推荐：**候选**带人物关联时也要能序列化（本次 500 的精确复现点）。

    两个 rich 素材共享同一个标签 → 必然互为候选；源与候选都带博主/模特，
    所以任何一个加载器漏了内层预加载，这里就会 500。
    """
    source = rich_inspiration(color=(200, 30, 30))
    candidate = rich_inspiration(color=(30, 200, 30))

    r = client.get(f"/api/search/similar/{source['id']}")
    assert r.status_code == 200, r.text

    similar = r.json()["similar"]
    assert similar, "相似列表为空 → 本用例没验证到候选序列化（数据不足）"
    items = [entry["inspiration"] for entry in similar]
    _assert_person_links_serialized(items)
    # 候选（另一个 rich 素材）必须出现，且它的人物关联完整
    match = next(item for item in items if item["id"] == candidate["id"])
    assert [b["id"] for b in match["bloggers"]] == [candidate["blogger_id"]]
    assert [m["id"] for m in match["models"]] == [candidate["model_id"]]


def test_admin_export_serializes_person_names(client, rich_inspiration):
    """CSV 导出：关联人物的**名字**也要出现在导出内容里（同一条链式预加载）。"""
    created = rich_inspiration()
    r = client.get("/api/admin/export")
    assert r.status_code == 200, r.text

    text = r.content.decode("utf-8-sig")
    assert created["id"] in text
    assert created["blogger_name"] in text
    assert created["model_name"] in text


@pytest.mark.parametrize("endpoint", ["/api/inspirations", "/api/search", "/api/admin/export"])
def test_read_endpoints_do_not_500_with_rich_data(client, rich_inspiration, endpoint):
    """矩阵兜底：富数据下这些读端点在「两两互相关联」的素材上都不许 500。"""
    rich_inspiration(tag="法式穿搭", tag_category="style")
    rich_inspiration(tag="法式穿搭", tag_category="style")

    r = client.get(endpoint)
    assert r.status_code == 200, r.text
