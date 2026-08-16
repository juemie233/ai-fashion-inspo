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


def test_purge_only_expired(client, upload):
    """only_expired=True 只清理超过保留期的素材，未过期保留。"""
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


def test_restore_missing(client):
    assert client.post("/api/inspirations/no-such/restore").status_code == 404
