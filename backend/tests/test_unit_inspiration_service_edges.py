"""上传前内容哈希去重（``inspiration_service.check_duplicate_hash``）的直连测试。

上传接口的去重分支此前只被整链路用例间接覆盖：命中重复时要返回
``exists/inspiration_id/file_path`` 三个字段供前端提示「已存在」。这里直接调
服务函数，锁住「未命中」与「命中且能取回 file_path」两条分支。
"""

from app.database import async_session
from app.models.inspiration import Inspiration
from app.services.inspiration_service import check_duplicate_hash

_HASH = "0f343b0931126a20f133d67c2b018a3b"  # 任意 32 位 hex，测试内部自洽即可


async def _add_inspiration(insp_id: str, content_hash: str | None, file_path: str):
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="manual_upload",
                file_path=file_path,
                content_hash=content_hash,
                media_type="image",
                quality_status="approved",
            )
        )
        await db.commit()


async def test_check_duplicate_hash_not_found(client):
    await _add_inspiration("dup-none", "ffffffffffffffffffffffffffffffff", "images/a.jpg")

    async with async_session() as db:
        result = await check_duplicate_hash(db, _HASH)

    assert result == {"exists": False, "inspiration_id": None, "file_path": None}


async def test_check_duplicate_hash_found_returns_id_and_path(client):
    await _add_inspiration("dup-hit", _HASH, "images/dup-hit.jpg")

    async with async_session() as db:
        result = await check_duplicate_hash(db, _HASH)

    assert result == {
        "exists": True,
        "inspiration_id": "dup-hit",
        "file_path": "images/dup-hit.jpg",
    }
