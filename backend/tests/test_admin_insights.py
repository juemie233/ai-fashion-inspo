"""素材管理后台洞察：CSV 导出、新增趋势、人物频次排行。"""


def test_export_csv(client, upload):
    """导出 CSV：返回可下载的文本/CSV，内容含表头与素材元数据。"""
    upload(source_author="测试博主", source_type="manual_upload")

    r = client.get("/api/admin/export")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]

    body = r.content.decode("utf-8-sig")
    assert "id" in body
    assert "测试博主" in body
    assert "manual_upload" in body


def test_export_csv_empty(client):
    """无素材时导出也应成功，返回仅表头的 CSV。"""
    r = client.get("/api/admin/export")
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    assert "id" in body


def test_trend(client, upload):
    """新增趋势：近 N 天统计至少包含刚上传的素材。"""
    upload()
    r = client.get("/api/admin/trend", params={"days": 30})
    assert r.status_code == 200
    data = r.json()
    assert data["days"] == 30
    assert sum(p["count"] for p in data["trend"]) >= 1


def test_person_frequency(client, upload, create_person):
    """人物频次：按关联素材数降序返回人物。"""
    insp_id = upload().json()["id"]
    person = create_person(name="高频博主")
    client.post(f"/api/inspirations/{insp_id}/persons", json={"person_ids": [person["id"]]})

    r = client.get("/api/admin/person-frequency")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "高频博主"
    assert data[0]["count"] == 1


def test_audit_log_batch_trash(client, upload):
    """批量移入垃圾桶会写入审计日志。"""
    a = upload().json()["id"]
    client.post("/api/inspirations/batch-trash", json={"ids": [a]})

    logs = client.get("/api/admin/audit-logs").json()
    assert len(logs) == 1
    assert logs[0]["action"] == "batch_trash"
    assert logs[0]["count"] == 1


def test_audit_log_empty_trash(client, upload):
    """清空垃圾桶会写入审计日志（含释放空间）。"""
    a = upload().json()["id"]
    client.post(f"/api/inspirations/{a}/trash", json={"reason": "不喜欢"})
    client.delete("/api/inspirations/trash")

    logs = client.get("/api/admin/audit-logs").json()
    actions = [l["action"] for l in logs]
    assert "empty_trash" in actions
    empty = next(l for l in logs if l["action"] == "empty_trash")
    assert empty["count"] == 1
    assert empty["freed_bytes"] > 0
