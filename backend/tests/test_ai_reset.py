"""数据重置（/api/ai/reset）测试：二次确认、API Key 认证、重置流程。"""

from app.config import settings


def test_reset_requires_confirm(client):
    """缺少 confirm=yes → 400（开发模式跳过认证）。"""
    r = client.delete("/api/ai/reset")
    assert r.status_code == 400
    assert "confirm=yes" in r.json()["detail"]


def test_reset_requires_api_key(client, monkeypatch):
    """配置 API_KEY 后：无密钥重置 → 401（破坏性接口双重防护之一）。"""
    monkeypatch.setattr(settings, "api_key", "test-secret-key")
    r = client.delete("/api/ai/reset", params={"confirm": "yes"})
    assert r.status_code == 401


def test_reset_clears_data_and_files(client, upload):
    """confirm=yes：清空素材、标签与存储文件。"""
    insp = upload().json()
    client.post(f"/api/inspirations/{insp['id']}/tags", json={"names": ["法式"]})

    r = client.delete("/api/ai/reset", params={"confirm": "yes"})
    assert r.status_code == 200
    data = r.json()
    assert data["files_deleted"] >= 1  # 上传的图片/缩略图目录被清空

    assert client.get("/api/inspirations").json()["total"] == 0
    assert client.get("/api/tags").json() == []


def test_reset_clears_persons(client, create_blogger, create_model):
    """confirm=yes：博主/模特表一并清空（修复「重置所有数据」但人物残留）。"""
    create_blogger(name="测试博主")
    create_model(name="测试模特")

    r = client.delete("/api/ai/reset", params={"confirm": "yes"})
    assert r.status_code == 200
    db_counts = r.json()["database"]
    assert db_counts["bloggers"] == 1
    assert db_counts["models"] == 1

    assert client.get("/api/bloggers").json()["total"] == 0
    assert client.get("/api/models").json()["total"] == 0
