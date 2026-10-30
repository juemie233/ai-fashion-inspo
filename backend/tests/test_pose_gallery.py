"""姿势图库服务与接口（个人本地使用，不做内容分级过滤）。

关键约束（都能在这里锁住）：

1. **不过滤体系**：源站 4 个体系（含成人向）都是可选姿势——这是产品决定；
2. **目录缓存**：源站是静态站且无 CORS，后端拉一次缓存到 storage；过期才回源，
   回源失败用旧缓存兜底（源站抖动不能让换姿势功能直接不可用）；
3. **按需下载**：姿势图只在生成时下载，文件落在 storage 缓存，不随仓库分发；
4. **脏数据不能进目录**：id 非法/缺缩略图或原图的行直接丢（防路径穿越与空条目）。
"""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi import HTTPException

from app.services import pose_gallery

"""测试用原始行：故意混入 3 个体系（含成人向）、1 个非法 id、1 个缺字段行。"""
RAW_ROWS = [
    {
        "id": "full_std_P001_front",
        "体系": "全身单人・标准系",
        "分类": "站姿",
        "姿势名称": "S形站姿・胯部侧顶",
        "角度": "正面",
        "缩略图": "thumbs/a.jpg",
        "原图": "img/a.png",
        "姿势编号": "P001",
    },
    {
        "id": "full_sexy_P002_side",
        "体系": "全身单人・性感系",
        "分类": "侧卧位",
        "姿势名称": "侧卧支撑",
        "角度": "侧面",
        "缩略图": "thumbs/b.jpg",
        "原图": "img/b.png",
        "姿势编号": "P002",
    },
    {
        "id": "upper_P003_low",
        "体系": "上半身单人",
        "分类": "坐姿",
        "姿势名称": "半身坐姿",
        "角度": "俯视",
        "缩略图": "thumbs/c.jpg",
        "原图": "img/c.png",
        "姿势编号": "P003",
    },
    {"id": "bad id!", "体系": "上半身单人", "缩略图": "x.jpg", "原图": "x.png"},
    {"id": "no_thumb", "体系": "上半身单人", "原图": "img/d.png"},
]


def _normalized() -> list[dict]:
    """把 RAW_ROWS 归一化（等价于真实回源后的结果）。"""
    return [item for item in (pose_gallery._normalize(row) for row in RAW_ROWS) if item]


@pytest.fixture
def fake_gallery(monkeypatch, tmp_path):
    """把源站替换成固定目录，缓存路径改到 tmp（避免跨用例共享 session 级 storage）。"""
    items = _normalized()
    calls = {"fetch": 0, "download": 0, "images": {}}

    async def _fetch_catalog() -> list[dict]:
        calls["fetch"] += 1
        return items

    async def _fetch_image(url: str) -> bytes:
        calls["download"] += 1
        return pose_gallery.PNG_MAGIC + b"\x00" * 4096

    monkeypatch.setattr(pose_gallery, "_fetch_catalog", _fetch_catalog)
    monkeypatch.setattr(pose_gallery, "_fetch_image", _fetch_image)
    monkeypatch.setattr(pose_gallery, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(pose_gallery, "catalog_cache_path", lambda: tmp_path / "catalog.json")
    return {"items": items, "calls": calls, "tmp": tmp_path}


# ── 归一化与过滤口径 ──


def test_normalize_keeps_all_systems_and_drops_dirty_rows():
    """**不过滤任何体系**（含成人向）；只丢 id 非法/缺图的行。"""
    items = _normalized()
    systems = {item["system"] for item in items}
    assert "全身单人・性感系" in systems
    assert len(items) == 3
    for item in items:
        assert set(item) == {
            "id", "name", "pose_no", "system", "category", "angle", "thumb_url", "image_url"
        }
        assert item["thumb_url"].startswith("https://")
        assert item["image_url"].endswith(".png")


def test_normalize_rejects_traversal_like_ids():
    """id 会被拼进缓存文件名，含 ``..`` / 斜杠/空格的一律不接受。"""
    for bad in ("../etc/passwd", "a/b", "x y", ""):
        row = {
            "id": bad,
            "体系": "上半身单人",
            "缩略图": "t.jpg",
            "原图": "i.png",
        }
        assert pose_gallery._normalize(row) is None


def test_normalize_rejects_unsafe_image_paths():
    """图片路径只能站内相对路径：绝对 URL / 路径穿越一律丢弃（防上游被篡改）。"""
    base = {
        "id": "upper_P004_front",
        "体系": "上半身单人",
        "缩略图": "thumbs/a.jpg",
        "原图": "img/a.png",
        "分类": "站姿",
        "姿势名称": "测试",
        "角度": "正面",
        "姿势编号": "P004",
    }
    assert pose_gallery._normalize(base) is not None
    for bad in ("https://evil.com/x.png", "//evil.com/x.png", "../../etc/passwd", "a/../../b.png"):
        assert pose_gallery._normalize({**base, "原图": bad}) is None
        assert pose_gallery._normalize({**base, "缩略图": bad}) is None


async def test_fetch_limited_rejects_oversize():
    """上游返回超大响应时必须中止，不能把整段内容读进内存。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4096)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="响应"):
            await pose_gallery._fetch_limited(
                "https://example.com/huge", 1024, client=client
            )


# ── 目录缓存 ──


async def test_catalog_cached_until_forced(fake_gallery):
    first = await pose_gallery.load_catalog()
    second = await pose_gallery.load_catalog()
    assert first == second
    assert fake_gallery["calls"]["fetch"] == 1
    await pose_gallery.load_catalog(force=True)
    assert fake_gallery["calls"]["fetch"] == 2


async def test_stale_cache_used_when_source_fails(fake_gallery, monkeypatch):
    await pose_gallery.load_catalog()  # 写缓存
    cache = fake_gallery["tmp"] / "catalog.json"
    old = time.time() - 999999
    import os

    os.utime(cache, (old, old))  # 让缓存过期

    async def _boom() -> list[dict]:
        raise RuntimeError("源站 502")

    monkeypatch.setattr(pose_gallery, "_fetch_catalog", _boom)
    items = await pose_gallery.load_catalog()
    assert len(items) == 3  # 用旧缓存兜底，而不是直接报错


async def test_source_down_without_cache_returns_503(fake_gallery, monkeypatch):
    async def _boom() -> list[dict]:
        raise RuntimeError("源站 502")

    monkeypatch.setattr(pose_gallery, "_fetch_catalog", _boom)
    with pytest.raises(HTTPException) as exc:
        await pose_gallery.load_catalog()
    assert exc.value.status_code == 503
    assert "姿势库暂时不可用" in str(exc.value.detail)


# ── 列表 / 元信息 / 单条 ──


async def test_list_poses_filters_and_paginates(fake_gallery):
    all_items = await pose_gallery.list_poses()
    assert all_items["total"] == 3
    assert len(all_items["items"]) == 3
    assert "ai-pose-gallery-zh" in all_items["note"]

    page = await pose_gallery.list_poses(page=2, size=2)
    assert page["total"] == 3
    assert len(page["items"]) == 1

    by_system = await pose_gallery.list_poses(system="全身单人・性感系")
    assert [item["id"] for item in by_system["items"]] == ["full_sexy_P002_side"]

    by_q = await pose_gallery.list_poses(q="侧卧")
    assert [item["id"] for item in by_q["items"]] == ["full_sexy_P002_side"]

    by_angle = await pose_gallery.list_poses(angle="俯视")
    assert [item["id"] for item in by_angle["items"]] == ["upper_P003_low"]

    by_category = await pose_gallery.list_poses(category="坐姿")
    assert [item["id"] for item in by_category["items"]] == ["upper_P003_low"]


async def test_meta_counts(fake_gallery):
    data = await pose_gallery.meta()
    assert data["total"] == 3
    assert {item["name"] for item in data["systems"]} == {
        "全身单人・标准系",
        "全身单人・性感系",
        "上半身单人",
    }
    assert sum(item["count"] for item in data["categories"]) == 3
    assert data["source_url"].startswith("https://")
    assert data["note"]


async def test_get_pose_ok_and_404(fake_gallery):
    item = await pose_gallery.get_pose("full_std_P001_front")
    assert item["name"] == "S形站姿・胯部侧顶"
    with pytest.raises(HTTPException) as exc:
        await pose_gallery.get_pose("not_exist_pose")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await pose_gallery.get_pose("../evil")
    assert exc.value.status_code == 400


# ── 按需下载 ──


async def test_ensure_pose_image_downloads_once(fake_gallery):
    path = await pose_gallery.ensure_pose_image("full_std_P001_front")
    assert path.exists()
    assert path.read_bytes().startswith(pose_gallery.PNG_MAGIC)
    assert fake_gallery["calls"]["download"] == 1

    again = await pose_gallery.ensure_pose_image("full_std_P001_front")
    assert again == path
    assert fake_gallery["calls"]["download"] == 1  # 命中缓存，不重复下载


async def test_ensure_pose_image_rejects_bad_payload(fake_gallery, monkeypatch):
    async def _html(_url: str) -> bytes:
        return b"<html>404</html>" * 200  # 够长但不是 PNG

    monkeypatch.setattr(pose_gallery, "_fetch_image", _html)
    with pytest.raises(HTTPException) as exc:
        await pose_gallery.ensure_pose_image("full_std_P001_front")
    assert exc.value.status_code == 502


async def test_cached_bad_magic_is_redownloaded(fake_gallery):
    """大小达标但魔数不对的缓存（半截文件/错误页）必须重新下载，不能直接复用。"""
    folder = fake_gallery["tmp"] / "images"
    folder.mkdir()
    bad = folder / "full_std_P001_front.png"
    bad.write_bytes(b"<html>error</html>" + b"x" * 4096)

    path = await pose_gallery.ensure_pose_image("full_std_P001_front")

    assert path.read_bytes().startswith(pose_gallery.PNG_MAGIC)
    assert fake_gallery["calls"]["download"] == 1


# ── 接口层（真实路由 + 假目录）──


def test_router_meta_and_list(client, fake_gallery):
    meta = client.get("/api/pose-gallery/meta")
    assert meta.status_code == 200
    assert meta.json()["total"] == 3

    resp = client.get("/api/pose-gallery", params={"q": "半身", "page": 1, "size": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "upper_P003_low"
    assert body["note"]
    assert "成人向" in body["note"]  # 不过滤 18+ 时必须在 UI 文案里明示
    # 缩略图/原图直链给前端用（<img> 展示不需要 CORS）
    assert body["items"][0]["thumb_url"].startswith("https://")


def test_router_get_pose(client, fake_gallery):
    """历史「带入表单」恢复姿势用：按 id 取单条，未知 id 404。"""
    ok = client.get("/api/pose-gallery/full_std_P001_front")
    assert ok.status_code == 200
    assert ok.json()["id"] == "full_std_P001_front"

    assert client.get("/api/pose-gallery/not_exist_pose").status_code == 404
