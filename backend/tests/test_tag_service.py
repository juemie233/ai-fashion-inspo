"""标签服务高级功能集成测试：合并/批量编辑/未使用删除/别名/导入导出/排序/统计。"""


def _tag_names(client) -> list[str]:
    """返回库内全部标签名（扁平化）。"""
    return [t["name"] for g in client.get("/api/tags").json() for t in g["tags"]]


async def test_seed_tags_skips_when_tags_exist(client):
    """预设标签仅空库导入：库非空时 seed 不执行，用户删除的预设不重建。

    回归：曾出现「删除预设标签后后端重启又被 seed 重建」——seed_tags 每次
    启动执行且只按名称判重，无法区分「从未存在」与「用户删除过」。
    """
    from sqlalchemy import delete, func, select

    from app.database import async_session
    from app.models.tag import Tag
    from app.services.tag_service import seed_tags

    async with async_session() as db:
        # 模拟用户使用中的库：已有任意标签（非空库）
        db.add(Tag(name="已有标签", category="free"))
        await db.commit()

        # 库非空 → seed 不再执行
        added = await seed_tags(db)
        assert added == 0

        # 预设标签未被创建（如 Lolita）
        count = (
            await db.execute(select(func.count(Tag.id)).where(Tag.name == "Lolita"))
        ).scalar()
        assert count == 0

        # 删除一个预设后再次 seed 也不会重建（同前：库非空直接跳过）
        await db.execute(delete(Tag).where(Tag.name == "已有标签"))
        await db.commit()
        db.add(Tag(name="另一个标签", category="free"))
        await db.commit()
        added2 = await seed_tags(db)
        assert added2 == 0


async def test_seed_tags_imports_on_empty_db(client):
    """空库（全新初始化）时 seed 导入全部预设标签。"""
    from sqlalchemy import func, select

    from app.database import async_session
    from app.models.tag import Tag
    from app.services.tag_service import seed_tags

    async with async_session() as db:
        # clean_state 已清空 tags 表 → 空库
        added = await seed_tags(db)
        assert added > 0
        # 预设标签已创建（抽查）
        for name in ("Lolita", "白衬衫", "街拍"):
            count = (
                await db.execute(select(func.count(Tag.id)).where(Tag.name == name))
            ).scalar()
            assert count == 1, f"预设标签 {name} 应被创建"


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


def test_tag_stats_unused_counts_trash_only(client, upload):
    """只关联垃圾桶素材的标签应计入「未使用」统计（与 usage_count=0 口径一致）。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})
    tag_id = next(
        t["id"]
        for g in client.get("/api/tags").json()
        for t in g["tags"]
        if t["name"] == "法式"
    )

    # 素材未删除：标签有未删除素材关联，不算未使用
    assert client.get("/api/tags/stats").json()["unused"] == 0

    # 移入垃圾桶：标签不再有未删除素材关联 → 计入未使用
    client.post(f"/api/inspirations/{insp_id}/trash")
    stats = client.get("/api/tags/stats").json()
    assert stats["unused"] == 1
    # 标签列表使用次数同步为 0（两侧口径一致，不再出现「无素材却未使用数为 0」的矛盾）
    groups = client.get("/api/tags").json()
    tag = next(t for g in groups for t in g["tags"] if t["id"] == tag_id)
    assert tag["usage_count"] == 0


def test_delete_unused_tags_removes_trash_only(client, upload):
    """删除未使用：只关联垃圾桶素材的标签也应被清理（连同残留关联）。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})
    client.post(f"/api/inspirations/{insp_id}/trash")

    r = client.delete("/api/tags/unused")
    assert r.status_code == 200
    assert r.json()["count"] == 1

    names = _tag_names(client)
    assert "法式" not in names
