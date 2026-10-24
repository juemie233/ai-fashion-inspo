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

    # 扫描接口**只读缓存**（实测请求内补 300 张要 34 秒，超过前端 30 秒上限），
    # 所以先显式补算，再扫描——这也正是 UI 上「后台补齐哈希缓存」按钮做的事
    from app.database import async_session

    from app.services.near_duplicate_service import backfill_phash_cache

    async def _backfill() -> dict:
        async with async_session() as _db:
            return await backfill_phash_cache(_db, budget_seconds=60.0)

    import asyncio

    assert asyncio.run(_backfill())["computed"] == 2

    res = client.post("/api/admin/near-duplicates", json={"threshold": 32}).json()
    assert res["scanned"] == 2
    # 扫描本身零补算（只读缓存）
    assert res["backfilled"] == 0
    assert res["cached_total"] == 2
    assert len(res["groups"]) == 1
    ids = {f["id"] for f in res["groups"][0]["files"]}
    assert ids == {ra.json()["id"], rb.json()["id"]}
    # 每组应给出保留建议
    assert res["groups"][0]["keeper_id"] in ids

    # 二次扫描：哈希已缓存，零补算、零解码，结果一致
    res2 = client.post("/api/admin/near-duplicates", json={"threshold": 32}).json()
    assert res2["backfilled"] == 0
    assert res2["cache_complete"] is True
    assert res2["scanned"] == 2
    assert len(res2["groups"]) == 1


def test_near_duplicate_full_scan_accepted(client, upload):
    """limit=0 全库扫描必须被接受（回归：请求模型曾限制 ge=1，全量被 422 拦截）。

    随机抽样在大库中漏检率高（重复对未必同批抽中），全量扫描是「不漏检」
    的主路径；服务层约定 limit=0 表示不限制，请求模型需放行。
    """
    upload()
    # 扫描只读缓存 → 先补算（真实路径就是「后台补齐哈希缓存」任务）
    import asyncio

    from app.database import async_session

    from app.services.near_duplicate_service import backfill_phash_cache

    async def _backfill() -> dict:
        async with async_session() as _db:
            return await backfill_phash_cache(_db, budget_seconds=30.0)

    assert asyncio.run(_backfill())["computed"] == 1

    r = client.post("/api/admin/near-duplicates", json={"limit": 0, "threshold": 32})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scanned"] == 1
    assert data["truncated"] is False  # 全量扫描不截断


async def test_near_duplicate_scan_backfill_visibility(client):
    """近似重复扫描：独立补算提交的 phash 必须在扫描的随机抽样中可见。

    回归两件事：
    1. 补算是独立事务（后台任务 / 独立会话）提交的，扫描的随机抽样要能读到
       ——commit 后同会话读到旧快照会让 scanned 偏少（如请求 5000 却只有 300）；
    2. 扫描接口本身**不再补算**（请求内补 300 张要 34 秒，超过前端 30 秒上限）。

    造数据直接落盘图片 + 批量插入 inspirations（phash 留空），不走 HTTP
    上传——本测试关注的是扫描/补算服务，350 次完整上传（去重/缩略图/人脸
    等重活）与被测逻辑无关却占十几秒。补算直接调服务函数，扫描仍真实走 /api。
    """
    import random
    import uuid

    from PIL import Image

    from app.config import settings
    from app.database import async_session
    from app.models.inspiration import Inspiration

    N = 350  # > BACKFILL_PER_SCAN(300)：触发「回卷补算」剩余 50 张

    def _make_and_save(seed):
        """随机噪点图（视觉唯一，phash 互不相同），落盘返回相对 file_path。"""
        random.seed(seed)
        img = Image.new("RGB", (64, 64))
        img.putdata([
            (random.randint(0, 255), random.randint(0, 127), random.randint(0, 255))
            for _ in range(64 * 64)
        ])
        rel = f"images/test_{seed}.jpg"
        full = settings.storage_root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        img.save(full, format="JPEG", quality=60)
        return rel

    async with async_session() as db:
        rows = []
        for i in range(N):
            rel = _make_and_save(i)
            rows.append(Inspiration(
                id=str(uuid.uuid4()),
                source_type="manual_upload",
                file_path=rel,
                media_type="image",
                phash=None,  # 关键：留空，触发扫描时补算
            ))
        db.add_all(rows)
        await db.commit()

    # 先显式补齐（扫描接口只读缓存；N > BACKFILL_PER_SCAN 会触发多轮回卷）
    from app.services.near_duplicate_service import backfill_phash_cache

    async with async_session() as _db:
        filled = await backfill_phash_cache(_db, budget_seconds=120.0)
    assert filled["computed"] == N, f"computed={filled['computed']} should be {N}"
    assert filled["complete"] is True

    # 扫描：limit=5000 → 库中只有 N 张，scanned 应 = N（关键：补算写入的 phash
    # 必须在扫描的随机抽样里可见，不能读到旧快照）
    res = client.post(
        "/api/admin/near-duplicates", json={"limit": 5000, "threshold": 32}
    ).json()
    assert res["backfilled"] == 0, "扫描接口不应再补算"
    assert res["cached_total"] == N, (
        f"cached_total={res['cached_total']} should be {N}"
    )
    assert res["scanned"] == N, (
        f"BUG! scanned={res['scanned']} should be {N} when limit=5000 and total={N}"
    )
    assert res["truncated"] is False

    # 第二次扫描：全部哈希已缓存，scanned 应该 = total = N
    res2 = client.post(
        "/api/admin/near-duplicates", json={"limit": 5000, "threshold": 32}
    ).json()
    assert res2["backfilled"] == 0
    assert res2["scanned"] == N
    assert res2["truncated"] is False
