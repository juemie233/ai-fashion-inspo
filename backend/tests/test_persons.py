"""人物模块回归测试（博主/模特已拆分两表）：CRUD、素材关联、风格画像、删除。"""


# ═══════════════════════════════════════════════════════════════
#  穿搭博主（/api/bloggers）
# ═══════════════════════════════════════════════════════════════


def test_create_and_list_bloggers(client, create_blogger, create_model):
    blogger = create_blogger(name="小美", platform="xiaohongshu")
    create_model(name="Anna")

    lst = client.get("/api/bloggers").json()
    assert lst["total"] == 1
    assert lst["items"][0]["name"] == "小美"

    # 模特与博主互不可见（已物理拆分）
    assert client.get("/api/models").json()["total"] == 1

    # 搜索
    r = client.get("/api/bloggers", params={"search": "小美"}).json()
    assert r["total"] == 1


def test_create_person_validation(client):
    """空白名称 422；缺失名称 422（博主/模特一致）。"""
    assert client.post("/api/bloggers", json={"name": "   "}).status_code == 422
    assert client.post("/api/bloggers", json={}).status_code == 422
    assert client.post("/api/models", json={"name": "   "}).status_code == 422
    assert client.post("/api/models", json={}).status_code == 422


def test_blogger_detail_with_style_profile(client, create_blogger, upload):
    """详情含素材数 + 风格画像（标签聚合）。"""
    blogger = create_blogger(name="风格博主")
    insp_id = upload().json()["id"]

    # 关联博主
    r = client.post(
        f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [blogger["id"]]}
    )
    assert r.status_code == 200
    assert r.json()["count"] == 1

    # 给素材打标签（画像数据源）
    client.post(
        f"/api/inspirations/{insp_id}/tags",
        json={"names": ["法式", "白色"], "category": "style"},
    )

    detail = client.get(f"/api/bloggers/{blogger['id']}").json()
    assert detail["inspiration_count"] == 1
    assert detail["style_profile"]["top_tags"]
    assert {t["name"] for t in detail["style_profile"]["top_tags"]} == {"法式", "白色"}


def test_blogger_inspirations(client, create_blogger, upload):
    """博主素材列表端点。"""
    blogger = create_blogger(name="素材博主")
    insp_id = upload().json()["id"]
    client.post(
        f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [blogger["id"]]}
    )

    r = client.get(f"/api/bloggers/{blogger['id']}/inspirations").json()
    assert r["total"] == 1
    assert r["items"][0]["inspiration_id"] == insp_id


def test_link_blogger_missing_inspiration_404(client, create_blogger):
    blogger = create_blogger(name="孤儿博主")
    r = client.post(
        "/api/inspirations/no-such-id/bloggers", json={"person_ids": [blogger["id"]]}
    )
    assert r.status_code == 404


def test_unlink_blogger(client, create_blogger, upload):
    blogger = create_blogger(name="解除博主")
    insp_id = upload().json()["id"]
    client.post(
        f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [blogger["id"]]}
    )

    r = client.delete(f"/api/inspirations/{insp_id}/bloggers/{blogger['id']}")
    assert r.status_code == 200

    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["bloggers"] == []
    assert detail["models"] == []


def test_delete_blogger_blocked_when_has_inspirations(client, create_blogger, upload):
    """有关联素材时禁止删除：返回 400 与明确提示，博主与关联均保留。"""
    blogger = create_blogger(name="待删除")
    insp_id = upload().json()["id"]
    client.post(
        f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [blogger["id"]]}
    )

    r = client.delete(f"/api/bloggers/{blogger['id']}")
    assert r.status_code == 400
    assert r.json()["detail"] == "该博主下仍有 1 个素材，无法删除"

    # 博主未被删除，素材关联仍保留
    assert client.get("/api/bloggers").json()["total"] == 1
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert [p["id"] for p in detail["bloggers"]] == [blogger["id"]]


def test_delete_blogger_allowed_when_no_inspirations(client, create_blogger, upload):
    """无关联素材时删除成功，素材保留且不再关联该博主。"""
    blogger = create_blogger(name="待删除")
    insp_id = upload().json()["id"]
    # 先关联再解除，模拟素材已无博主归属
    client.post(
        f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [blogger["id"]]}
    )
    assert (
        client.delete(f"/api/inspirations/{insp_id}/bloggers/{blogger['id']}").status_code
        == 200
    )

    r = client.delete(f"/api/bloggers/{blogger['id']}")
    assert r.status_code == 204

    assert client.get("/api/bloggers").json()["total"] == 0
    # 素材仍存在
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["id"] == insp_id
    assert detail["bloggers"] == []


def test_delete_blogger_ignores_trashed_inspirations(client, create_blogger, upload):
    """垃圾桶（软删除）素材不计入关联数：显示素材数为 0 时允许删除，口径与列表一致。"""
    blogger = create_blogger(name="垃圾桶关联")
    insp_id = upload().json()["id"]
    client.post(
        f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [blogger["id"]]}
    )
    # 素材移入垃圾桶 → 有效素材数为 0
    client.post(f"/api/inspirations/{insp_id}/trash", json={"reason": "质量差"})

    assert client.delete(f"/api/bloggers/{blogger['id']}").status_code == 204


def test_delete_blogger_missing_404(client):
    """删除不存在的博主返回 404。"""
    r = client.delete("/api/bloggers/999999")
    assert r.status_code == 404
    assert r.json()["detail"] == "博主未找到"


def test_blogger_suggestions_and_top(client, create_blogger, upload):
    create_blogger(name="热门博主")
    r = client.get("/api/bloggers/suggestions", params={"name": "热门"}).json()
    assert len(r) == 1

    top = client.get("/api/bloggers/top").json()
    assert len(top) == 1


def test_update_blogger_clear_nullable(client, create_blogger):
    """PATCH 显式传 null 可清空可空字段（bio）。"""
    blogger = create_blogger(name="更新博主", bio="旧简介")
    r = client.patch(f"/api/bloggers/{blogger['id']}", json={"bio": None})
    assert r.status_code == 200
    assert r.json()["bio"] is None
    assert r.json()["name"] == "更新博主"


# ═══════════════════════════════════════════════════════════════
#  职业模特（/api/models）
# ═══════════════════════════════════════════════════════════════


def test_model_crud_and_link(client, create_model, upload):
    """模特 CRUD + 素材关联 + 删除限制。"""
    model = create_model(name="Anna")
    insp_id = upload().json()["id"]

    r = client.post(
        f"/api/inspirations/{insp_id}/models", json={"person_ids": [model["id"]]}
    )
    assert r.status_code == 200
    assert r.json()["count"] == 1

    detail = client.get(f"/api/models/{model['id']}").json()
    assert detail["inspiration_count"] == 1

    # 有关联素材禁止删除
    r = client.delete(f"/api/models/{model['id']}")
    assert r.status_code == 400
    assert "模特" in r.json()["detail"]

    # 解除关联后可删除
    assert (
        client.delete(f"/api/inspirations/{insp_id}/models/{model['id']}").status_code
        == 200
    )
    assert client.delete(f"/api/models/{model['id']}").status_code == 204

    # 素材详情中关联清空
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["models"] == []


def test_model_missing_404(client):
    r = client.delete("/api/models/999999")
    assert r.status_code == 404
    assert r.json()["detail"] == "模特未找到"


# ═══════════════════════════════════════════════════════════════
#  CSV 导入（博主专属：按 xhs_id upsert）
# ═══════════════════════════════════════════════════════════════


def _upload_csv(client, content: bytes, filename: str = "bloggers.csv"):
    """上传 CSV 到导入接口。"""
    return client.post(
        "/api/bloggers/import-csv",
        files={"file": (filename, content, "text/csv")},
    )


def test_import_csv_basic(client):
    """UTF-8（带 BOM）CSV 导入：表头匹配、成功计数、列表可见。"""
    content = "\ufeffnickname,xhs_id,ip_location\n水色结-,zhn20050228,河北\nZoe菲,8976332534,黑龙江\n".encode("utf-8")
    r = _upload_csv(client, content)
    assert r.status_code == 200
    data = r.json()
    assert data["imported"] == 2
    assert data["updated"] == 0
    assert data["failed"] == 0

    lst = client.get("/api/bloggers").json()
    assert lst["total"] == 2
    item = next(p for p in lst["items"] if p["xhs_id"] == "zhn20050228")
    assert item["name"] == "水色结-"
    assert item["ip_location"] == "河北"


def test_import_csv_upsert_no_duplicate(client):
    """重复导入相同 xhs_id：不产生重复记录，而是更新昵称与 IP。"""
    csv1 = "nickname,xhs_id,ip_location\n博主A,abc123,浙江\n".encode("utf-8")
    assert _upload_csv(client, csv1).json()["imported"] == 1

    # 第二次导入相同 xhs_id，昵称/IP 变更
    csv2 = "nickname,xhs_id,ip_location\n博主A-新名,abc123,上海\n".encode("utf-8")
    r = _upload_csv(client, csv2).json()
    assert r["imported"] == 0
    assert r["updated"] == 1

    lst = client.get("/api/bloggers").json()
    assert lst["total"] == 1  # 无重复记录
    item = lst["items"][0]
    assert item["name"] == "博主A-新名"
    assert item["ip_location"] == "上海"


def test_import_csv_reordered_columns(client):
    """表头顺序不一致（xhs_id 在前）仍按表头名正确匹配。"""
    content = "xhs_id,nickname,ip_location\nabc111,博主B,江苏\n".encode("utf-8")
    r = _upload_csv(client, content).json()
    assert r["imported"] == 1

    lst = client.get("/api/bloggers").json()
    assert lst["items"][0]["name"] == "博主B"
    assert lst["items"][0]["ip_location"] == "江苏"


def test_import_csv_missing_required(client):
    """缺失 nickname / xhs_id 的行计入 failed 并给出原因。"""
    content = "nickname,xhs_id,ip_location\n,abc222,浙江\n博主C,,广东\n博主D,def333,湖北\n".encode("utf-8")
    r = _upload_csv(client, content).json()
    assert r["imported"] == 1
    assert r["failed"] == 2
    reasons = {e["reason"] for e in r["errors"]}
    assert reasons == {"昵称为空", "小红书号为空"}

    assert client.get("/api/bloggers").json()["total"] == 1


def test_import_csv_duplicate_in_file(client):
    """CSV 文件内重复 xhs_id：合并为一行，后出现者覆盖，计入 skipped。"""
    content = (
        "nickname,xhs_id,ip_location\n博主E,xhs999,浙江\n博主E新名,xhs999,上海\n"
    ).encode("utf-8")
    r = _upload_csv(client, content).json()
    assert r["imported"] == 1
    assert r["skipped"] == 1

    lst = client.get("/api/bloggers").json()
    assert lst["total"] == 1
    assert lst["items"][0]["name"] == "博主E新名"
    assert lst["items"][0]["ip_location"] == "上海"


def test_import_csv_missing_header(client):
    """缺少必填列（xhs_id）返回 400 与明确提示。"""
    content = "nickname,ip_location\n博主F,浙江\n".encode("utf-8")
    r = _upload_csv(client, content)
    assert r.status_code == 400
    assert "xhs_id" in r.json()["detail"]


def test_import_csv_non_utf8(client):
    """非 UTF-8 编码（GBK）返回 400。"""
    content = "nickname,xhs_id,ip_location\n博主G,abc444,浙江\n".encode("gbk")
    r = _upload_csv(client, content)
    assert r.status_code == 400
    assert "UTF-8" in r.json()["detail"]


def test_search_by_xhs_id_and_ip(client):
    """列表搜索支持小红书号与 IP 属地命中。"""
    content = (
        "nickname,xhs_id,ip_location\n博主H,xhs888,浙江\n博主I,xhs777,广东\n"
    ).encode("utf-8")
    _upload_csv(client, content)

    # 按小红书号搜索
    r = client.get("/api/bloggers", params={"search": "xhs777"}).json()
    assert r["total"] == 1
    assert r["items"][0]["name"] == "博主I"

    # 按 IP 属地搜索
    r = client.get("/api/bloggers", params={"search": "浙江"}).json()
    assert r["total"] == 1
    assert r["items"][0]["name"] == "博主H"
