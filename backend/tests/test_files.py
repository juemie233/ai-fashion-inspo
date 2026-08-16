"""静态文件服务与路径安全测试。"""


def test_serve_file_ok(client, upload):
    """上传的图片可通过 /api/files/ 访问，且带 nosniff 响应头。"""
    insp = upload().json()
    r = client.get(f"/api/files/{insp['file_path']}")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"


def test_serve_file_not_found(client):
    """不存在的文件返回 404。"""
    assert client.get("/api/files/images/no-such.jpg").status_code == 404


def test_serve_file_traversal_denied(client):
    """路径穿越应被拒绝（400/403/404），绝不能 200 或泄露库外文件。"""
    r = client.get("/api/files/..%2F..%2Ffashion_inspo.db")
    assert r.status_code in (400, 403, 404)
    assert r.status_code != 200
