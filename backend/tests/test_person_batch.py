"""人物管理批量操作测试：批量纳入追踪 / 批量删除 / 头像覆盖率与批量清除 / 同名建议。

批量删除复用单条删除的校验（有素材关联一律拒绝），这里重点验证「能删的删掉、
不能删的被挡下并说明原因」，以及同名建议只给建议、不改数据。
"""

from app.config import settings
from app.database import async_session
from app.models.person import Blogger, PersonGroup
from app.services.person import batch


def _upload_material(client, make_image, size=(64, 64)) -> str:
    """上传一张素材，返回素材 id。"""
    data, ctype = make_image(size=size)
    resp = client.post("/api/inspirations", files={"file": ("m.jpg", data, ctype)})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _link(client, inspiration_id: str, blogger_id: int) -> None:
    """把素材绑到博主。"""
    resp = client.post(f"/api/inspirations/{inspiration_id}/bloggers", json={"person_ids": [blogger_id]})
    assert resp.status_code == 200, resp.text


def _write_avatar_file(blogger_id: int) -> str:
    """造一个头像文件（清除头像时应被删掉）。"""
    rel = f"avatars/avatar_{blogger_id}.jpg"
    path = settings.storage_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-jpeg")
    return rel


async def _mark_auto_collect(blogger_id: int) -> None:
    """把博主标记为「自动登记」（创建接口不接受 source，测试里直接改库）。"""
    async with async_session() as db:
        row = await db.get(Blogger, blogger_id)
        assert row is not None
        row.source = "auto_collect"
        await db.commit()


async def _set_avatar(blogger_id: int) -> str:
    """给博主写一个「已有头像」状态（库 + 文件）。"""
    rel = _write_avatar_file(blogger_id)
    async with async_session() as db:
        row = await db.get(Blogger, blogger_id)
        row.avatar_path = rel
        await db.commit()
    return rel


async def test_batch_promote_only_touches_auto_collect(client, create_blogger):
    """只把「自动登记」的改为 manual；已登记的、不存在的进跳过明细。"""
    auto = create_blogger(name="自动登记博")
    await _mark_auto_collect(auto["id"])
    manual = create_blogger(name="已登记博", source="manual")

    async with async_session() as db:
        result = await batch.batch_promote(db, [auto["id"], manual["id"], 999999])

    assert result["promoted"] == 1
    reasons = {item["id"]: item["reason"] for item in result["skipped"]}
    assert manual["id"] in reasons and "不是" in reasons[manual["id"]]
    assert 999999 in reasons

    async with async_session() as db:
        auto_row = await db.get(Blogger, auto["id"])
        manual_row = await db.get(Blogger, manual["id"])
    assert auto_row is not None and auto_row.source == "manual"
    assert manual_row is not None and manual_row.source == "manual"


async def test_batch_delete_blocks_bloggers_with_materials(client, create_blogger, make_image):
    """有素材的删不掉（被挡下并说明原因），没素材的删掉。"""
    empty = create_blogger(name="空壳博")
    busy = create_blogger(name="有素材博")
    _link(client, _upload_material(client, make_image), busy["id"])

    async with async_session() as db:
        result = await batch.batch_delete(db, [empty["id"], busy["id"]])

    assert result["deleted"] == 1
    blocked = {item["id"]: item["reason"] for item in result["blocked"]}
    assert busy["id"] in blocked and "素材" in blocked[busy["id"]]

    async with async_session() as db:
        assert await db.get(Blogger, empty["id"]) is None
        assert await db.get(Blogger, busy["id"]) is not None


async def test_avatar_coverage_counts(client, create_blogger, make_image):
    """覆盖率：总数、已有头像、可补（有素材且没头像）。"""
    no_material = create_blogger(name="无素材博")
    fillable = create_blogger(name="可补博")
    _link(client, _upload_material(client, make_image), fillable["id"])
    has_avatar = create_blogger(name="有头像博")
    _link(client, _upload_material(client, make_image), has_avatar["id"])
    await _set_avatar(has_avatar["id"])

    async with async_session() as db:
        coverage = await batch.avatar_coverage(db)

    assert coverage["total"] >= 3
    assert coverage["with_avatar"] >= 1
    assert coverage["fillable"] >= 1
    # 无素材的永远不算「可补」
    assert coverage["fillable"] < coverage["total"]


async def test_clear_avatars_removes_files(client, create_blogger):
    """批量清除：库置空 + 文件删除；指定 ids 时只清指定的。"""
    keep = create_blogger(name="保留头像博")
    drop = create_blogger(name="清除头像博")
    keep_rel = await _set_avatar(keep["id"])
    drop_rel = await _set_avatar(drop["id"])

    async with async_session() as db:
        result = await batch.clear_avatars(db, [drop["id"]])

    assert result["cleared"] == 1
    assert not (settings.storage_root / drop_rel).exists()
    assert (settings.storage_root / keep_rel).exists()

    async with async_session() as db:
        drop_row = await db.get(Blogger, drop["id"])
        keep_row = await db.get(Blogger, keep["id"])
    assert drop_row is not None and not (drop_row.avatar_path or "").strip()
    assert keep_row is not None and keep_row.avatar_path == keep_rel


async def test_duplicate_name_groups_normalizes_and_skips_grouped(client, create_blogger):
    """同名建议：归一化后同名成组（emoji/下划线/波浪号不影响）；已同组的不再提示。"""
    first = create_blogger(name="折乙")
    second = create_blogger(name="折乙～")  # 波浪号归一化后同名
    unique = create_blogger(name="独一份")
    grouped_a = create_blogger(name="已合并人")
    grouped_b = create_blogger(name="已合并人_")

    async with async_session() as db:
        group = PersonGroup(name="已合并人", primary_blogger_id=grouped_a["id"])
        db.add(group)
        await db.flush()
        for blogger_id in (grouped_a["id"], grouped_b["id"]):
            row = await db.get(Blogger, blogger_id)
            row.person_group_id = group.id
        await db.commit()
        groups = await batch.duplicate_name_groups(db)

    keys = {group["key"] for group in groups}
    assert "折乙" in keys
    assert "独一份" not in keys
    assert "已合并人" not in keys  # 已经在同一组里，不必再提示
    target = next(group for group in groups if group["key"] == "折乙")
    assert {item["id"] for item in target["items"]} >= {first["id"], second["id"]}
    assert unique["id"] not in {item["id"] for item in target["items"]}


async def test_api_batch_endpoints(client, create_blogger):
    """接口层：批量纳入追踪 / 覆盖率 / 同名建议 都能通（参数校验也在）。"""
    auto = create_blogger(name="接口自动登记博")
    await _mark_auto_collect(auto["id"])

    resp = client.post("/api/bloggers/batch/promote", json={"ids": [auto["id"]]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["promoted"] == 1

    resp = client.get("/api/bloggers/avatars/coverage")
    assert resp.status_code == 200, resp.text
    assert "fillable" in resp.json()

    resp = client.get("/api/bloggers/duplicate-names")
    assert resp.status_code == 200, resp.text
    assert "groups" in resp.json()

    # ids 非法 → 422（前端拼错参数时要立刻报错，而不是悄悄当成「全部」）
    resp = client.post("/api/bloggers/batch/promote", json={"ids": "1,2"})
    assert resp.status_code == 422


async def test_api_clear_avatars_requires_explicit_scope(client, create_blogger):
    """清除头像必须显式指定范围：空 body 一律 422（破坏性操作不能靠默认值全清）。"""
    blogger = create_blogger(name="有头像待清博")
    await _set_avatar(blogger["id"])

    resp = client.post("/api/bloggers/avatars/clear", json={})
    assert resp.status_code == 422
    assert "all=true" in resp.json()["detail"]

    # 指定 ids：只清这一位
    resp = client.post("/api/bloggers/avatars/clear", json={"ids": [blogger["id"]]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["cleared"] == 1
