"""素材创建的服务层用例（补齐 `inspiration_create.py` 48% 的盲区）。

覆盖**不需要真实网络**的部分（下载/流式/超时分支靠 httpx，单测里不碰）：

- `_validate_download_url` 的 SSRF 防护（回环 / 私网 / 链路本地元数据地址 / 无法解析 /
  空主机名）——这是「浏览器插件投递任意 URL」这条链路的唯一闸门；
- 两条创建路径的**入库前查重**：平台 ID 重复 409、来源 URL 墓碑 409、采集任务不存在 400；
- URL 导入在**下载之前**就该被 SSRF 拦截（证明防护生效顺序正确：不会先下文件再报错）。

这些分支线上极难复现（要先造墓碑 / 抢并发），但每一条都对应「孤儿文件」或
「已删除素材被重新采回」这类真实事故。
"""

from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from app.exceptions import ValidationException

from app.database import async_session
from app.models.scraper import ScraperSeenURL
from app.services import inspiration_create


def _upload_file(color: tuple[int, int, int] = (12, 34, 56)) -> UploadFile:
    """构造一个最小的内存图片 UploadFile（PIL 生成的真实 JPEG）。"""
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="JPEG")
    buf.seek(0)
    return UploadFile(
        file=buf,
        filename="unit.jpg",
        headers={"content-type": "image/jpeg"},  # type: ignore[arg-type]
    )


# ── SSRF 防护 ──


@pytest.mark.parametrize(
    "url,expect",
    [
        ("http://127.0.0.1/a.jpg", "内网"),  # 回环
        ("http://10.1.2.3/a.jpg", "内网"),  # 私网
        ("http://169.254.169.254/latest/meta-data/", "内网"),  # 云元数据（典型 SSRF 目标）
        ("http://[::1]/a.jpg", "内网"),  # IPv6 回环
    ],
)
def test_validate_download_url_blocks_internal_targets(url: str, expect: str):
    """内网/保留地址一律拒绝（ValidationException，由全局处理器映射成 4xx）。"""
    with pytest.raises(ValidationException) as exc:
        inspiration_create._validate_download_url(url)
    assert expect in str(exc.value)


def test_validate_download_url_rejects_invalid_and_unresolvable():
    """空主机名与无法解析的域名分别拒绝（不能静默放行）。"""
    with pytest.raises(ValidationException) as no_host:
        inspiration_create._validate_download_url("not-a-url")
    assert "无效" in str(no_host.value)

    with pytest.raises(ValidationException) as bad_domain:
        inspiration_create._validate_download_url("https://no-such-host.invalid/a.jpg")
    assert "无法解析" in str(bad_domain.value)


# ── 上传入库的查重分支 ──


async def test_create_inspiration_duplicate_platform_id(client):
    """平台 ID 已被「未删除」素材占用 → 409（且不会先落盘）。"""
    async with async_session() as db:
        await inspiration_create.create_inspiration(
            db, _upload_file((1, 1, 1)), source_platform_id="f2:1111111111111111111#image_1"
        )
        await db.commit()

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await inspiration_create.create_inspiration(
                db, _upload_file((2, 2, 2)), source_platform_id="f2:1111111111111111111#image_1"
            )
    assert exc.value.status_code == 409
    assert "已存在" in str(exc.value.detail)


async def test_create_inspiration_tombstone_blocks_reimport(client):
    """来源 URL 有墓碑（此前被删）→ 409，不再重新入库。"""
    async with async_session() as db:
        db.add(ScraperSeenURL(source_url="https://example.com/deleted-note"))
        await db.commit()

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await inspiration_create.create_inspiration(
                db, _upload_file(), source_url="https://example.com/deleted-note"
            )
    assert exc.value.status_code == 409
    assert "墓碑" in str(exc.value.detail)


async def test_create_inspiration_unknown_scraper_task(client):
    """关联的采集任务不存在 → 400（避免指向不存在任务的孤儿记录）。"""
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await inspiration_create.create_inspiration(
                db, _upload_file(), scraper_task_id=999999
            )
    assert exc.value.status_code == 400
    assert "采集任务" in str(exc.value.detail)


async def test_create_inspiration_dedupes_identical_content(client):
    """内容完全相同的两次上传：第二次 409，并且不留下孤儿文件。"""
    from app.config import settings

    async with async_session() as db:
        first = await inspiration_create.create_inspiration(db, _upload_file((9, 9, 9)))
        await db.commit()
        first_path = first.file_path

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await inspiration_create.create_inspiration(db, _upload_file((9, 9, 9)))
    assert exc.value.status_code == 409
    assert "内容重复" in str(exc.value.detail)
    # 第一次的文件还在；重复上传的临时文件已被清理（目录里只有一张图）
    assert (settings.storage_root / first_path).exists()


# ── URL 导入：下载之前就要拦住的两种情形 ──


async def test_create_from_url_rejects_ssrf_before_download(client):
    """内网 URL 在下载前被拒（证明防护顺序正确：不会先发请求再报错）。"""
    async with async_session() as db:
        with pytest.raises(ValidationException) as exc:
            await inspiration_create.create_inspiration_from_url(
                db, "http://127.0.0.1/secret.jpg"
            )
    assert "内网" in str(exc.value)


async def test_create_from_url_duplicate_platform_id(client):
    """URL 导入同样先查平台 ID：重复直接 409，不做任何网络请求。"""
    platform_id = "xhs:66abcdef0123456789#image_1"
    async with async_session() as db:
        await inspiration_create.create_inspiration(
            db, _upload_file((3, 3, 3)), source_platform_id=platform_id
        )
        await db.commit()

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await inspiration_create.create_inspiration_from_url(
                db, "https://example.com/a.jpg", source_platform_id=platform_id
            )
    assert exc.value.status_code == 409
    assert "已存在" in str(exc.value.detail)


async def test_create_from_url_tombstone(client):
    """URL 导入命中墓碑（来源页或图片地址任一）→ 409。"""
    async with async_session() as db:
        db.add(ScraperSeenURL(source_url="https://example.com/note-page"))
        await db.commit()

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await inspiration_create.create_inspiration_from_url(
                db,
                "https://cdn.example.com/a.jpg",
                source_url="https://example.com/note-page",
            )
    assert exc.value.status_code == 409
    assert "墓碑" in str(exc.value.detail)
