"""姿势图库（pose_gallery 服务 + 路由）的直连边缘单测。

既有 test_pose_gallery.py 用 fake_gallery 覆盖了目录缓存/下载主路径；本文件直接
调用服务与路由函数，补齐：``_normalize`` 缺体系、``_fetch_limited`` 自建客户端与
响应头/分块超限、``_fetch_catalog`` 的 JSON/结构/空目录错误、``_read_cache`` 坏
缓存、缓存写失败降级、``random_pose`` 的排除与 404、``_is_png`` 的读失败、
``ensure_pose_image`` 的缓存复用与 4xx/5xx/格式错误映射，以及路由层
``usage_count`` 注入（不得污染内存缓存）与姿势提示词查询。
"""

import json

import httpx
import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.pose_prompt import PosePrompt
from app.routers import pose_gallery as router_mod
from app.services import generation_service
from app.services import pose_gallery as svc


RAW_ROWS = [
    {
        "id": "full_std_P001_front",
        "体系": "全身单人",
        "分类": "站姿",
        "姿势名称": "S形站姿",
        "角度": "正面",
        "缩略图": "thumbs/a.jpg",
        "原图": "img/a.png",
        "姿势编号": "P001",
    },
    {
        "id": "upper_P002_side",
        "体系": "上半身单人",
        "分类": "坐姿",
        "姿势名称": "半身坐姿",
        "角度": "侧面",
        "缩略图": "thumbs/b.jpg",
        "原图": "img/b.png",
        "姿势编号": "P002",
    },
]


def _item(pose_id: str = "a", **overrides) -> dict:
    data = {
        "id": pose_id,
        "name": "姿势",
        "pose_no": "P001",
        "system": "全身单人",
        "category": "站姿",
        "angle": "正面",
        "thumb_url": "https://example.com/thumbs/a.jpg",
        "image_url": "https://example.com/img/a.png",
    }
    data.update(overrides)
    return data


# ═══════════════════════════════════════════════════════════════
#  归一化 / 下载限额
# ═══════════════════════════════════════════════════════════════


def test_normalize_requires_system_and_valid_paths():
    """缺体系 / 缺缩略图或原图 / 路径越界 → None；正常行字段完整。"""
    base = {
        "id": "upper_P003_front",
        "体系": "上半身单人",
        "缩略图": "thumbs/c.jpg",
        "原图": "img/c.png",
    }
    assert svc._normalize({**base, "体系": "  "}) is None
    assert svc._normalize({k: v for k, v in base.items() if k != "缩略图"}) is None
    assert svc._normalize({**base, "原图": "\\\\server\\share\\x.png"}) is None

    item = svc._normalize({**base, "姿势名称": " ", "分类": None, "角度": ""})
    assert item is not None
    assert item["name"] == "" and item["category"] == "" and item["angle"] == ""
    assert item["thumb_url"].endswith("/thumbs/c.jpg")
    assert item["image_url"].endswith("/img/c.png")


class _FakeResponse:
    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None) -> None:
        self._chunks = chunks
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeStream:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, *_args) -> bool:
        return False


class _FakeClient:
    def __init__(self, response: _FakeResponse, closed: list[bool]) -> None:
        self._response = response
        self._closed = closed

    def stream(self, _method: str, _url: str, headers=None):
        assert headers and "User-Agent" in headers
        return _FakeStream(self._response)

    async def aclose(self) -> None:
        self._closed.append(True)


def _patch_http_client(monkeypatch, response: _FakeResponse) -> list[bool]:
    closed: list[bool] = []
    monkeypatch.setattr(
        svc.httpx, "AsyncClient", lambda timeout=20.0: _FakeClient(response, closed)
    )
    return closed


async def test_fetch_limited_self_owned_client_closes(monkeypatch):
    """未注入 client 时自建并负责关闭；正常响应原样返回。"""
    closed = _patch_http_client(
        monkeypatch, _FakeResponse([b"abc", b"def"], {"content-length": "6"})
    )

    data = await svc._fetch_limited("https://example.com/x", 1024)

    assert data == b"abcdef"
    assert closed == [True]


async def test_fetch_limited_rejects_oversize_by_header(monkeypatch):
    """content-length 先判大小：超过上限直接 ValueError，不读响应体。"""
    _patch_http_client(monkeypatch, _FakeResponse([b"x" * 100], {"content-length": "9999"}))

    with pytest.raises(ValueError, match="响应过大"):
        await svc._fetch_limited("https://example.com/x", 1024)


async def test_fetch_limited_rejects_chunk_overrun(monkeypatch):
    """没有 content-length 时按累计读取字节中止（防分块绕过）。"""
    _patch_http_client(monkeypatch, _FakeResponse([b"x" * 700, b"x" * 700]))

    with pytest.raises(ValueError, match="超过上限"):
        await svc._fetch_limited("https://example.com/x", 1024)


# ═══════════════════════════════════════════════════════════════
#  回源目录 / 缓存读写
# ═══════════════════════════════════════════════════════════════


async def _fake_limited_factory(payload: bytes):
    async def _fake(_url: str, _max: int, **_kw) -> bytes:
        return payload

    return _fake


async def test_fetch_catalog_parses_and_filters_dirty_rows(monkeypatch):
    rows = [*RAW_ROWS, "not-a-dict", {"id": "bad id!", "体系": "x"}]
    monkeypatch.setattr(
        svc, "_fetch_limited", await _fake_limited_factory(json.dumps(rows).encode())
    )

    items = await svc._fetch_catalog()

    assert [item["id"] for item in items] == ["full_std_P001_front", "upper_P002_side"]


@pytest.mark.parametrize(
    "payload,match",
    [
        (b"not-json", "不是合法 JSON"),
        (b'{"a": 1}', "顶层不是数组"),
        (b'[{"id": "bad id!"}]', "目录为空"),
    ],
)
async def test_fetch_catalog_rejects_bad_upstream(monkeypatch, payload, match):
    monkeypatch.setattr(svc, "_fetch_limited", await _fake_limited_factory(payload))

    with pytest.raises(ValueError, match=match):
        await svc._fetch_catalog()


@pytest.fixture
def cache_paths(monkeypatch, tmp_path):
    """把缓存目录/文件指到 tmp（避免污染 session 级 storage）。"""
    monkeypatch.setattr(svc, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(svc, "catalog_cache_path", lambda: tmp_path / "catalog.json")
    return tmp_path / "catalog.json"


def test_read_cache_missing_and_corrupted(cache_paths):
    assert svc._read_cache() is None  # 文件不存在

    cache_paths.write_text("not-json", encoding="utf-8")
    assert svc._read_cache() is None

    cache_paths.write_text("[]", encoding="utf-8")
    assert svc._read_cache() is None  # 空数组等价于没有缓存

    cache_paths.write_text('[1, {"id": "ok"}]', encoding="utf-8")
    assert svc._read_cache() == [{"id": "ok"}]  # 非 dict 条目被过滤


async def test_load_catalog_uses_cache_then_refetches(cache_paths, monkeypatch):
    """有效缓存命中不回源；缓存内容损坏则回源。"""
    calls: list[int] = []

    async def _fetch():
        calls.append(1)
        return [_item()]

    monkeypatch.setattr(svc, "_fetch_catalog", _fetch)
    assert await svc.load_catalog(force=True) == [_item()]
    assert len(calls) == 1

    # 刚写入的有效缓存：不再回源
    assert await svc.load_catalog() == [_item()]
    assert len(calls) == 1

    # 缓存损坏 → 回源
    cache_paths.write_text("broken", encoding="utf-8")
    assert await svc.load_catalog() == [_item()]
    assert len(calls) == 2


async def test_load_catalog_cache_write_failure_is_tolerated(cache_paths, monkeypatch):
    """缓存写失败只告警，不影响本次返回。"""

    async def _fetch():
        return [_item()]

    def _boom(_items):
        raise OSError("磁盘满")

    monkeypatch.setattr(svc, "_fetch_catalog", _fetch)
    monkeypatch.setattr(svc, "_write_cache", _boom)

    assert await svc.load_catalog(force=True) == [_item()]


# ═══════════════════════════════════════════════════════════════
#  随机 / 详情 / 元信息
# ═══════════════════════════════════════════════════════════════


def _patch_catalog(monkeypatch, items: list[dict]) -> None:
    async def _load() -> list[dict]:
        return items

    monkeypatch.setattr(svc, "load_catalog", _load)


async def test_random_pose_filters_exclude_and_404(monkeypatch):
    a, b = _item("a"), _item("b", system="上半身单人")
    _patch_catalog(monkeypatch, [a, b])

    only_a = await svc.random_pose(exclude="b")
    assert only_a["id"] == "a"

    # 排除后只剩被排除的那个 → 允许返回它自己（比 404 合理）
    one_match = await svc.random_pose(system="全身单人", exclude="a")
    assert one_match["id"] == "a"

    with pytest.raises(HTTPException) as exc:
        await svc.random_pose(system="不存在的体系")
    assert exc.value.status_code == 404
    assert "没有匹配的姿势" in str(exc.value.detail)


async def test_get_pose_validation(monkeypatch):
    _patch_catalog(monkeypatch, [_item("a")])

    with pytest.raises(HTTPException) as bad:
        await svc.get_pose("..")
    assert bad.value.status_code == 400

    with pytest.raises(HTTPException) as missing:
        await svc.get_pose("not-exist")
    assert missing.value.status_code == 404

    assert await svc.get_pose("a") == _item("a")


async def test_counts_and_meta(monkeypatch):
    items = [
        _item("a", system="A", category="站姿", angle="正面"),
        _item("b", system="A", category="坐姿", angle="侧面"),
        _item("c", system="B", category="", angle=""),
    ]
    _patch_catalog(monkeypatch, items)

    info = await svc.meta()

    assert info["total"] == 3
    assert info["systems"] == [{"name": "A", "count": 2}, {"name": "B", "count": 1}]
    assert info["categories"] == [{"name": "坐姿", "count": 1}, {"name": "站姿", "count": 1}]
    assert info["angles"] == [{"name": "侧面", "count": 1}, {"name": "正面", "count": 1}]
    assert info["note"] == svc.GALLERY_NOTE
    assert "source_url" in info
    assert svc._counts(["", ""]) == []  # 空字符串不计


# ═══════════════════════════════════════════════════════════════
#  图片按需下载与缓存
# ═══════════════════════════════════════════════════════════════


def test_is_png_magic_and_read_error(tmp_path):
    good = tmp_path / "good.png"
    good.write_bytes(svc.PNG_MAGIC + b"x")
    assert svc._is_png(good) is True

    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not-png")
    assert svc._is_png(broken) is False
    assert svc._is_png(tmp_path / "missing.png") is False  # OSError 降级


async def test_fetch_image_uses_image_limits(monkeypatch):
    captured: dict = {}

    async def _fake(url: str, max_bytes: int, **kwargs) -> bytes:
        captured.update(url=url, max_bytes=max_bytes, kwargs=kwargs)
        return b"data"

    monkeypatch.setattr(svc, "_fetch_limited", _fake)
    assert await svc._fetch_image("https://example.com/img.png") == b"data"
    assert captured["max_bytes"] == svc.MAX_IMAGE_BYTES
    assert captured["kwargs"]["timeout"] == 60.0


@pytest.fixture
def image_env(monkeypatch, tmp_path):
    """姿势详情固定、缓存目录指到 tmp，并记录下载次数。"""
    _patch_catalog(monkeypatch, [_item("a")])
    monkeypatch.setattr(svc, "cache_dir", lambda: tmp_path)
    calls: list[str] = []

    async def _fetch_image(url: str) -> bytes:
        calls.append(url)
        return svc.PNG_MAGIC + b"x" * svc.MIN_IMAGE_BYTES

    monkeypatch.setattr(svc, "_fetch_image", _fetch_image)
    return {"tmp": tmp_path, "calls": calls}


async def test_ensure_pose_image_cache_reuse_and_repair(image_env):
    """首次下载写缓存；二次命中直接复用；缓存文件过小则重新下载。"""
    target = await svc.ensure_pose_image("a")
    assert target.exists() and target.stat().st_size >= svc.MIN_IMAGE_BYTES
    assert image_env["calls"] == [_item("a")["image_url"]]

    again = await svc.ensure_pose_image("a")
    assert again == target
    assert len(image_env["calls"]) == 1  # 未重复下载

    target.write_bytes(b"tiny")  # 损坏缓存（半截文件）
    repaired = await svc.ensure_pose_image("a")
    assert repaired == target
    assert len(image_env["calls"]) == 2


async def test_ensure_pose_image_maps_http_5xx_to_503(image_env, monkeypatch):
    request = httpx.Request("GET", "https://example.com/img.png")
    response = httpx.Response(500, request=request)

    async def _boom(_url: str) -> bytes:
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr(svc, "_fetch_image", _boom)
    with pytest.raises(HTTPException) as exc:
        await svc.ensure_pose_image("a")
    assert exc.value.status_code == 503
    assert "下载失败" in str(exc.value.detail)


async def test_ensure_pose_image_rejects_non_png_payload(image_env, monkeypatch):
    async def _html(_url: str) -> bytes:
        return b"<html>" + b"x" * svc.MIN_IMAGE_BYTES

    monkeypatch.setattr(svc, "_fetch_image", _html)
    with pytest.raises(HTTPException) as exc:
        await svc.ensure_pose_image("a")
    assert exc.value.status_code == 502
    assert "非 PNG" in str(exc.value.detail)


# ═══════════════════════════════════════════════════════════════
#  路由层：usage_count 注入与姿势提示词
# ═══════════════════════════════════════════════════════════════


async def test_router_list_adds_usage_count_without_mutating_cache(monkeypatch):
    cached_items = [_item("a"), _item("b")]
    payload = {"items": [dict(item) for item in cached_items], "total": 2, "page": 1, "size": 40}
    cache_dicts = payload["items"]  # 模拟服务层内存缓存里的 dict 对象

    async def _list_poses(**_kwargs):
        return payload

    async def _usage(_db):
        return {"a": 3}

    monkeypatch.setattr(svc, "list_poses", _list_poses)
    monkeypatch.setattr(generation_service, "pose_usage_counts", _usage)

    async with async_session() as db:
        data = await router_mod.list_poses(
            q=None, system=None, category=None, angle=None, page=1, size=40, db=db
        )

    assert [item["usage_count"] for item in data["items"]] == [3, 0]
    # 返回的是复制条目，不能污染内存缓存（否则 get_pose 会多出字段）
    assert all("usage_count" not in item for item in cache_dicts)


async def test_router_random_and_prompt(monkeypatch):
    async def _random(**_kwargs):
        return _item("a")

    async def _usage(_db):
        return {"a": 2}

    async def _get_pose(_pose_id: str):
        return _item("a")

    monkeypatch.setattr(svc, "random_pose", _random)
    monkeypatch.setattr(svc, "get_pose", _get_pose)
    monkeypatch.setattr(generation_service, "pose_usage_counts", _usage)

    async with async_session() as db:
        item = await router_mod.random_pose(
            q=None, system=None, category=None, angle=None, exclude=None, db=db
        )
        assert item["usage_count"] == 2

        db.add(PosePrompt(pose_id="a", name="姿势", prompt="正面站立，双手自然下垂"))
        await db.commit()
        prompt = await router_mod.get_pose_prompt("a", db=db)
        assert prompt == {"pose_id": "a", "prompt": "正面站立，双手自然下垂"}

        empty = await router_mod.get_pose_prompt("b", db=db)
        assert empty == {"pose_id": "b", "prompt": None}
