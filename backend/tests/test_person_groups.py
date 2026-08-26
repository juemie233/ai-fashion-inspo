"""博主人物组（方案 B）测试：绑定/解绑/切主、列表折叠、审计留痕、路由安全。"""

from app.config import settings


def _sql(statement: str, params: tuple = ()) -> list:
    """直接查库（断言审计留痕等跨表状态用）。"""
    import sqlite3

    db_path = settings.storage_root.parent / "fashion_inspo.db"
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(statement, params)
        conn.commit()
        if cur.description:
            return cur.fetchall()
        return []
    finally:
        conn.close()


def _create_blogger(client, name: str, platform: str = "xiaohongshu") -> dict:
    r = client.post(
        "/api/bloggers",
        json={"name": name, "platform": platform, "platform_user_id": f"uid-{name}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ── 绑定 / 解绑 / 切主 ──


def test_link_bloggers_creates_group(client):
    """两个博主绑定 → 新建组，返回组信息（成员含双方）。"""
    a = _create_blogger(client, "Fox_", platform="xiaohongshu")
    b = _create_blogger(client, "多多", platform="douyin")

    r = client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": a["id"], "target_blogger_id": b["id"]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["group_id"] > 0
    assert set(data["member_ids"]) == {a["id"], b["id"]}

    # 组信息接口
    info = client.get(f"/api/bloggers/groups/{data['group_id']}").json()
    assert len(info["members"]) == 2
    assert {m["id"] for m in info["members"]} == {a["id"], b["id"]}


def test_link_bloggers_into_existing_group(client):
    """两个已绑定的博主 + 第三个博主 → 并入已有组。"""
    a = _create_blogger(client, "甲", platform="xiaohongshu")
    b = _create_blogger(client, "乙", platform="douyin")
    c = _create_blogger(client, "丙", platform="other")

    link = client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": a["id"], "target_blogger_id": b["id"]},
    ).json()
    gid = link["group_id"]

    r = client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": c["id"], "group_id": gid},
    )
    assert r.status_code == 200
    assert set(r.json()["member_ids"]) == {a["id"], b["id"], c["id"]}


def test_link_bloggers_self_rejected(client):
    """绑定自身 → 409。"""
    a = _create_blogger(client, "自环")
    r = client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": a["id"], "target_blogger_id": a["id"]},
    )
    assert r.status_code == 409


def test_link_bloggers_already_in_group_rejected(client):
    """已同组再绑定 → 409。"""
    a = _create_blogger(client, "同组甲")
    b = _create_blogger(client, "同组乙")
    client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": a["id"], "target_blogger_id": b["id"]},
    )
    r = client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": a["id"], "target_blogger_id": b["id"]},
    )
    assert r.status_code == 409


def test_unlink_blogger(client):
    """解绑 → 账号独立；组内剩 1 个时自动删组。"""
    a = _create_blogger(client, "解绑甲")
    b = _create_blogger(client, "解绑乙")
    gid = client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": a["id"], "target_blogger_id": b["id"]},
    ).json()["group_id"]

    r = client.post("/api/bloggers/groups/unlink", json={"blogger_id": a["id"]})
    assert r.status_code == 200
    assert r.json()["removed_group_id"] == gid  # 组内只剩乙 → 组删除

    # 乙回退独立
    assert client.get(f"/api/bloggers/groups/{gid}").status_code == 404
    lst = client.get("/api/bloggers", params={"grouped": True}).json()
    ids = [i["id"] for i in lst["items"]]
    assert b["id"] in ids


def test_unlink_blogger_not_in_group(client):
    """未在组的博主解绑 → 409。"""
    a = _create_blogger(client, "游离")
    r = client.post("/api/bloggers/groups/unlink", json={"blogger_id": a["id"]})
    assert r.status_code == 409


def test_set_primary_blogger(client):
    """手动切主 → 组信息主账号更新；解绑主账号且组内剩 1 人时组自动删除。"""
    a = _create_blogger(client, "主甲")
    b = _create_blogger(client, "主乙")
    gid = client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": a["id"], "target_blogger_id": b["id"]},
    ).json()["group_id"]

    r = client.post(
        f"/api/bloggers/groups/{gid}/set-primary", json={"blogger_id": b["id"]}
    )
    assert r.status_code == 200
    assert r.json()["primary_blogger_id"] == b["id"]

    # 组信息接口反映手动主账号
    info = client.get(f"/api/bloggers/groups/{gid}").json()
    assert info["primary_blogger_id"] == b["id"]

    # 解绑主账号：组内只剩 a（1 人）→ 组自动删除，a 回退独立
    client.post("/api/bloggers/groups/unlink", json={"blogger_id": b["id"]})
    assert client.get(f"/api/bloggers/groups/{gid}").status_code == 404
    lst = client.get("/api/bloggers", params={"grouped": True}).json()
    ids = [i["id"] for i in lst["items"]]
    assert a["id"] in ids


def test_set_primary_not_in_group(client):
    """把组外博主设为主账号 → 409。"""
    a = _create_blogger(client, "组甲")
    b = _create_blogger(client, "组乙")
    outsider = _create_blogger(client, "组外")
    gid = client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": a["id"], "target_blogger_id": b["id"]},
    ).json()["group_id"]

    r = client.post(
        f"/api/bloggers/groups/{gid}/set-primary", json={"blogger_id": outsider["id"]}
    )
    assert r.status_code == 409


# ── 列表折叠（grouped） ──


def test_list_grouped_collapses(client, upload):
    """grouped=true：同组只显示一条主记录（素材数多者），组员在 group_members。"""
    a = _create_blogger(client, "折叠甲", platform="xiaohongshu")
    b = _create_blogger(client, "折叠乙", platform="douyin")
    # 给甲关联 2 个素材，乙 1 个 → 甲为主账号
    for _ in range(2):
        insp_id = upload().json()["id"]
        client.post(f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [a["id"]]})
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [b["id"]]})

    lk = client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": a["id"], "target_blogger_id": b["id"]},
    )
    assert lk.status_code == 200, lk.text
    assert set(lk.json()["member_ids"]) == {a["id"], b["id"]}, lk.text

    lst = client.get("/api/bloggers", params={"grouped": True}).json()
    assert lst["total"] == 1, f"total={lst['total']} items={lst['items']}"
    item = lst["items"][0]
    assert item["id"] == a["id"]  # 主账号 = 素材数多者
    assert item["inspiration_count"] == 2
    assert len(item["group_members"]) == 1
    assert item["group_members"][0]["id"] == b["id"]
    assert set(item["group_platforms"]) == {"xiaohongshu", "douyin"}

    # 平铺视图（grouped=false）：两条都显示
    flat = client.get("/api/bloggers", params={"grouped": False}).json()
    assert flat["total"] == 2


def test_list_platform_filter_flat(client):
    """按平台筛选时平铺（即使 grouped=true 也不折叠）。"""
    a = _create_blogger(client, "平台甲", platform="xiaohongshu")
    b = _create_blogger(client, "平台乙", platform="douyin")
    client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": a["id"], "target_blogger_id": b["id"]},
    )

    lst = client.get(
        "/api/bloggers", params={"grouped": True, "platform": "douyin"}
    ).json()
    assert lst["total"] == 1
    assert lst["items"][0]["id"] == b["id"]


def test_list_grouped_standalone_unchanged(client, create_blogger):
    """独立账号（不在任何组）在折叠视图中照常显示。"""
    create_blogger(name="独立一号")
    create_blogger(name="独立二号")
    lst = client.get("/api/bloggers", params={"grouped": True}).json()
    assert lst["total"] == 2
    assert all("group_members" not in i or i["group_members"] == [] for i in lst["items"])


# ── 审计留痕 ──


def test_group_operations_write_audit(client):
    """绑定/解绑/切主均写 audit_logs。"""
    a = _create_blogger(client, "审计甲")
    b = _create_blogger(client, "审计乙")
    gid = client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": a["id"], "target_blogger_id": b["id"]},
    ).json()["group_id"]
    client.post(f"/api/bloggers/groups/{gid}/set-primary", json={"blogger_id": b["id"]})
    client.post("/api/bloggers/groups/unlink", json={"blogger_id": a["id"]})

    rows = _sql("SELECT action FROM audit_logs ORDER BY id")
    actions = [r[0] for r in rows]
    assert "link_blogger_group" in actions
    assert "set_primary_blogger" in actions
    assert "unlink_blogger_group" in actions


# ── 路由安全：破坏性接口认证 ──


def test_group_routes_require_key(client, monkeypatch):
    """配置 API_KEY 后：绑定/解绑/切主无 key → 401。"""
    monkeypatch.setattr(settings, "api_key", "test-secret")
    assert client.post("/api/bloggers/groups/link", json={}).status_code == 401
    assert client.post("/api/bloggers/groups/unlink", json={}).status_code == 401
    assert client.post("/api/bloggers/groups/1/set-primary", json={}).status_code == 401
