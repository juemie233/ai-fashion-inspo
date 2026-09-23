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


def delete_json(client, url, payload):
    """TestClient.delete 不支持 json kwarg，统一走 request("DELETE")。"""
    return client.request("DELETE", url, json=payload)


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
    assert r.json() == {"added": 0, "skipped": 1, "not_found": 1}

    ids, total = get_content(client, c["id"])
    assert sorted(ids) == sorted([a, b])
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


# ── 两级收藏夹 ──


async def test_ensure_root_collections_is_idempotent(client):
    """两个一级收藏夹由 ensure_root_collections 幂等保证存在。

    结构（用户口径）：系统入库手动收藏 = 智能合集（is_favorite）；
    抖音入库自动收藏 = 手动合集 + auto_source=douyin（二级挂在它下面）。

    注：测试的 clean_state 每个用例前清空所有表，所以这里显式调用（生产由
    启动钩子 + 迁移负责）。
    """
    from app.database import async_session
    from app.services import collection_service

    async with async_session() as db:
        first = await collection_service.ensure_root_collections(db)
        second = await collection_service.ensure_root_collections(db)
    assert first == second  # 幂等：第二次不新建

    listed = {c["name"]: c for c in client.get("/api/collections").json()}
    manual = listed[collection_service.MANUAL_ROOT_COLLECTION_NAME]
    assert manual["kind"] == "smart"
    assert manual["parent_id"] is None
    assert manual["query_json"] == {"is_favorite": True}

    douyin = listed[collection_service.DOUYIN_ROOT_COLLECTION_NAME]
    assert douyin["kind"] == "manual"
    assert douyin["parent_id"] is None
    assert douyin["auto_source"] == "douyin"
    assert douyin["id"] == first[collection_service.DOUYIN_ROOT_COLLECTION_NAME]


def test_child_collection_and_per_level_name_uniqueness(client, upload):
    """二级收藏夹：挂在父下、同层重名 409、跨层可同名、最多两级。"""
    parent = create_collection(client, "父级")
    child = client.post(
        "/api/collections", json={"name": "穿搭", "parent_id": parent["id"]}
    )
    assert child.status_code == 201, child.text
    child = child.json()
    assert child["parent_id"] == parent["id"]
    assert child["auto_source"] is None

    # 同层重名 → 409；换个父（或一级）同名 → 允许（否则抖音收藏夹名会和用户自建撞死）
    assert (
        client.post(
            "/api/collections", json={"name": "穿搭", "parent_id": parent["id"]}
        ).status_code
        == 409
    )
    assert client.post("/api/collections", json={"name": "穿搭"}).status_code == 201

    # 三级：不能挂在二级之下
    deep = client.post(
        "/api/collections", json={"name": "更深一层", "parent_id": child["id"]}
    )
    assert deep.status_code == 400

    # 不存在的父 → 404
    assert (
        client.post(
            "/api/collections", json={"name": "孤儿", "parent_id": 99999}
        ).status_code
        == 404
    )


def test_level_one_lists_children_right_after_itself(client):
    """列表顺序：一级后面紧跟它的二级（前端按 parent_id 分组即可渲染两级）。"""
    parent = create_collection(client, "带子的父级")
    child = client.post(
        "/api/collections", json={"name": "子夹甲", "parent_id": parent["id"]}
    ).json()

    listed = client.get("/api/collections").json()
    ids = [c["id"] for c in listed]
    assert ids[ids.index(parent["id"]) + 1] == child["id"]
    assert listed[ids.index(parent["id"])]["child_count"] == 1


async def test_auto_collection_cannot_be_renamed(client):
    """自动同步的收藏夹名由抖音侧决定：改名 400（否则下次同步会再建一个）。

    可删除：删掉后如果抖音那边还有这个夹，下一次同步会按需重建。
    """
    from app.database import async_session
    from app.services import collection_service

    async with async_session() as db:
        roots = await collection_service.ensure_root_collections(db)
    douyin_id = roots[collection_service.DOUYIN_ROOT_COLLECTION_NAME]

    r = client.patch(f"/api/collections/{douyin_id}", json={"name": "我改的名字"})
    assert r.status_code == 400
    assert "自动维护" in r.json()["detail"]

    assert client.delete(f"/api/collections/{douyin_id}").status_code == 204
    # 删掉后再 ensure 会重建（自愈）；SQLite 未用 AUTOINCREMENT，id 可能被复用，
    # 所以判「名字回来了」而不是判 id 不同
    async with async_session() as db:
        again = await collection_service.ensure_root_collections(db)
    assert collection_service.DOUYIN_ROOT_COLLECTION_NAME in again
    names = [c["name"] for c in client.get("/api/collections").json()]
    assert names.count(collection_service.DOUYIN_ROOT_COLLECTION_NAME) == 1


def test_deleting_parent_removes_children(client):
    """删父合集：二级一并删除（它们是父的子分类，单独留下没有意义）。"""
    parent = create_collection(client, "将被删的父级")
    child = client.post(
        "/api/collections", json={"name": "跟着走的子夹", "parent_id": parent["id"]}
    ).json()

    assert client.delete(f"/api/collections/{parent['id']}").status_code == 204
    remaining = {c["id"] for c in client.get("/api/collections").json()}
    assert child["id"] not in remaining


async def test_ensure_root_collections_survives_insert_race(client):
    """启动钩子里的 ensure 也必须扛住并发插入：SELECT 落空 + INSERT 撞唯一索引 → 回退查询已有。

    真实后果比 f2 那条更重：``ensure_root_collections`` 跑在 lifespan 里，异常冒泡
    出去就是**后端起不来**（worker 正在跑收藏任务时建同一个一级节点，窗口虽窄但真实）。
    """
    from sqlalchemy import select

    from app.database import async_session
    from app.models.collection import Collection
    from app.services import collection_service

    class _Miss:
        """假装「一条都没查到」的最小结果对象（_ensure_one 只用到 scalar）。"""

        def scalar(self):
            return None

    name = collection_service.MANUAL_ROOT_COLLECTION_NAME
    async with async_session() as db:
        real_execute = db.execute
        state = {"pending": True}

        async def _flaky(stmt, *args, **kwargs):
            if state["pending"] and name in str(stmt):
                state["pending"] = False
                # 对手抢在我们 INSERT 之前建好了同名一级节点
                async with async_session() as other:
                    other.add(Collection(name=name, position=7))
                    await other.commit()
                return _Miss()
            return await real_execute(stmt, *args, **kwargs)

        db.execute = _flaky  # type: ignore[method-assign]
        try:
            roots = await collection_service.ensure_root_collections(db)
        finally:
            db.execute = real_execute  # type: ignore[method-assign]

        winner = (
            await db.execute(select(Collection).where(Collection.name == name))
        ).scalars().one()

    assert roots[name] == winner.id


async def test_list_collections_query_count_stays_flat(client):
    """列表接口的 SQL 条数不随合集数量增长（两级结构下能挂几十个收藏夹）。

    回归：原先逐条 ``collection_to_dict`` → 每个合集 1 次成员计数 + 1 次封面查询，
    31 个收藏夹时列表页一次约 100 条 SQL。批量序列化后应稳定在个位数。
    """
    from sqlalchemy import event

    from app.database import async_session, engine
    from app.models.collection import Collection
    from app.services import collection_service

    async with async_session() as db:
        for i in range(25):
            db.add(Collection(name=f"查询数夹{i}", position=100 + i))
        await db.commit()

    statements: list[str] = []

    def _before_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _before_execute)
    try:
        async with async_session() as db:
            data = await collection_service.list_collections(db)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before_execute)

    assert len(data) >= 25
    assert len(statements) <= 10, f"列表接口 SQL 条数应恒定，实际 {len(statements)}：{statements}"


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
    assert sorted(ids) == sorted([a, b]) and total == 2

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
    r = delete_json(client, f"{base}/inspirations", {"inspiration_ids": ["x"]})
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
