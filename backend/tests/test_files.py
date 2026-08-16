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


def test_generate_filename_whitelists_extension():
    """上传文件名扩展名白名单：非图片/视频扩展名回退 .jpg（防存储型 XSS）。"""
    from app.services.file_service import _generate_filename

    assert _generate_filename("evil.html").endswith(".jpg")
    assert _generate_filename("x.svg").endswith(".jpg")
    assert _generate_filename("x.php").endswith(".jpg")
    assert _generate_filename("photo.png").endswith(".png")
    assert _generate_filename("clip.mp4").endswith(".mp4")


def test_serve_file_rejects_non_media_extension(client):
    """非图片/视频扩展名即使文件存在也不对外提供（防按 text/html 返回）。"""
    from app.config import settings

    p = settings.images_dir / "evil.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("<script>alert(1)</script>", encoding="utf-8")
    try:
        rel = p.relative_to(settings.storage_root).as_posix()
        assert client.get(f"/api/files/{rel}").status_code == 404
    finally:
        p.unlink(missing_ok=True)
