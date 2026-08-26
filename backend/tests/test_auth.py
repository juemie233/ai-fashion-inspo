"""破坏性接口 API Key 认证（安全加固回归测试）。"""

from app.config import settings

TEST_KEY = "pytest-secret-key"


def test_destructive_requires_key(client, monkeypatch):
    """配置 API_KEY 后：破坏性接口无 key 401、错 key 403、正确 key 通过。"""
    monkeypatch.setattr(settings, "api_key", TEST_KEY)

    # 无 key → 401
    r = client.delete("/api/inspirations/trash")
    assert r.status_code == 401
    assert "X-API-Key" in r.json()["detail"]

    # 错误 key → 403
    r = client.delete("/api/inspirations/trash", headers={"X-API-Key": "wrong"})
    assert r.status_code == 403

    # 正确 key → 通过（空库清空垃圾桶返回 200）
    r = client.delete("/api/inspirations/trash", headers={"X-API-Key": TEST_KEY})
    assert r.status_code == 200


def test_read_endpoints_unaffected(client, monkeypatch):
    """读接口不受认证影响。"""
    monkeypatch.setattr(settings, "api_key", TEST_KEY)
    assert client.get("/api/inspirations").status_code == 200
    assert client.get("/api/tags").status_code == 200
    assert client.get("/api/bloggers").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_recoverable_writes_unaffected(client, monkeypatch, upload):
    """可恢复写操作（移入垃圾桶）不受认证影响：素材不存在返回 404 而非 401。"""
    monkeypatch.setattr(settings, "api_key", TEST_KEY)
    r = client.post("/api/inspirations/no-such-id/trash", json={})
    assert r.status_code == 404


def test_other_destructive_endpoints(client, monkeypatch):
    """抽查其他破坏性端点（标签批量删除/合并、人物删除）→ 401。"""
    monkeypatch.setattr(settings, "api_key", TEST_KEY)

    r = client.post("/api/tags/batch-delete", json={"tag_ids": [1]})
    assert r.status_code == 401

    r = client.post("/api/tags/merge", json={"source_tag_id": 1, "target_tag_id": 2})
    assert r.status_code == 401

    r = client.delete("/api/bloggers/1")
    assert r.status_code == 401

    r = client.delete("/api/ai/history/999")
    assert r.status_code == 401

    # 任务队列历史记录删除（破坏性接口）
    r = client.delete("/api/tasks/1")
    assert r.status_code == 401


def test_tag_advanced_destructive_endpoints(client, monkeypatch):
    """高级标签的批量编辑/聚类应用/历史回滚 → 401（修复：此前未纳入清单）。"""
    monkeypatch.setattr(settings, "api_key", TEST_KEY)

    r = client.post("/api/tags/batch-edit", json={"dry_run": False, "rules": []})
    assert r.status_code == 401

    r = client.post("/api/tags/clusters/apply", json={"groups": []})
    assert r.status_code == 401

    r = client.post("/api/tags/history/1/rollback")
    assert r.status_code == 401


def test_destructive_without_key_config_skipped(client):
    """未配置 API_KEY（开发模式）：破坏性接口跳过认证，正常业务响应。"""
    assert settings.api_key == ""  # conftest 已清空
    r = client.delete("/api/bloggers/999")
    assert r.status_code == 404  # 人物不存在（而非 401）
    r = client.delete("/api/inspirations/trash")
    assert r.status_code == 200
