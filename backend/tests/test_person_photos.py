"""人物照片组回归测试：创建/列表/详情、上传照片、组内去重、删除与级联清理。"""


def _create_set(client, person, name="街拍一组"):
    r = client.post(f"/api/models/{person['id']}/photo-sets", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _upload_photo(client, person, set_id, color=(10, 20, 30), filename="a.jpg", sort_order=0):
    data, ctype = _make_photo_bytes(color)
    files = {"file": (filename, data, ctype)}
    data_fields = {"sort_order": str(sort_order)}
    return client.post(
        f"/api/models/{person['id']}/photo-sets/{set_id}/photos",
        files=files,
        data=data_fields,
    )


def _make_photo_bytes(color):
    """生成一张测试图片字节与 content_type（避免依赖 fixtures 的模块级单例）。"""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="JPEG")
    buf.seek(0)
    return buf.getvalue(), "image/jpeg"


def test_create_and_list_photo_sets(client, create_model):
    person = create_model(name="Anna", )
    _create_set(client, person, "棚拍")
    _create_set(client, person, "街拍")

    r = client.get(f"/api/models/{person['id']}/photo-sets").json()
    assert r["total"] == 2
    assert {s["name"] for s in r["items"]} == {"棚拍", "街拍"}

    # 空名回退「未命名照片组」
    r2 = client.post(f"/api/models/{person['id']}/photo-sets", json={"name": "   "})
    assert r2.status_code == 201
    assert r2.json()["name"] == "未命名照片组"


def test_upload_and_detail_photos(client, create_model):
    person = create_model(name="ModelA", )
    s = _create_set(client, person)

    r = _upload_photo(client, person, s["id"], color=(1, 1, 1), filename="01.jpg", sort_order=0)
    assert r.status_code == 201, r.text
    assert r.json()["sort_order"] == 0
    assert r.json()["file_path"].startswith("person_photos/")

    _upload_photo(client, person, s["id"], color=(2, 2, 2), filename="02.jpg", sort_order=1)

    detail = client.get(f"/api/models/{person['id']}/photo-sets/{s['id']}").json()
    assert detail["photo_count"] == 2
    assert detail["cover_path"]
    assert len(detail["photos"]) == 2
    # 照片按 sort_order 升序
    assert detail["photos"][0]["sort_order"] == 0

    # 列表返回封面与数量
    lst = client.get(f"/api/models/{person['id']}/photo-sets").json()
    assert lst["items"][0]["photo_count"] == 2
    assert lst["items"][0]["cover_path"]


def test_photo_dedup_within_set(client, create_model):
    person = create_model(name="ModelB", )
    s = _create_set(client, person)

    assert _upload_photo(client, person, s["id"], color=(9, 9, 9)).status_code == 201
    # 同内容重复上传 → 409
    assert _upload_photo(client, person, s["id"], color=(9, 9, 9)).status_code == 409


def test_delete_photo_and_set_cleanup(client, create_model):
    from app.config import settings

    person = create_model(name="ModelC", )
    s = _create_set(client, person)

    r1 = _upload_photo(client, person, s["id"], color=(3, 3, 3))
    photo_id = r1.json()["id"]

    # 单张删除
    assert (
        client.delete(
            f"/api/models/{person['id']}/photo-sets/{s['id']}/photos/{photo_id}"
        ).status_code
        == 200
    )
    detail = client.get(f"/api/models/{person['id']}/photo-sets/{s['id']}").json()
    assert detail["photo_count"] == 0

    # 删除照片组 → 组与照片记录清空
    assert (
        client.delete(f"/api/models/{person['id']}/photo-sets/{s['id']}").status_code
        == 204
    )
    assert client.get(f"/api/models/{person['id']}/photo-sets").json()["total"] == 0


def test_delete_person_cascades_photo_files(client, create_model):
    from app.config import settings

    person = create_model(name="ModelD", )
    s = _create_set(client, person)
    r = _upload_photo(client, person, s["id"], color=(4, 4, 4))
    photo_path = r.json()["file_path"]

    root = settings.storage_root
    assert (root / photo_path).exists()

    assert client.delete(f"/api/models/{person['id']}").status_code == 204
    # 物理文件随人物删除一并清理
    assert not (root / photo_path).exists()


def test_photo_set_missing_person_404(client):
    r = client.post("/api/models/999999/photo-sets", json={"name": "x"})
    assert r.status_code == 404


def test_photo_set_wrong_person_404(client, create_model):
    p1 = create_model(name="P1")
    p2 = create_model(name="P2")
    s = _create_set(client, p1)
    # 用另一个 person_id 访问该组 → 404
    r = client.get(f"/api/models/{p2['id']}/photo-sets/{s['id']}")
    assert r.status_code == 404


def test_delete_photo_wrong_set_404(client, create_model):
    """用错误的照片组 ID 删除照片 → 404（防跨组误删，照片保留）。"""
    person = create_model(name="ModelE", )
    s1 = _create_set(client, person, "组一")
    s2 = _create_set(client, person, "组二")
    r = _upload_photo(client, person, s1["id"], color=(5, 5, 5))
    photo_id = r.json()["id"]

    # 用组二的 set_id 删除组一的照片 → 404，且照片仍存在
    resp = client.delete(
        f"/api/models/{person['id']}/photo-sets/{s2['id']}/photos/{photo_id}"
    )
    assert resp.status_code == 404
    detail = client.get(f"/api/models/{person['id']}/photo-sets/{s1['id']}").json()
    assert detail["photo_count"] == 1


def test_photo_set_delete_missing_404(client, create_model):
    person = create_model(name="P3")
    # 删除不存在的照片组 → 404（而非 500）
    r = client.delete(f"/api/models/{person['id']}/photo-sets/999999")
    assert r.status_code == 404
