"""素材服务边界与 URL 导入测试：409/404/400 错误分支 + 日期筛选 + 从 URL 导入。"""

import io
from datetime import datetime, timedelta

import httpx
from PIL import Image


def test_trash_already_trashed_409(client, upload):
    """已移入垃圾桶的素材再次移入 → 409。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/trash")
    r = client.post(f"/api/inspirations/{insp_id}/trash")
    assert r.status_code == 409


def test_restore_not_trashed_409(client, upload):
    """未在垃圾桶中的素材恢复 → 409。"""
    insp_id = upload().json()["id"]
    r = client.post(f"/api/inspirations/{insp_id}/restore")
    assert r.status_code == 409


def test_remove_tag_not_found_404(client, upload):
    """解除不存在的标签关联 → 404。"""
    insp_id = upload().json()["id"]
    r = client.delete(f"/api/inspirations/{insp_id}/tags/99999")
    assert r.status_code == 404


def test_add_tags_empty_400(client, upload):
    """给素材添加空标签列表 → 400。"""
    insp_id = upload().json()["id"]
    r = client.post(f"/api/inspirations/{insp_id}/tags", json={"names": []})
    assert r.status_code == 400


def test_update_not_found_404(client):
    """更新不存在的素材 → 404。"""
    r = client.patch("/api/inspirations/no-such-id", json={"is_favorite": True})
    assert r.status_code == 404


def test_date_filter(client, upload):
    """按上传日期筛选：date_from 在未来 → 空，在过去 → 含刚上传素材。"""
    upload()
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    assert client.get("/api/inspirations", params={"date_from": tomorrow}).json()["total"] == 0
    assert client.get("/api/inspirations", params={"date_from": yesterday}).json()["total"] == 1


def test_batch_add_tags(client, upload):
    """批量给多个素材关联标签。"""
    a = upload().json()["id"]
    b = upload().json()["id"]

    r = client.post(
        "/api/inspirations/batch-tags",
        json={"inspiration_ids": [a, b], "names": ["法式"], "category": "style"},
    )
    assert r.status_code == 200
    assert r.json()["affected"] == 2


def test_platform_id_dedup_excludes_trash(client, upload, make_image):
    """平台 ID 查重仅统计未删除素材：垃圾桶素材释放平台 ID，可重新上传。"""
    pid = "xhs-note-12345"
    first = upload(source_platform_id=pid)
    assert first.status_code == 201

    # 未删除时：同平台 ID 再次上传 → 409（平台 ID 重复）
    data2, ctype2 = make_image()
    r = client.post(
        "/api/inspirations",
        files={"file": ("t2.jpg", data2, ctype2)},
        data={"source_platform_id": pid},
    )
    assert r.status_code == 409

    # 移入垃圾桶后：平台 ID 被释放，同 ID 可重新上传
    client.post(f"/api/inspirations/{first.json()['id']}/trash")
    r = client.post(
        "/api/inspirations",
        files={"file": ("t2.jpg", data2, ctype2)},
        data={"source_platform_id": pid},
    )
    assert r.status_code == 201


def test_restore_conflict_when_platform_id_reused(client, upload, make_image):
    """垃圾桶期间同平台 ID 被新素材占用，恢复返回 409 而非 IntegrityError 500。"""
    pid = "xhs-note-9999"
    first = upload(source_platform_id=pid)
    assert first.status_code == 201
    insp_id = first.json()["id"]

    client.post(f"/api/inspirations/{insp_id}/trash")

    data2, ctype2 = make_image()
    r = client.post(
        "/api/inspirations",
        files={"file": ("t2.jpg", data2, ctype2)},
        data={"source_platform_id": pid},
    )
    assert r.status_code == 201

    res = client.post(f"/api/inspirations/{insp_id}/restore")
    assert res.status_code == 409


def test_largest_sort_respects_filters(client, make_image):
    """sort=largest 与来源筛选组合：只返回筛选内的素材（修复 size_rows 漏筛选条件）。"""
    # 大图 → manual_upload（无筛选时 largest 排最前）；小图 → scraper
    big, ctype = make_image(size=(200, 200))
    client.post(
        "/api/inspirations",
        files={"file": ("big.jpg", big, ctype)},
        data={"source_type": "manual_upload"},
    )
    small, ctype2 = make_image(size=(64, 64))
    client.post(
        "/api/inspirations",
        files={"file": ("small.jpg", small, ctype2)},
        data={"source_type": "scraper"},
    )

    # 筛选 scraper + largest：修复前 size_rows 不带筛选，页内 ID 取到大图素材
    # 导致最终结果被筛选条件过滤成空页
    r = client.get("/api/inspirations", params={"sort": "largest", "source_type": "scraper"})
    body = r.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["source_type"] == "scraper"


def test_create_from_url(client, monkeypatch):
    """从 URL 下载图片导入素材（模拟 httpx 下载）。"""
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 100, 50)).save(buf, format="JPEG")
    buf.seek(0)
    img_bytes = buf.getvalue()

    class FakeStream:
        def __init__(self):
            self.headers = {
                "content-type": "image/jpeg",
                "content-length": str(len(img_bytes)),
            }

        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def aiter_bytes(self, _chunk_size):
            yield img_bytes

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def stream(self, _method, _url):
            return FakeStream()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    r = client.post(
        "/api/inspirations/from-url",
        json={"url": "https://example.com/a.jpg", "source_author": "博主", "tags": ["法式"]},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["source_type"] == "url_import"
    assert data["source_author"] == "博主"
    assert data["media_type"] == "image"

    detail = client.get(f"/api/inspirations/{data['id']}").json()
    assert any(t["tag"]["name"] == "法式" for t in detail["tags"])


def test_create_from_url_sends_browser_ua_and_referer(client, monkeypatch):
    """from-url 下载带浏览器 UA 与来源页 Referer（平台 CDN 防盗链，右键保存依赖）。

    回归：小红书/抖音图片 CDN 对裸 httpx 请求（默认 UA、无 Referer）常返回
    403/占位图，导致插件右键保存「下载失败」。
    """
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (5, 6, 7)).save(buf, format="JPEG")
    img_bytes = buf.getvalue()
    captured: dict = {}

    class FakeStream:
        def __init__(self):
            self.headers = {
                "content-type": "image/jpeg",
                "content-length": str(len(img_bytes)),
            }

        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def aiter_bytes(self, _chunk_size):
            yield img_bytes

    class FakeClient:
        def __init__(self, *args, **kwargs):
            captured["headers"] = kwargs.get("headers") or {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def stream(self, _method, _url):
            return FakeStream()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    r = client.post(
        "/api/inspirations/from-url",
        json={
            "url": "https://sns-webpic-qc.xhscdn.com/abc.jpg",
            "source_type": "browser_extension",
            "source_url": "https://www.xiaohongshu.com/explore/note123",
        },
    )
    assert r.status_code == 201, r.text
    assert "Mozilla" in captured["headers"].get("User-Agent", "")
    assert captured["headers"].get("Referer") == "https://www.xiaohongshu.com/explore/note123"


def test_create_from_url_plugin_flow(client, monkeypatch):
    """浏览器插件采集链路：from-url 携带平台/任务字段（服务端下载规避 CORS）。"""
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, format="JPEG")
    buf.seek(0)
    img_bytes = buf.getvalue()

    class FakeStream:
        def __init__(self):
            self.headers = {
                "content-type": "image/jpeg",
                "content-length": str(len(img_bytes)),
            }

        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def aiter_bytes(self, _chunk_size):
            yield img_bytes

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def stream(self, _method, _url):
            return FakeStream()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    # 先创建插件采集会话任务
    r = client.post(
        "/api/scraper/extension-tasks",
        json={"platform": "xiaohongshu", "source_url": "https://www.xiaohongshu.com/explore/abc"},
    )
    assert r.status_code == 201
    task_id = r.json()["id"]

    # 插件采集上传：显式传来源页面地址（应优先于图片 URL 落库）
    r = client.post(
        "/api/inspirations/from-url",
        json={
            "url": "https://sns-webpic-qc.xhscdn.com/note.jpg",
            "source_type": "browser_extension",
            "source_url": "https://www.xiaohongshu.com/explore/abc",
            "source_author": "博主",
            "source_platform_id": "xhs-abc-1",
            "scraper_task_id": task_id,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["source_type"] == "browser_extension"
    assert data["source_url"] == "https://www.xiaohongshu.com/explore/abc"
    assert data["source_author"] == "博主"

    # 素材已关联到采集任务（直连临时库验证 scraper_task_id 落库）
    from app.config import settings
    import sqlite3

    conn = sqlite3.connect(str(settings.storage_root.parent / "fashion_inspo.db"))
    try:
        row = conn.execute(
            "SELECT scraper_task_id FROM inspirations WHERE source_platform_id='xhs-abc-1'"
        ).fetchone()
    finally:
        conn.close()
    assert row and row[0] == task_id

    # 同平台 ID 重复 → 409
    r = client.post(
        "/api/inspirations/from-url",
        json={"url": "https://sns-webpic-qc.xhscdn.com/note2.jpg", "source_platform_id": "xhs-abc-1"},
    )
    assert r.status_code == 409

    # 关联任务不存在 → 400
    r = client.post(
        "/api/inspirations/from-url",
        json={"url": "https://example.com/b.jpg", "scraper_task_id": 99999},
    )
    assert r.status_code == 400

    # scraper_task_id 非法 → 400
    r = client.post(
        "/api/inspirations/from-url",
        json={"url": "https://example.com/c.jpg", "scraper_task_id": "abc"},
    )
    assert r.status_code == 400


def test_create_from_url_null_fields(client, monkeypatch):
    """插件真实请求会发送 JSON null 可选字段，不得抛 500（回归：None.strip）。"""
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (99, 88, 77)).save(buf, format="JPEG")
    buf.seek(0)
    img_bytes = buf.getvalue()

    class FakeStream:
        def __init__(self):
            self.headers = {
                "content-type": "image/jpeg",
                "content-length": str(len(img_bytes)),
            }

        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def aiter_bytes(self, _chunk_size):
            yield img_bytes

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def stream(self, _method, _url):
            return FakeStream()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    # 与 service-worker.js 发送的请求体一致：可选字段均为 null
    r = client.post(
        "/api/inspirations/from-url",
        json={
            "url": "https://sns-webpic-qc.xhscdn.com/note.jpg",
            "source_type": "browser_extension",
            "source_url": None,
            "source_author": None,
            "source_platform_id": None,
            "scraper_task_id": None,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["source_type"] == "browser_extension"
    # 未传 source_url（null）时回退为图片 URL，保证素材可溯源
    assert data["source_url"] == "https://sns-webpic-qc.xhscdn.com/note.jpg"

    # url 为 null → 400（而非 500）
    r = client.post(
        "/api/inspirations/from-url",
        json={"url": None, "source_type": "browser_extension"},
    )
    assert r.status_code == 400
