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


def test_person_frequency(client, upload, create_blogger):
    """人物频次：按关联素材数降序返回人物（博主/模特合并统计）。"""
    insp_id = upload().json()["id"]
    blogger = create_blogger(name="高频博主")
    client.post(
        f"/api/inspirations/{insp_id}/bloggers",
        json={"person_ids": [blogger["id"]]},
    )

    r = client.get("/api/admin/person-frequency")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "高频博主"
    assert data[0]["person_type"] == "blogger"
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


def test_admin_stats(client, upload):
    """素材总览统计：素材总数与来源分布。"""
    upload()
    data = client.get("/api/admin/stats").json()
    assert data["total_count"] == 1
    sources = {s["source_type"]: s["count"] for s in data["by_source_type"]}
    assert sources["manual_upload"] == 1


def test_near_duplicate_scan(client):
    """近似重复检测：同构图不同尺寸的两张图应被归入同一组；哈希缓存渐进补齐。"""
    import io

    from PIL import Image

    def _structured(size):
        img = Image.new("RGB", size, (255, 255, 255))
        px = img.load()
        for y in range(size[1]):
            for x in range(size[0]):
                v = 255 if x >= size[0] // 2 else 0
                if abs(x - y) < 3:
                    v = 128
                px[x, y] = (v, v, v)
        return img

    def _bytes(img):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        buf.seek(0)
        return buf.getvalue()

    base = _structured((64, 64))
    a = _bytes(base)
    b = _bytes(base.resize((48, 48)).resize((64, 64)))

    ra = client.post("/api/inspirations", files={"file": ("a.jpg", a, "image/jpeg")})
    rb = client.post("/api/inspirations", files={"file": ("b.jpg", b, "image/jpeg")})
    assert ra.status_code == 201 and rb.status_code == 201

    # 首次扫描：全库 2 张均无哈希缓存，全部补算并参与分组
    res = client.post("/api/admin/near-duplicates", json={"threshold": 32}).json()
    assert res["scanned"] == 2
    assert res["backfilled"] == 2
    assert res["cached_total"] == 2
    assert len(res["groups"]) == 1
    ids = {f["id"] for f in res["groups"][0]["files"]}
    assert ids == {ra.json()["id"], rb.json()["id"]}
    # 每组应给出保留建议
    assert res["groups"][0]["keeper_id"] in ids

    # 二次扫描：哈希已缓存，零补算、零解码，结果一致
    res2 = client.post("/api/admin/near-duplicates", json={"threshold": 32}).json()
    assert res2["backfilled"] == 0
    assert res2["scanned"] == 2
    assert len(res2["groups"]) == 1


def test_near_duplicate_scan_backfill_visibility(client):
    """近似重复扫描：backfill 写入的 phash 必须在随机抽样中可见。

    验证首次扫描时 scanned 数正确（backfill 的 phash 在随机抽样中可见）。
    这是之前 bug 的回归测试：commit 后随机抽样可能读到旧快照，
    导致 scanned 数偏少（如请求 5000 却只显示 300）。
    """
    import io
    import random

    from PIL import Image

    def _rand_img(seed):
        """用随机噪点生成视觉唯一的图片，避免 SHA-256 和 phash 去重拦截。"""
        random.seed(seed)
        img = Image.new("RGB", (64, 64))
        img.putdata([
            (random.randint(0, 255), random.randint(0, 127), random.randint(0, 255))
            for _ in range(64 * 64)
        ])
        return img

    def _bytes_pil(img):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        buf.seek(0)
        return buf.getvalue()

    # 上传 500 张唯一图片
    all_ids = []
    for i in range(500):
        img = _rand_img(i)
        r = client.post(
            "/api/inspirations",
            files={"file": (f"test{i}.jpg", _bytes_pil(img), "image/jpeg")},
        )
        assert r.status_code == 201, f"upload {i} failed: {r.text}"
        all_ids.append(r.json()["id"])

    # 首次扫描：500 张均无 phash，循环回卷 backfill 直至全部补齐
    # limit=5000 → 库中只有 500 张，所以 scanned 应 = 500（min(500, 5000)）
    # 关键验证：backfill 写入的 phash 必须在随机抽样中可见
    res = client.post(
        "/api/admin/near-duplicates", json={"limit": 5000, "threshold": 32}
    ).json()
    assert res["backfilled"] == 500, f"backfilled={res['backfilled']} should be 500 (all images)"
    assert res["cached_total"] == res["backfilled"], (
        f"cached_total={res['cached_total']} != backfilled={res['backfilled']}"
    )
    assert res["scanned"] == 500, (
        f"BUG! scanned={res['scanned']} should be 500 when limit=5000 and total=500"
    )
    assert res["truncated"] is False

    # 第二次扫描：全部哈希已缓存，scanned 应该 = total = 500
    res2 = client.post(
        "/api/admin/near-duplicates", json={"limit": 5000, "threshold": 32}
    ).json()
    assert res2["backfilled"] == 0
    assert res2["scanned"] == 500
    assert res2["truncated"] is False
