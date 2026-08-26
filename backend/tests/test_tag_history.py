"""标签操作历史测试：快照记录、单条回滚、merge 回滚恢复、冲突检测。"""

from sqlalchemy import func, select

from app.database import async_session
from app.models.tag import InspirationTag, Tag, TagAlias
from app.services.tag_history_service import (
    TagHistoryRollbackError,
    list_history,
    rollback_history,
)


def _history_rows(client) -> list[dict]:
    """获取全部操作历史（按时间倒序）。"""
    return client.get("/api/tags/history").json()["items"]


def _tag_names(client) -> list[str]:
    """返回库内全部标签名（扁平化）。"""
    return [t["name"] for g in client.get("/api/tags").json() for t in g["tags"]]


def test_create_and_rename_record_history(client):
    """创建与改名都写入操作历史，before/after 快照正确。"""
    tag = client.post("/api/tags", json={"name": "历史甲", "category": "color"}).json()
    r = client.patch(f"/api/tags/{tag['id']}", json={"name": "历史乙"})
    assert r.status_code == 200

    rows = _history_rows(client)
    assert [x["operation"] for x in rows] == ["rename", "create"]

    create_row = rows[1]
    assert create_row["before"] == {}
    assert create_row["after"][str(tag["id"])]["name"] == "历史甲"
    assert create_row["tag_ids"] == [tag["id"]]
    # 影响标签随行返回「当前」标签名（与 tag_ids 一一对应；查询时标签已被改名）
    assert create_row["tag_names"] == ["历史乙"]

    rename_row = rows[0]
    assert rename_row["before"][str(tag["id"])]["name"] == "历史甲"
    assert rename_row["after"][str(tag["id"])]["name"] == "历史乙"
    assert rename_row["tag_names"] == ["历史乙"]


async def test_rollback_rename_restores_name(client):
    """回滚改名：名称恢复为操作前值。"""
    tag = client.post("/api/tags", json={"name": "历史甲", "category": "color"}).json()
    client.patch(f"/api/tags/{tag['id']}", json={"name": "历史乙"})
    rename_row = _history_rows(client)[0]
    assert rename_row["operation"] == "rename"

    async with async_session() as db:
        res = await rollback_history(db, rename_row["id"])
        assert res["rolled_back"] is True

    names = _tag_names(client)
    assert "历史甲" in names and "历史乙" not in names


async def test_merge_records_history(client, upload):
    """合并写入历史；回滚后源标签、别名、关联全部恢复。"""
    s = client.post("/api/tags", json={"name": "历史源", "category": "style"}).json()
    t = client.post("/api/tags", json={"name": "历史目标", "category": "style"}).json()
    # 给源标签添加一个别名
    alias_r = client.post(f"/api/tags/{s['id']}/aliases", json={"alias": "历史源别名"})
    assert alias_r.status_code == 201
    # 上传素材并关联到源标签
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["历史源"]})

    # 执行合并
    r = client.post("/api/tags/merge", json={"source_tag_id": s["id"], "target_tag_id": t["id"]})
    assert r.status_code == 200
    assert "历史源" not in _tag_names(client)

    # 历史中存在 merge 记录（含关联/别名明细）
    merge_row = next(x for x in _history_rows(client) if x["operation"] == "merge")
    meta = merge_row["meta"]
    assert meta["source_tag_id"] == s["id"]
    assert meta["target_tag_id"] == t["id"]
    assert insp_id in meta["merged_link_ids"]
    assert len(meta["moved_alias_ids"]) == 1

    # 回滚合并
    async with async_session() as db:
        res = await rollback_history(db, merge_row["id"])
        assert res["rolled_back"] is True

    names = _tag_names(client)
    assert "历史源" in names
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert any(x["tag"]["name"] == "历史源" for x in detail["tags"])
    # 别名回到源标签
    async with async_session() as db:
        alias_owner = (
            await db.execute(
                select(TagAlias.tag_id).where(TagAlias.alias == "历史源别名")
            )
        ).scalar_one_or_none()
        assert alias_owner == s["id"]


async def test_merge_rollback_restores_duplicate_links(client, upload):
    """merge 回滚：合并时被删除的重复关联（素材同时关联源与目标）也会恢复。"""
    s = client.post("/api/tags", json={"name": "重复源", "category": "style"}).json()
    t = client.post("/api/tags", json={"name": "重复目标", "category": "style"}).json()
    insp_id = upload().json()["id"]
    # 素材同时关联源与目标 → 合并时源侧关联被删除（重复）
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["重复源", "重复目标"]})

    client.post("/api/tags/merge", json={"source_tag_id": s["id"], "target_tag_id": t["id"]})
    merge_row = next(x for x in _history_rows(client) if x["operation"] == "merge")
    meta = merge_row["meta"]
    assert insp_id in meta["duplicate_link_ids"]

    async with async_session() as db:
        await rollback_history(db, merge_row["id"])

    # 回滚后源标签重新关联该素材
    async with async_session() as db:
        source_links = (
            await db.execute(
                select(InspirationTag.inspiration_id).where(InspirationTag.tag_id == s["id"])
            )
        ).scalars().all()
        assert insp_id in source_links


async def test_delete_rollback_restores_tag(client):
    """删除写入历史；回滚重建标签（保留原 id）。"""
    tag = client.post("/api/tags", json={"name": "删除甲", "category": "free"}).json()
    r = client.post("/api/tags/batch-delete", json={"tag_ids": [tag["id"]]})
    assert r.status_code == 200
    assert "删除甲" not in _tag_names(client)

    delete_row = next(x for x in _history_rows(client) if x["operation"] == "delete")
    async with async_session() as db:
        await rollback_history(db, delete_row["id"])

    names = _tag_names(client)
    assert "删除甲" in names
    async with async_session() as db:
        restored = (
            await db.execute(select(Tag).where(Tag.name == "删除甲"))
        ).scalar_one_or_none()
        assert restored is not None
        assert restored.id == tag["id"]


async def test_delete_rollback_restores_links(client, upload):
    """删除使用中的标签后回滚：素材-标签关联一并恢复（修复级联删除丢关联）。"""
    tag = client.post("/api/tags", json={"name": "删除关联甲", "category": "style"}).json()
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["删除关联甲"]})

    # 删除使用中的标签（级联删除关联行）
    r = client.post("/api/tags/batch-delete", json={"tag_ids": [tag["id"]]})
    assert r.status_code == 200
    async with async_session() as db:
        links_after_delete = (
            await db.execute(
                select(InspirationTag.inspiration_id).where(InspirationTag.tag_id == tag["id"])
            )
        ).scalars().all()
        assert links_after_delete == []

    delete_row = next(x for x in _history_rows(client) if x["operation"] == "delete")
    async with async_session() as db:
        await rollback_history(db, delete_row["id"])

    # 回滚后标签重建，且素材关联恢复
    async with async_session() as db:
        links_after_rollback = (
            await db.execute(
                select(InspirationTag.inspiration_id).where(InspirationTag.tag_id == tag["id"])
            )
        ).scalars().all()
        assert insp_id in links_after_rollback


async def test_rollback_conflict_detection(client):
    """回滚冲突检测：操作后标签又被修改 → 拒绝回滚并报冲突。"""
    tag = client.post("/api/tags", json={"name": "冲突甲", "category": "free"}).json()
    client.patch(f"/api/tags/{tag['id']}", json={"name": "冲突乙"})
    first_rename = _history_rows(client)[0]
    assert first_rename["operation"] == "rename"
    # 操作后又被改名
    client.patch(f"/api/tags/{tag['id']}", json={"name": "冲突丙"})

    async with async_session() as db:
        try:
            await rollback_history(db, first_rename["id"])
            raise AssertionError("应抛出 TagHistoryRollbackError 冲突异常")
        except TagHistoryRollbackError as e:
            assert "冲突乙" in e.message or "已被修改" in e.message


async def test_rollback_rename_name_taken_by_other_tag(client):
    """回滚改名时旧名已被其它标签占用 → 友好拒绝（409 而非唯一约束 500）。"""
    a = client.post("/api/tags", json={"name": "回滚甲", "category": "free"}).json()
    b = client.post("/api/tags", json={"name": "占用乙", "category": "free"}).json()
    # A 改名为「占用丙」
    client.patch(f"/api/tags/{a['id']}", json={"name": "占用丙"})
    rename_row = _history_rows(client)[0]
    assert rename_row["operation"] == "rename"
    # B 占用了 A 的旧名「回滚甲」
    client.patch(f"/api/tags/{b['id']}", json={"name": "回滚甲"})

    async with async_session() as db:
        try:
            await rollback_history(db, rename_row["id"])
            raise AssertionError("应抛出 TagHistoryRollbackError（旧名被占用）")
        except TagHistoryRollbackError as e:
            assert "已被其它标签占用" in e.message


async def test_alias_history_and_rollback(client):
    """添加/删除别名写入历史；回滚 alias_add 移除别名。"""
    tag = client.post("/api/tags", json={"name": "别名甲", "category": "free"}).json()
    client.post(f"/api/tags/{tag['id']}/aliases", json={"alias": "别名乙"})

    add_row = next(x for x in _history_rows(client) if x["operation"] == "alias_add")
    assert add_row["meta"]["alias"] == "别名乙"
    assert "别名乙" in add_row["after"][str(tag["id"])]["aliases"]

    async with async_session() as db:
        await rollback_history(db, add_row["id"])
    async with async_session() as db:
        exists = (
            await db.execute(select(TagAlias.id).where(TagAlias.alias == "别名乙"))
        ).scalar_one_or_none()
        assert exists is None


async def test_batch_category_history_and_rollback(client):
    """批量改类别写入历史（同批次分组）；回滚恢复原类别。"""
    a = client.post("/api/tags", json={"name": "批类甲", "category": "free"}).json()
    b = client.post("/api/tags", json={"name": "批类乙", "category": "free"}).json()
    r = client.patch(
        "/api/tags/batch-category",
        json={"tag_ids": [a["id"], b["id"]], "category": "style"},
    )
    assert r.status_code == 200

    cat_row = next(x for x in _history_rows(client) if x["operation"] == "category_change")
    assert cat_row["batch_id"]
    assert set(cat_row["tag_ids"]) == {a["id"], b["id"]}

    async with async_session() as db:
        await rollback_history(db, cat_row["id"])
    async with async_session() as db:
        cats = {
            tid: category
            for tid, category in (await db.execute(select(Tag.id, Tag.category))).all()
        }
        assert cats[a["id"]] == "free" and cats[b["id"]] == "free"


async def test_list_history_filter_by_tag(client):
    """历史查询支持按标签 ID 过滤（json_each 精确匹配）。"""
    a = client.post("/api/tags", json={"name": "过滤甲", "category": "free"}).json()
    client.patch(f"/api/tags/{a['id']}", json={"name": "过滤甲改"})
    client.post("/api/tags", json={"name": "过滤乙", "category": "free"}).json()

    async with async_session() as db:
        result = await list_history(db, tag_id=a["id"])
        assert result["total"] == 2  # 只含「过滤甲」的 create + rename
        assert all(a["id"] in item["tag_ids"] for item in result["items"])

        result_all = await list_history(db, operation="create")
        assert result_all["total"] == 2  # 「过滤甲」「过滤乙」两条 create 记录


def test_rollback_via_http_endpoint(client):
    """HTTP 回滚端点可用；历史不存在返回 404。"""
    tag = client.post("/api/tags", json={"name": "接口甲", "category": "free"}).json()
    client.patch(f"/api/tags/{tag['id']}", json={"name": "接口乙"})
    rename_row = _history_rows(client)[0]

    r = client.post(f"/api/tags/history/{rename_row['id']}/rollback")
    assert r.status_code == 200
    assert r.json()["rolled_back"] is True
    assert "接口甲" in _tag_names(client)

    r404 = client.post("/api/tags/history/999999/rollback")
    assert r404.status_code == 404
