"""垃圾桶（软删除）回归测试：移入/恢复/清空/过期清理。"""

import sqlite3
from datetime import timedelta

from app.config import settings
from app.utils.time import utcnow


def _set_deleted_at(days_ago: int, inspiration_id: str):
    """直接改库，把素材的 deleted_at 改成 days_ago 天前（模拟过期）。"""
    db_path = settings.storage_root.parent / "fashion_inspo.db"
    conn = sqlite3.connect(str(db_path))
    try:
        old = (utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE inspirations SET deleted_at = ? WHERE id = ?",
            (old, inspiration_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_trash_restore_roundtrip(client, upload):
    """移入垃圾桶 → 恢复 → 回到正常列表。"""
    insp_id = upload().json()["id"]

    client.post(f"/api/inspirations/{insp_id}/trash", json={"reason": "质量差"})
    assert client.get("/api/inspirations").json()["total"] == 0

    r = client.post(f"/api/inspirations/{insp_id}/restore")
    assert r.status_code == 200
    assert r.json()["deleted_at"] is None

    assert client.get("/api/inspirations").json()["total"] == 1


def test_trash_filter_by_reason(client, upload):
    """垃圾桶可按删除原因筛选。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    client.post(f"/api/inspirations/{a}/trash", json={"reason": "质量差"})
    client.post(f"/api/inspirations/{b}/trash", json={"reason": "重复"})

    r = client.get("/api/inspirations/trash", params={"reason": "质量差"}).json()
    assert r["total"] == 1
    assert r["items"][0]["id"] == a


def test_empty_trash_purges_all(client, upload):
    """清空垃圾桶（DELETE /trash）后垃圾桶为空，正常列表不受影响。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    client.post(f"/api/inspirations/{a}/trash")
    client.post(f"/api/inspirations/{b}/trash")

    r = client.delete("/api/inspirations/trash")
    assert r.status_code == 200
    assert r.json()["deleted"] == 2

    assert client.get("/api/inspirations/trash").json()["total"] == 0
    assert client.get("/api/inspirations").json()["total"] == 0


def test_purge_only_expired(client, upload, monkeypatch):
    """only_expired=True 只清理超过保留期的素材，未过期保留。"""
    # 显式启用保留期（默认 0 表示禁用自动回收），验证 only_expired 逻辑
    monkeypatch.setattr(settings, "trash_retention_days", 30)
    fresh = upload().json()["id"]
    old = upload().json()["id"]
    client.post(f"/api/inspirations/{fresh}/trash")
    client.post(f"/api/inspirations/{old}/trash")
    _set_deleted_at(days_ago=40, inspiration_id=old)  # 超过 30 天保留期

    r = client.delete("/api/inspirations/trash", params={"only_expired": True})
    assert r.json()["deleted"] == 1

    trash = client.get("/api/inspirations/trash").json()
    assert trash["total"] == 1
    assert trash["items"][0]["id"] == fresh


def test_purge_only_expired_disabled_when_retention_zero(client, upload):
    """trash_retention_days=0（禁用自动回收）时 only_expired=True 不清理任何素材。"""
    insp = upload().json()["id"]
    client.post(f"/api/inspirations/{insp}/trash")
    _set_deleted_at(days_ago=999, inspiration_id=insp)  # 远超任何保留期

    r = client.delete("/api/inspirations/trash", params={"only_expired": True})
    assert r.json()["deleted"] == 0

    # 素材仍留在垃圾桶，未被自动回收
    assert client.get("/api/inspirations/trash").json()["total"] == 1


def test_restore_missing(client):
    assert client.post("/api/inspirations/no-such/restore").status_code == 404


def test_trash_writes_tombstone(client, upload):
    """移入垃圾桶立即写入来源 URL 墓碑，防止采集器重复采集。"""
    url = "https://www.xiaohongshu.com/explore/abc123"
    insp_id = upload(source_url=url).json()["id"]

    client.post(f"/api/inspirations/{insp_id}/trash")

    db_path = settings.storage_root.parent / "fashion_inspo.db"
    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM scraper_seen_urls WHERE source_url = ?", (url,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_upload_rejected_by_tombstone(client, upload):
    """墓碑拦截：删除（移入垃圾桶）过的来源 URL 再次上传 → 409，不再重新入库。

    对应「采集任务结果删除的素材直接添加到墓碑表」的防重复闭环：
    删除即写墓碑，插件/上传链路再次携带同一 URL 时被墓碑拦截。
    """
    url = "https://www.xiaohongshu.com/explore/tombstone-upload"
    insp_id = upload(source_url=url).json()["id"]
    client.post(f"/api/inspirations/{insp_id}/trash")  # 软删除 → 写墓碑

    r = upload(source_url=url)  # 同 URL 再次上传
    assert r.status_code == 409
    assert "墓碑" in r.json()["detail"]


def test_url_import_rejected_by_tombstone(client, upload):
    """墓碑拦截：URL 导入（含插件下载链路）携带已被删除的 URL → 409，不下载不入库。"""
    url = "https://img.example.com/tombstone-import.jpg"
    insp_id = upload(source_url=url).json()["id"]
    client.post(f"/api/inspirations/{insp_id}/trash")

    r = client.post(
        "/api/inspirations/from-url",
        json={"url": url, "source_type": "browser_extension"},
    )
    assert r.status_code == 409
    assert "墓碑" in r.json()["detail"]


def test_trash_source_default_manual(client, upload):
    """单条移入垃圾桶：默认来源为手动（manual），垃圾桶返回来源标记。"""
    insp = upload().json()["id"]
    r = client.post(f"/api/inspirations/{insp}/trash", json={"reason": "不喜欢"})
    assert r.status_code == 200
    assert r.json()["trash_source"] == "manual"

    trash = client.get("/api/inspirations/trash").json()
    assert trash["items"][0]["trash_source"] == "manual"


def test_trash_source_auto(client, upload):
    """单条移入传 source=auto：标记为自动移动。"""
    insp = upload().json()["id"]
    r = client.post(f"/api/inspirations/{insp}/trash", json={"reason": "质量差", "source": "auto"})
    assert r.status_code == 200
    assert r.json()["trash_source"] == "auto"


def test_batch_trash_source_auto(client, upload):
    """批量移入传 source=auto：垃圾桶列表全部标记为自动移动。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    r = client.post(
        "/api/inspirations/batch-trash",
        json={"ids": [a, b], "reason": "质量差", "source": "auto"},
    )
    assert r.status_code == 200
    assert r.json()["trashed"] == 2

    trash = client.get("/api/inspirations/trash").json()
    assert all(i["trash_source"] == "auto" for i in trash["items"])


def test_quality_rejected_trash_source_auto(client, upload):
    """质量审核被拒绝素材批量移入垃圾桶：来源标记为自动移动（质量分析自动进入）。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    client.patch(f"/api/inspirations/{a}", json={"quality_status": "rejected"})
    client.patch(f"/api/inspirations/{b}", json={"quality_status": "rejected"})

    r = client.delete("/api/inspirations/quality-rejected")
    assert r.status_code == 200
    assert r.json()["trashed"] == 2

    trash = client.get("/api/inspirations/trash").json()
    assert trash["total"] == 2
    assert all(i["trash_source"] == "auto" for i in trash["items"])
    assert all(i["trash_reason"] == "质量差" for i in trash["items"])


# ── 垃圾桶状态不变量（verify_trash_invariants）──


def _sql(statement: str, params: tuple = ()):
    """直接改库执行 SQL（构造违规数据用），返回行数。"""
    db_path = settings.storage_root.parent / "fashion_inspo.db"
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(statement, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


async def _verify() -> list[dict]:
    """调用 verify_trash_invariants 扫描全库。"""
    from app.database import async_session
    from app.services.inspiration_service import verify_trash_invariants

    async with async_session() as db:
        return await verify_trash_invariants(db)


async def test_trash_invariants_healthy_after_roundtrip(client, upload):
    """正常旅程（上传→移入→恢复）后不变量保持健康（0 违规）。"""
    insp = upload().json()["id"]
    client.post(f"/api/inspirations/{insp}/trash", json={"reason": "不喜欢"})
    assert await _verify() == []

    client.post(f"/api/inspirations/{insp}/restore")
    assert await _verify() == []


async def test_trash_invariants_detect_broken_state(client, upload):
    """手工制造半状态：缺原因（R1）/ 未删除残留来源（R3）均被检出。"""
    trashed = upload().json()["id"]
    active = upload().json()["id"]
    client.post(f"/api/inspirations/{trashed}/trash", json={"reason": "质量差"})

    # R1：垃圾桶素材删除原因被清空
    assert _sql("UPDATE inspirations SET trash_reason = NULL WHERE id = ?", (trashed,)) == 1
    # R3：未删除素材残留移入来源
    assert _sql("UPDATE inspirations SET trash_source = 'manual' WHERE id = ?", (active,)) == 1

    violations = await _verify()
    rules = {(v["rule"], v["id"]) for v in violations}
    assert ("R1", trashed) in rules
    assert ("R3", active) in rules
    assert not any(v["rule"] == "R2" for v in violations)  # 其余字段未被误报


async def test_integrity_check_includes_trash_invariants(client, upload):
    """管理页完整性检查返回垃圾桶不变量结果（空列表 = 健康）。"""
    upload()
    r = client.get("/api/admin/integrity-check")
    assert r.status_code == 200
    body = r.json()
    assert "trash_invariants" in body
    assert body["trash_invariants"] == []
