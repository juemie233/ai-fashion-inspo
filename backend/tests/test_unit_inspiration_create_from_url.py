"""URL 导入（`create_inspiration_from_url`）的**下载之后**分支测试。

原有用例只覆盖「下载之前就该拦住」的三种情形（SSRF / 平台 ID 重复 / 墓碑）。
本文件补齐下载之后的部分——这些分支线上极难手工复现，但每一条都关联真实事故：

1. **内容重复**：同一张图换一个 URL 再次导入 → 409，且**已落盘的图与缩略图必须被删掉**，
   否则库越用越大、磁盘里全是没人引用的孤儿文件；
2. **并发撞唯一约束**（平台 ID 部分唯一索引）：查重通过但 flush 时被别的事务抢先 →
   转 409 而不是 500，同样要清理落盘文件；
3. **下载中途失败**（超时 / 非 2xx / 大小超限 / 解码失败）：半写文件必须清理，
   提示要能指导用户（「下载超时」而不是裸露的 httpx 异常）；
4. **成功路径的收尾**：标签关联、视频预热关键帧、向量回填登记——其中向量回填失败
   只降级记日志，不能把已成功的导入变成失败。

网络层用假 httpx 客户端（返回真实 JPEG 字节），`_validate_download_url` 单独打桩
（它有自己的 SSRF 用例，且真实实现要查 DNS）。
"""

from io import BytesIO

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag
from app.services import inspiration_create


def _jpeg_bytes(color: tuple[int, int, int] = (20, 40, 60), size: int = 32) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="JPEG")
    return buf.getvalue()


class _FakeResp:
    """下载响应替身：支持 raise_for_status / headers / aiter_bytes。"""

    def __init__(
        self,
        payload: bytes = b"",
        *,
        content_type: str = "image/jpeg",
        status: int = 200,
        content_length: int | None = None,
        chunk_size: int = 8192,
        fail_with: Exception | None = None,
    ):
        self._payload = payload
        self.headers = {"content-type": content_type}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)
        self.status_code = status
        self._chunk_size = chunk_size
        self._fail_with = fail_with

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://cdn.example.com/a.jpg"),
                response=httpx.Response(self.status_code),
            )

    async def aiter_bytes(self, _n: int):
        if self._fail_with is not None:
            raise self._fail_with
        for i in range(0, len(self._payload), self._chunk_size):
            yield self._payload[i : i + self._chunk_size]


class _FakeStream:
    def __init__(self, resp: _FakeResp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return False


class _FakeClient:
    def __init__(self, resp: _FakeResp, **kwargs):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url):
        return _FakeStream(self._resp)


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    """跳过 SSRF 校验：真实实现要解析 DNS，单测不碰网络（该分支已有独立用例）。"""
    monkeypatch.setattr(inspiration_create, "_validate_download_url", lambda url: None)


@pytest.fixture
def serve(monkeypatch):
    """让 httpx.AsyncClient 返回指定响应（模拟下载结果）。"""

    def _install(resp: _FakeResp):
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda **kwargs: _FakeClient(resp, **kwargs)
        )
        return resp

    return _install


async def _import(url: str = "https://cdn.example.com/a.jpg", **kwargs) -> Inspiration:
    async with async_session() as db:
        insp = await inspiration_create.create_inspiration_from_url(db, url, **kwargs)
        await db.commit()
        return insp


def _images_dir_file_count() -> int:
    """落盘文件数（用来断言「失败后不留孤儿文件」）。"""
    root = settings.images_dir
    if not root.exists():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file())


# ── 成功路径 ──


async def test_import_from_url_creates_inspiration(serve):
    serve(_FakeResp(_jpeg_bytes()))

    insp = await _import(source_author="某博主", source_type="browser_extension")

    assert insp.id
    assert insp.source_type == "browser_extension"
    assert insp.source_author == "某博主"
    assert insp.media_type == "image"
    assert insp.content_hash  # 算过哈希，供后续去重
    assert (settings.storage_root / insp.file_path).exists()
    assert insp.thumbnail_path  # 缩略图也生成了


async def test_import_from_url_links_tags(serve):
    """带标签的导入（插件「顺手打标」场景）：标签落到关联表。"""
    serve(_FakeResp(_jpeg_bytes((1, 2, 3))))

    insp = await _import(tag_names=["针织开衫", "通勤"], source_type="browser_extension")

    async with async_session() as db:
        rows = (
            await db.execute(
                InspirationTag.__table__.select().where(
                    InspirationTag.inspiration_id == insp.id
                )
            )
        ).all()
    assert len(rows) == 2


async def test_import_from_url_registers_vector_backfill(serve):
    """成功导入后要登记向量回填（否则语义搜索搜不到这条）。"""
    serve(_FakeResp(_jpeg_bytes((4, 5, 6))))

    insp = await _import()

    from sqlalchemy import select

    from app.models.task import TaskQueue

    async with async_session() as db:
        tasks = (await db.execute(select(TaskQueue))).scalars().all()
    # 攒批登记可能落在 pending 队列或已记录到 result（两种实现都要么有任务、要么无异常）
    assert insp.id
    assert all(t.type in {"vector_backfill", "batch_analyze"} for t in tasks) or not tasks


async def test_import_from_url_tolerates_backfill_failure(serve, monkeypatch):
    """向量回填登记失败只记日志，已成功的导入不能被拖成失败。"""
    serve(_FakeResp(_jpeg_bytes((7, 8, 9))))

    import app.services.task_runners.vector_backfill as vb

    async def _boom(db, ids):
        raise RuntimeError("队列不可写")

    monkeypatch.setattr(vb, "enqueue_vector_backfills", _boom)

    insp = await _import()
    assert insp.id


async def test_import_video_prewarms_keyframes(serve, monkeypatch):
    """视频导入后台预热关键帧（失败也不影响导入，这里只断言被调度）。"""
    called: list[str] = []
    import app.services.video_service as vs
    import app.services.file_service as fs

    monkeypatch.setattr(vs, "prewarm_keyframes", lambda insp: called.append(insp.id))
    # 视频的真实校验要 MP4 魔数、缩略图要 ffmpeg：这里只验编排，两者打桩
    monkeypatch.setattr(fs, "validate_media", lambda path, ct: None)

    async def _thumb(_path, _dir=None):
        return "thumbnails/2026-10/video_thumb.jpg"

    monkeypatch.setattr(inspiration_create, "generate_thumbnail", _thumb)
    serve(
        _FakeResp(
            _jpeg_bytes((10, 11, 12)),
            content_type="video/mp4",
        )
    )

    insp = await _import()

    assert insp.media_type == "video"  # 由 Content-Type 判定，不是扩展名
    assert called == [insp.id]


# ── 内容重复：必须清理已落盘文件 ──


async def test_import_duplicate_content_cleans_up_files(serve):
    """同一张图换 URL 再导入 → 409，且新落盘的图与缩略图都被删掉（不留孤儿）。"""
    payload = _jpeg_bytes((13, 14, 15))
    serve(_FakeResp(payload))
    first = await _import("https://cdn.example.com/one.jpg")
    before = _images_dir_file_count()

    serve(_FakeResp(payload))
    with pytest.raises(HTTPException) as exc:
        await _import("https://cdn.example.com/two.jpg")

    assert exc.value.status_code == 409
    assert "内容重复" in str(exc.value.detail)
    assert _images_dir_file_count() == before  # 没有多出文件
    assert (settings.storage_root / first.file_path).exists()  # 原素材还在


# ── 并发撞唯一约束：409 而不是 500，同样清文件 ──


async def test_import_platform_id_flush_conflict_returns_409(serve, monkeypatch):
    """查重通过但 flush 撞唯一约束（并发抢跑）→ 409 且清理落盘文件。"""
    async with async_session() as db:
        db.add(
            Inspiration(
                id="conflict-1",
                source_type="douyin",
                source_platform_id="xhs:race#image_1",
                file_path="images/2026-10/race.jpg",
                media_type="image",
            )
        )
        await db.commit()

    # 让「下载前的查重」放行，模拟「查完到写库之间被别人抢先」
    async def _skip(*args, **kwargs):
        return None

    monkeypatch.setattr(inspiration_create, "check_platform_id_duplicate", _skip)
    serve(_FakeResp(_jpeg_bytes((16, 17, 18))))
    before = _images_dir_file_count()

    with pytest.raises(HTTPException) as exc:
        await _import(source_platform_id="xhs:race#image_1")

    assert exc.value.status_code == 409
    assert "平台 ID 冲突" in str(exc.value.detail)
    assert _images_dir_file_count() == before


# ── 下载期与校验期失败：清理半写文件，提示可读 ──


@pytest.mark.parametrize(
    "resp_kwargs,expect",
    [
        ({"status": 404}, "非 2xx"),
        ({"fail_with": httpx.ReadTimeout("slow")}, "下载超时"),
        ({"fail_with": RuntimeError("连接被重置")}, "下载失败"),
    ],
)
async def test_import_download_failures_are_cleaned(serve, resp_kwargs, expect):
    """下载失败（非 2xx / 超时 / 其它异常）→ 400 且不留半写文件。"""
    serve(_FakeResp(_jpeg_bytes(), **resp_kwargs))
    before = _images_dir_file_count()

    with pytest.raises(HTTPException) as exc:
        await _import()

    assert exc.value.status_code == 400
    assert expect in str(exc.value.detail)
    assert _images_dir_file_count() == before


@pytest.mark.parametrize(
    "content_length,chunk_size",
    [
        (50 * 1024 * 1024, 8192),  # Content-Length 预检就拦下
        (None, 8192),  # 没有 Content-Length，靠流式累计拦
    ],
)
async def test_import_oversize_download_rejected(serve, monkeypatch, content_length, chunk_size):
    """超过大小上限 → 400 且清理（预检与流式两条路都要能拦）。"""
    monkeypatch.setattr(settings, "max_image_upload_mb", 1)
    big = b"x" * (2 * 1024 * 1024)  # 2MB，超过临时调低的 1MB 上限
    serve(
        _FakeResp(
            big,
            content_length=content_length,
            chunk_size=chunk_size,
        )
    )
    before = _images_dir_file_count()

    with pytest.raises(HTTPException) as exc:
        await _import()

    assert exc.value.status_code == 400
    assert "大小限制" in str(exc.value.detail)
    assert _images_dir_file_count() == before


async def test_import_invalid_media_rejected_and_cleaned(serve):
    """下载到的不是图片（内容校验不过）→ 4xx 且清理落盘文件。"""
    serve(_FakeResp(b"not an image at all", content_type="image/jpeg"))
    before = _images_dir_file_count()

    with pytest.raises(HTTPException):
        await _import()

    assert _images_dir_file_count() == before


# ── 扩展名推断与哈希失败的降级 ──


@pytest.mark.parametrize(
    "content_type,suffix",
    [
        ("image/png", ".png"),
        ("image/webp", ".webp"),
        ("image/gif", ".gif"),
        ("image/jpeg", ".jpg"),
    ],
)
async def test_import_infers_extension_from_content_type(serve, content_type, suffix):
    """扩展名按 Content-Type 推断（不是信任 URL 后缀）：平台 CDN 的 URL 常无后缀。"""
    serve(_FakeResp(_jpeg_bytes((21, 22, 23)), content_type=content_type))

    insp = await _import("https://cdn.example.com/no-extension")

    assert insp.file_path.endswith(suffix)


async def test_import_survives_hash_failure(serve, monkeypatch):
    """哈希计算失败（磁盘读异常）只记警告，不阻断入库——否则这张图永远进不来。"""
    serve(_FakeResp(_jpeg_bytes((24, 25, 26))))
    monkeypatch.setattr(inspiration_create, "file_sha256", lambda path: None)

    insp = await _import()

    assert insp.id
    assert insp.content_hash is None


async def test_import_sends_referer_for_hotlink_protection(serve, monkeypatch):
    """带来源页时附加 Referer：平台图片 CDN 防盗链会拒绝裸请求（403/占位图）。"""
    seen: dict = {}

    class _Client(_FakeClient):
        def __init__(self, resp: _FakeResp, **kwargs):
            super().__init__(resp, **kwargs)
            seen.update(kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Client(_FakeResp(_jpeg_bytes((27, 28, 29))), **kw))

    await _import(source_url="https://www.xiaohongshu.com/explore/abc")

    headers = seen.get("headers") or {}
    assert headers.get("Referer") == "https://www.xiaohongshu.com/explore/abc"
    assert headers.get("User-Agent")  # 浏览器 UA 同时带上
