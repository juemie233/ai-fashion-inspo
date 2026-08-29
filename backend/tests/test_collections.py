"""收藏合集测试：手动合集成员管理、排序、智能合集动态求值与固化。

覆盖 docs/收藏合集设计方案.md 验收标准：
多合集归属、批量加入去重、素材物理删除级联出合集、删除合集不影响素材、
重名 409、智能合集动态求值、垃圾桶隐藏与恢复重现、solidify 固化、
智能合集调用加入/排序接口 400、排序提交。
"""

import sqlite3

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def clean_collections(clean_state):
    """清理合集相关表（conftest 的 _ALL_TABLES 尚未包含，避免跨用例残留）。"""
    db_path = settings.storage_root.parent / "fashion_inspo.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DELETE FROM collection_items")
        conn.execute("DELETE FROM collections")
        conn.commit()
    finally:
        conn.close()


def create_collection(client, name="测试合集", **kwargs):
    """创建合集并返回响应 JSON。"""
    body = {"name": name, **kwargs}
    r = client.post("/api/collections", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def get_content(client, collection_id, **params):
    """获取合集内容（返回 items ID 列表与 total）。"""
    r = client.get(f"/api/collections/{collection_id}/inspirations", params=params)
    assert r.status_code == 200, r.text
    data = r.json()
    return [i["id"] for i in data["items"]], data["total"]


def tag_id(client, name):
    """从分组标签列表中按名称查找标签 ID。"""
    for group in client.get("/api/tags").json():
        for tag in group["tags"]:
            if tag["name"] == name:
                return tag["id"]
    raise AssertionError(f"标签 {name} 未找到")


# ── 手动合集：成员管理 ──


def test_multi_collection_membership(client, upload):
    """同一素材可同时属于多个手动合集；列表接口返回 kind 与 item_count。"""
    insp_id = upload().json()["id"]
    c1 = create_collection(client, "通勤穿搭")
    c2 = create_collection(client, "约会穿搭")

    for c in (c1, c2):
        r = client.post(f"/api/collections/{c['id']}/inspirations",
                        json={"inspiration_ids": [insp_id]})
        assert r.status_code == 200, r.text
        assert r.json()["added"] == 1

    for c in (c1, c2):
        ids, total = get_content(client, c["id"])
        assert ids == [insp_id]
        assert total == 1

    listed = client.get("/api/collections").json()
    assert {c["name"]: c for c in listed}["通勤穿搭"]["item_count"] == 1
    entry = [c for c in listed if c["id"] == c1["id"]][0]
    assert entry["kind"] == "manual"
    assert entry["query_json"] is None
    # 未手动指定封面时取「加入最早」的成员
    assert entry["cover_inspiration_id"] == insp_id
    assert entry["cover_thumbnail_path"] is not None


def test_batch_add_dedup(client, upload):
    """批量加入去重：请求内重复去重、重复加入跳过、不存在的素材计入 not_found。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    c = create_collection(client, "去重合集")

    r = client.post(f"/api/collections/{c['id']}/inspirations",
                    json={"inspiration_ids": [a, a, b]})
    assert r.json() == {"added": 2, "skipped": 0, "not_found": 0}

    # 重复加入全部跳过，不存在的素材计入 not_found
    r = client.post(f"/api/collections/{c['id']}/inspirations",
                    json={"inspiration_ids": [a, "nonexistent-id"]})
    assert r.json() == {"added": 0, "skipped": 2, "not_found": 1}

    ids, total = get_content(client, c["id"])
    assert sorted(ids) == [a, b]
    assert total == 2


def test_physical_delete_cascades_out_of_collection(client, upload):
    """素材物理删除后自动出合集（外键级联），其余成员不受影响。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    c = create_collection(client, "级联合集")
    client.post(f"/api/collections/{c['id']}/inspirations",
                json={"inspiration_ids": [a, b]})

    # 物理删除前必须先移入垃圾桶
    r = client.delete(f"/api/inspirations/{a}")
    assert r.status_code == 409
    client.post(f"/api/inspirations/{a}/trash", json={"reason": "其他"})
    r = client.delete(f"/api/inspirations/{a}")
    assert r.status_code == 204, r.text

    ids, total = get_content(client, c["id"])
    assert ids == [b]
    assert total == 1


def test_delete_collection_keeps_inspirations(client, upload):
    """删除合集不影响素材（素材仍在素材库中）。"""
    a = upload().json()["id"]
    c = create_collection(client, "临别合集")
    client.post(f"/api/collections/{c['id']}/inspirations",
                json={"inspiration_ids": [a]})

    r = client.delete(f"/api/collections/{c['id']}")
    assert r.status_code == 204

    assert client.get(f"/api/collections/{c['id']}/inspirations").status_code == 404
    assert client.get("/api/inspirations").json()["total"] == 1


def test_duplicate_name_conflict(client):
    """重名创建/改名均返回 409。"""
    create_collection(client, "重名合集")
    r = client.post("/api/collections", json={"name": "重名合集"})
    assert r.status_code == 409

    c2 = create_collection(client, "另一个合集")
    r = client.patch(f"/api/collections/{c2['id']}", json={"name": "重名合集"})
    assert r.status_code == 409


def test_name_length_validation(client):
    """name 越界返回 422。"""
    assert client.post("/api/collections", json={"name": ""}).status_code == 422
    assert client.post("/api/collections", json={"name": "x" * 51}).status_code == 422


# ── 排序 ──


def test_collection_items_reorder(client, upload):
    """合集内成员拖拽排序：按 ordered_ids 重排，未提交的成员追加到末尾。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    c = upload().json()["id"]
    col = create_collection(client, "排序合集")
    client.post(f"/api/collections/{col['id']}/inspirations",
                json={"inspiration_ids": [a, b, c]})

    # 提交 c, a（b 未提交，按原顺序追加到末尾）
    r = client.patch(f"/api/collections/{col['id']}/items/order",
                     json={"ordered_ids": [c, a]})
    assert r.status_code == 200
    ids, _ = get_content(client, col["id"])
    assert ids == [c, a, b]

    # 包含不属于该合集的素材 → 400
    r = client.patch(f"/api/collections/{col['id']}/items/order",
                     json={"ordered_ids": [a, "foreign-id"]})
    assert r.status_code == 400


def test_collections_reorder(client):
    """合集列表拖拽排序：按 ordered_ids 重排 position，未提交的追加到末尾。"""
    c1 = create_collection(client, "甲")
    c2 = create_collection(client, "乙")
    c3 = create_collection(client, "丙")

    r = client.patch("/api/collections/order", json={"ordered_ids": [c3["id"], c1["id"]]})
    assert r.status_code == 200

    listed = client.get("/api/collections").json()
    assert [c["id"] for c in listed] == [c3["id"], c1["id"], c2["id"]]
    assert [c["position"] for c in listed] == [0, 1, 2]


# ── 垃圾桶：隐藏与恢复重现 ──


def test_trashed_inspiration_hidden_and_restored(client, upload):
    """垃圾桶素材在手动合集中隐藏，恢复后重现。"""
    insp_id = upload().json()["id"]
    col = create_collection(client, "隐藏合集")
    client.post(f"/api/collections/{col['id']}/inspirations",
                json={"inspiration_ids": [insp_id]})

    client.post(f"/api/inspirations/{insp_id}/trash", json={"reason": "质量差"})
    ids, total = get_content(client, col["id"])
    assert ids == [] and total == 0

    client.post(f"/api/inspirations/{insp_id}/restore")
    ids, total = get_content(client, col["id"])
    assert ids == [insp_id] and total == 1


def test_trashed_inspiration_hidden_in_smart_collection(client, upload):
    """智能合集同样排除垃圾桶素材，恢复后自动重现。"""
    insp_id = upload().json()["id"]
    client.patch(f"/api/inspirations/{insp_id}", json={"is_favorite": True})
    col = create_collection(client, "星标精选", query_json={"is_favorite": True})
    assert col["kind"] == "smart"

    ids, total = get_content(client, col["id"])
    assert ids == [insp_id] and total == 1

    client.post(f"/api/inspirations/{insp_id}/trash", json={"reason": "重复"})
    assert get_content(client, col["id"]) == ([], 0)

    client.post(f"/api/inspirations/{insp_id}/restore")
    ids, total = get_content(client, col["id"])
    assert ids == [insp_id] and total == 1


# ── 智能合集：动态求值 / 更新条件 / 固化 ──


def test_smart_collection_dynamic_evaluation(client, upload):
    """智能合集随素材库动态变化：新加入匹配素材自动入合集，取消匹配自动出。"""
    col = create_collection(
        client, "白色衬衫精选",
        query_json={"keyword": "白色", "is_favorite": True, "min_rating": 3},
    )
    assert get_content(client, col["id"]) == ([], 0)

    # 关键词命中（标签名）但未收藏 → 不入合集
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["白色衬衫"]})
    assert get_content(client, col["id"]) == ([], 0)

    # 收藏 + 评分达标 → 自动入合集
    client.patch(f"/api/inspirations/{insp_id}", json={"is_favorite": True, "rating": 4})
    ids, total = get_content(client, col["id"])
    assert ids == [insp_id] and total == 1

    # 取消收藏 → 自动出合集
    client.patch(f"/api/inspirations/{insp_id}", json={"is_favorite": False})
    assert get_content(client, col["id"]) == ([], 0)


def test_smart_collection_tag_filters(client, upload):
    """智能合集标签条件：tag_mode 为 or 时并集、and 时交集。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    client.post(f"/api/inspirations/{a}/tags", json={"names": ["红色"]})
    client.post(f"/api/inspirations/{b}/tags", json={"names": ["长裙"]})
    red, dress = tag_id(client, "红色"), tag_id(client, "长裙")

    col_or = create_collection(
        client, "红裙OR", query_json={"tag_ids": [red, dress], "tag_mode": "or"}
    )
    ids, total = get_content(client, col_or["id"])
    assert sorted(ids) == [a, b] and total == 2

    col_and = create_collection(
        client, "红裙AND", query_json={"tag_ids": [red, dress], "tag_mode": "and"}
    )
    assert get_content(client, col_and["id"]) == ([], 0)


def test_smart_collection_update_query_json(client, upload):
    """智能合集可更新条件（PATCH query_json）；手动合集传 query_json 返回 400。"""
    a = upload().json()["id"]
    col = create_collection(client, "条件演进", query_json={"is_favorite": True})
    assert get_content(client, col["id"]) == ([], 0)

    r = client.patch(f"/api/collections/{col['id']}",
                     json={"query_json": {"is_favorite": False}})
    assert r.status_code == 200
    assert r.json()["query_json"] == {"is_favorite": False}

    ids, total = get_content(client, col["id"])
    assert ids == [a] and total == 1

    # 手动合集不能更新筛选条件
    manual = create_collection(client, "纯手动")
    r = client.patch(f"/api/collections/{manual['id']}", json={"query_json": {}})
    assert r.status_code == 400
    # query_json 置空也不允许（转手动请走 solidify）
    r = client.patch(f"/api/collections/{col['id']}", json={"query_json": None})
    assert r.status_code == 400


def test_smart_collection_member_api_rejected(client, upload):
    """智能合集调用加入/移出/成员排序接口返回 400。"""
    col = create_collection(client, "智能只读", query_json={"is_favorite": True})
    cid = col["id"]
    base = f"/api/collections/{cid}"

    r = client.post(f"{base}/inspirations", json={"inspiration_ids": ["x"]})
    assert r.status_code == 400
    r = client.delete(f"{base}/inspirations", json={"inspiration_ids": ["x"]})
    assert r.status_code == 400
    r = client.patch(f"{base}/items/order", json={"ordered_ids": ["x"]})
    assert r.status_code == 400


def test_solidify_smart_collection(client, upload):
    """固化：当前匹配素材按当前位置写入成员并清空 query_json，之后不再动态变化。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    client.patch(f"/api/inspirations/{a}", json={"is_favorite": True})
    client.patch(f"/api/inspirations/{b}", json={"is_favorite": True})
    col = create_collection(client, "待固化", query_json={"is_favorite": True})
    assert get_content(client, col["id"])[1] == 2

    # 手动合集调用固化 → 400
    manual = create_collection(client, "已是手动")
    assert client.post(f"/api/collections/{manual['id']}/solidify").status_code == 400

    r = client.post(f"/api/collections/{col['id']}/solidify")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["kind"] == "manual"
    assert data["query_json"] is None
    assert data["item_count"] == 2

    # 固化后新匹配素材不再自动入合集
    c = upload().json()["id"]
    client.patch(f"/api/inspirations/{c}", json={"is_favorite": True})
    ids, total = get_content(client, col["id"])
    assert total == 2

    # 固化后可正常使用成员排序接口
    r = client.patch(f"/api/collections/{col['id']}/items/order",
                     json={"ordered_ids": [b, a]})
    assert r.status_code == 200
    ids, _ = get_content(client, col["id"])
    assert ids == [b, a]


def test_update_collection_fields(client, upload):
    """PATCH 更新名称/描述/封面；显式 null 清空手动封面。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    col = create_collection(client, "旧名")
    client.post(f"/api/collections/{col['id']}/inspirations",
                json={"inspiration_ids": [a]})

    r = client.patch(f"/api/collections/{col['id']}",
                     json={"name": "新名", "description": "描述",
                           "cover_inspiration_id": a})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "新名" and data["description"] == "描述"
    assert data["cover_inspiration_id"] == a

    # 不存在的封面素材 → 404
    r = client.patch(f"/api/collections/{col['id']}",
                     json={"cover_inspiration_id": "missing"})
    assert r.status_code == 404

    # 显式置空封面 → 回退到「加入最早」的成员
    r = client.patch(f"/api/collections/{col['id']}", json={"cover_inspiration_id": None})
    assert r.status_code == 200
    assert r.json()["cover_inspiration_id"] == a  # 回退自动封面（唯一成员）

    # 不存在的合集 → 404
    assert client.get("/api/collections/9999/inspirations").status_code == 404
    assert client.patch("/api/collections/9999", json={"name": "x"}).status_code == 404
    assert client.delete("/api/collections/9999").status_code == 404


def test_smart_collection_list_lazily_counts(client, upload):
    """列表接口对智能合集 item_count 返回 null（懒计算），内容页返回精确数。"""
    upload()
    col = create_collection(client, "懒计算", query_json={"is_favorite": False})

    listed = client.get("/api/collections").json()
    entry = [c for c in listed if c["id"] == col["id"]][0]
    assert entry["item_count"] is None
    assert entry["query_json"] == {"is_favorite": False}

    # 内容页动态求值给出精确数
    assert get_content(client, col["id"])[1] == 1


def test_smart_collection_date_and_media_filters(client, upload):
    """智能合集日期与媒体类型条件与素材库同口径生效。"""
    upload()  # 一张图片素材
    col = create_collection(
        client, "视频合集", query_json={"media_type": "video",
                                        "start_date": "2026-01-01",
                                        "end_date": "2026-12-31"}
    )
    assert get_content(client, col["id"]) == ([], 0)

    col2 = create_collection(
        client, "图片合集", query_json={"media_type": "image",
                                        "start_date": "2020-01-01",
                                        "end_date": "2099-12-31"}
    )
    assert get_content(client, col2["id"])[1] == 1
