"""数据重置（/api/ai/reset）测试：四重防呆 + 重置流程。

四重防呆（T5）：
1. 执行前快照 DB + 素材目录（保留 7 天）；
2. confirm=yes 且 confirm_text=DELETE 双重确认；
3. 未配 API Key 时非回环来源拒绝（403）；
4. 完成后写 audit_logs。
"""

import pytest

from app.config import settings
from app.routers import ai_reset


def _reset(client, **params):
    """带默认双重确认参数的 reset 请求快捷方式。"""
    base = {"confirm": "yes", "confirm_text": "DELETE"}
    base.update(params)
    return client.delete("/api/ai/reset", params=base)


# ── 防护 2：确认文字 ──


def test_reset_requires_confirm(client):
    """缺少 confirm=yes → 400。"""
    r = client.delete("/api/ai/reset")
    assert r.status_code == 400
    assert "confirm=yes" in r.json()["detail"]


def test_reset_requires_confirm_text(client):
    """有 confirm=yes 但缺 confirm_text=DELETE → 400（防误点）。"""
    r = client.delete("/api/ai/reset", params={"confirm": "yes"})
    assert r.status_code == 400
    assert "DELETE" in r.json()["detail"]


def test_reset_rejects_wrong_confirm_text(client):
    """confirm_text 输入错误 → 400，不执行任何删除。"""
    r = client.delete(
        "/api/ai/reset", params={"confirm": "yes", "confirm_text": "delete"}
    )
    assert r.status_code == 400


# ── 防护 3：未配 Key + 非回环 → 403 ──


def test_reset_loopback_host_allowed_without_key(client, monkeypatch):
    """确认参数齐全 + 本机回环来源，即使未配 Key 也放行（开发模式）。"""
    monkeypatch.setattr(settings, "api_key", "")
    # 上传一条数据，确认 reset 实际被执行（返回 200 + 清空）
    client.post("/api/upload", files={"file": ("a.webp", _tiny_png(), "image/webp")})
    r = _reset(client)
    assert r.status_code == 200


def test_reset_non_loopback_rejected_without_key(client, monkeypatch):
    """未配 API Key 且来源非回环 → 403（局域网裸奔兜底）。"""
    monkeypatch.setattr(settings, "api_key", "")
    # 把回环判定强制为 False，模拟外部 IP 访问
    monkeypatch.setattr(ai_reset, "_is_loopback", lambda host: False)
    r = _reset(client)
    assert r.status_code == 403
    assert "本机" in r.json()["detail"]


def test_reset_non_loopback_allowed_with_key(client, monkeypatch):
    """配了 API Key 且携带正确密钥时，非回环来源允许执行（Key 是真正的防线）。"""
    monkeypatch.setattr(settings, "api_key", "secret-key")
    monkeypatch.setattr(ai_reset, "_is_loopback", lambda host: False)
    r = client.delete(
        "/api/ai/reset",
        params={"confirm": "yes", "confirm_text": "DELETE"},
        headers={"X-API-Key": "secret-key"},
    )
    assert r.status_code == 200


# ── 防护 1：执行前快照 ──


def _tiny_png() -> bytes:
    # 1x1 PNG
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082"
    )


def test_reset_creates_snapshot(client, upload):
    """reset 前自动生成快照：DB 副本 + images 目录移入快照，原位重建空目录。"""
    upload()  # 产生图片与缩略图

    r = _reset(client)
    assert r.status_code == 200
    snap_rel = r.json().get("snapshot")
    assert snap_rel, "响应应返回快照路径"

    import sqlite3
    from pathlib import Path

    snap = Path(snap_rel)
    assert snap.exists()
    # 快照含 DB 一致性副本（integrity_check 通过）
    snap_db = snap / "fashion_inspo.db"
    assert snap_db.exists()
    con = sqlite3.connect(str(snap_db))
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # 快照应保留 reset 前的素材数据（至少 1 条）
        assert con.execute("SELECT COUNT(*) FROM inspirations").fetchone()[0] >= 1
    finally:
        con.close()
    # 快照捕获了 images（上传的素材被移走）
    assert (snap / "images").exists()
    # 原位 images 被重建为空目录（reset 后可继续上传）
    assert settings.images_dir.exists()


def test_snapshot_retention_cleans_old(client, monkeypatch, tmp_path):
    """清理函数删除超过 7 天的快照，保留近期快照。"""
    import os
    import time

    snap_root = settings.storage_root / "_pre_reset_snapshot"
    snap_root.mkdir(parents=True, exist_ok=True)

    old = snap_root / "20200101_000000"
    new = snap_root / "20990101_000000"
    old.mkdir()
    new.mkdir()

    # old 标记为 30 天前
    old_ts = time.time() - 30 * 86400
    os.utime(old, (old_ts, old_ts))

    cleaned = ai_reset.cleanup_expired_snapshots()
    assert cleaned >= 1
    assert not old.exists()
    assert new.exists()


# ── 防护 4：审计留痕 ──


def test_reset_writes_audit_log(client, upload):
    """reset 完成后写一条 audit_logs（action=reset）。"""
    upload()
    _reset(client)

    r = client.get("/api/admin/audit-logs", params={"limit": 10})
    assert r.status_code == 200
    logs = r.json()
    reset_logs = [log for log in logs if log.get("action") == "reset"]
    assert len(reset_logs) >= 1
    # 审计 detail 记录了快照与来源
    detail = reset_logs[0].get("detail") or ""
    assert "snapshot" in detail or "source_ip" in detail


# ── 重置正确性（回归）──


def test_reset_clears_data_and_files(client, upload):
    """confirm 齐全：清空素材、标签与存储文件。"""
    insp = upload().json()
    client.post(f"/api/inspirations/{insp['id']}/tags", json={"names": ["法式"]})

    r = _reset(client)
    assert r.status_code == 200
    data = r.json()
    assert data["files_deleted"] >= 1  # 上传的图片/缩略图目录被清空

    assert client.get("/api/inspirations").json()["total"] == 0
    assert client.get("/api/tags").json() == []


def test_reset_clears_persons(client, create_blogger, create_model):
    """confirm 齐全：博主/模特表一并清空。"""
    create_blogger(name="测试博主")
    create_model(name="测试模特")

    r = _reset(client)
    assert r.status_code == 200
    db_counts = r.json()["database"]
    assert db_counts["bloggers"] == 1
    assert db_counts["models"] == 1

    assert client.get("/api/bloggers").json()["total"] == 0
    assert client.get("/api/models").json()["total"] == 0
