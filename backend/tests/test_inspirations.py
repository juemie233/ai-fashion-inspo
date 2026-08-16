"""素材核心链路：上传、列表、详情、收藏、内容去重、软删除过滤。"""


def test_upload_and_list(client, upload):
    """上传素材后可在列表与详情中查询到。"""
    r = upload(source_type="manual_upload")
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["id"]
    assert data["media_type"] == "image"
    assert data["quality_status"] == "approved"  # 手动上传默认免审核
    assert data["source_type"] == "manual_upload"

    # 列表
    lst = client.get("/api/inspirations").json()
    assert lst["total"] == 1
    assert lst["items"][0]["id"] == data["id"]

    # 详情
    detail = client.get(f"/api/inspirations/{data['id']}")
    assert detail.status_code == 200
    assert detail.json()["file_path"]


def test_upload_rejects_unsupported_type(client):
    """不支持的文件类型应 400。"""
    files = {"file": ("a.txt", b"hello", "text/plain")}
    r = client.post("/api/inspirations", files=files)
    assert r.status_code == 400


def test_content_deduplication(client, upload):
    """相同内容二次上传应 409（内容 SHA-256 去重，验收标准之一）。"""
    assert upload(color=(200, 100, 50)).status_code == 201
    r = upload(color=(200, 100, 50))
    assert r.status_code == 409
    assert "重复" in r.json()["detail"]


def test_platform_id_deduplication(client, upload):
    """相同平台 ID 二次上传应 409（内容不同，仅平台 ID 冲突）。"""
    assert upload(source_platform_id="xh_123").status_code == 201
    r = upload(color=(10, 20, 30), source_platform_id="xh_123")
    assert r.status_code == 409


def test_soft_delete_filtering(client, upload):
    """软删除过滤：素材移入垃圾桶后不再出现在正常列表（验收标准之一）。"""
    insp_id = upload().json()["id"]

    # 移入垃圾桶
    r = client.post(f"/api/inspirations/{insp_id}/trash", json={"reason": "不喜欢"})
    assert r.status_code == 200
    assert r.json()["deleted_at"] is not None
    assert r.json()["trash_reason"] == "不喜欢"

    # 正常列表不再包含（软删除过滤）
    lst = client.get("/api/inspirations").json()
    assert lst["total"] == 0

    # 垃圾桶列表包含
    trash = client.get("/api/inspirations/trash").json()
    assert trash["total"] == 1
    assert trash["items"][0]["id"] == insp_id


def test_favorite_toggle(client, upload):
    """收藏状态切换。"""
    insp_id = upload().json()["id"]
    r = client.patch(f"/api/inspirations/{insp_id}", json={"is_favorite": True})
    assert r.status_code == 200
    assert r.json()["is_favorite"] is True

    r = client.patch(f"/api/inspirations/{insp_id}", json={"is_favorite": False})
    assert r.json()["is_favorite"] is False


def test_physical_delete(client, upload):
    """物理删除后列表与垃圾桶均不再包含。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/trash")
    r = client.delete(f"/api/inspirations/{insp_id}")
    assert r.status_code == 204

    assert client.get("/api/inspirations").json()["total"] == 0
    assert client.get("/api/inspirations/trash").json()["total"] == 0


def test_detail_not_found(client):
    assert client.get("/api/inspirations/no-such-id").status_code == 404


def test_tag_filter_include(client, upload):
    """按标签筛选（AND 语义）：只返回包含指定标签的素材。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    client.post(f"/api/inspirations/{a}/tags", json={"names": ["黑色"], "category": "color"})
    client.post(f"/api/inspirations/{b}/tags", json={"names": ["白色"], "category": "color"})

    r = client.get("/api/inspirations", params={"include_tags": "黑色"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["id"] == a


def test_tag_count_sort(client, upload):
    """按标签数量降序排序：标签丰富者在前。"""
    a = upload().json()["id"]
    upload()  # b 无标签
    client.post(f"/api/inspirations/{a}/tags", json={"names": ["黑色", "白色"], "category": "color"})

    ids = [i["id"] for i in client.get("/api/inspirations", params={"sort": "tag_count"}).json()["items"]]
    assert ids[0] == a


def test_batch_favorite(client, upload):
    """批量收藏素材。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    r = client.post("/api/inspirations/batch-favorite", json={"ids": [a, b], "is_favorite": True})
    assert r.status_code == 200
    assert r.json()["updated"] == 2

    favs = {i["id"]: i["is_favorite"] for i in client.get("/api/inspirations").json()["items"]}
    assert favs[a] is True and favs[b] is True


def test_batch_trash(client, upload):
    """批量移入垃圾桶：正常列表清空、垃圾桶包含全部。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    r = client.post("/api/inspirations/batch-trash", json={"ids": [a, b]})
    assert r.status_code == 200
    assert r.json()["trashed"] == 2

    assert client.get("/api/inspirations").json()["total"] == 0
    assert client.get("/api/inspirations/trash").json()["total"] == 2


def test_batch_update(client, upload):
    """批量编辑元数据（来源/审核状态/疑似 AI 标记）。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    r = client.post(
        "/api/inspirations/batch-update",
        json={"ids": [a, b], "source_type": "douyin", "quality_status": "rejected", "is_ai_generated": True},
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 2

    for i in client.get("/api/inspirations").json()["items"]:
        assert i["source_type"] == "douyin"
        assert i["quality_status"] == "rejected"
        assert i["is_ai_generated"] is True
