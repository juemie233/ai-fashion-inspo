"""健康检查与 schema 版本握手。"""


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["app"] == "Fashion Inspo"
    assert data["schema_version"]  # 前后端契约握手值


def test_health_schema_version_format(client):
    """schema_version 应为 {db_hash}-{api_contract_version} 形式。"""
    version = client.get("/api/health").json()["schema_version"]
    assert "-" in version
    head, tail = version.rsplit("-", 1)
    assert len(head) == 8  # SHA-256 前 8 位
    assert tail.isdigit()
