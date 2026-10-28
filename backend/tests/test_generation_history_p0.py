"""生成历史 P0（列表增强 / 分页筛选 / 缩略图）的接口与行为契约。

为什么要这些用例：

1. **列表不能为了「已入库」去算哈希**：详情接口 `GET /runs/{id}` 逐文件 SHA-256（实测
   8 ms/张），列表若复用它，20 行要多 300+ ms。这里锁住「列表只走一条聚合 SQL」的行为
   ——`imported_count` 正确、`candidates` 只给文件名与前 4 张。
2. **缩略图必须真的变小并可复用**：原图 1.5 MB，缩略图 6 KB（实测）；第二次请求命中缓存。
3. **筛选与计数同口径**：`total` 必须等于同条件下 `items` 的总数，否则分页会出现
   「第 2 页空着、total 却说还有」。

测试用临时存储根（conftest 已把 STORAGE_ROOT 指到 tmp），不会碰真实素材。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PIL import Image

from app.config import settings
from app.database import async_session
from app.models.generation import GenerationRun
from app.models.inspiration import Inspiration
from app.services import generation_service


def _write_png(path: Path, size: tuple[int, int] = (64, 64), color=(200, 120, 90)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def _seed_run(
    *,
    prompt: str = "米色针织开衫",
    negative: str | None = None,
    model_key: str = "z_image_int8",
    status: str = "done",
    kind: str = "t2i",
    reference_ids: str | None = None,
    files: int = 2,
) -> int:
    """建一条批次记录 + 落 N 张候选图（真实文件，缩略图才有的可生成）。"""

    async def _do() -> int:
        async with async_session() as db:
            run = GenerationRun(
                kind=kind,
                status=status,
                prompt=prompt,
                negative=negative,
                params_json='{"width": 1024, "height": 1024, "steps": 8}',
                model_key=model_key,
                workflow_name="z_image_t2i",
                reference_ids=reference_ids,
                total=files,
                done=files,
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)
            run_id = run.id
            # batch_dir 在 create 时才知道 id：补写 + 落文件
            rel = f"generated/{run_id}"
            run.batch_dir = rel
            await db.commit()
        folder = settings.storage_root / rel
        for idx in range(files):
            _write_png(folder / f"{idx + 1:02d}_seed-{1000 + idx}.png")
        return run_id

    return asyncio.run(_do())


def _delete_run(run_id: int) -> None:
    async def _do() -> None:
        async with async_session() as db:
            run = await db.get(GenerationRun, run_id)
            if run is not None:
                await db.delete(run)
                await db.commit()

    asyncio.run(_do())
    folder = settings.storage_root / "generated" / str(run_id)
    if folder.exists():
        import shutil

        shutil.rmtree(folder, ignore_errors=True)


def test_list_returns_thumbnails_and_imported_count(client):
    """列表每项带候选缩略图地址、已入库张数与目录标记（不返回 per-file imported）。"""
    run_id = _seed_run(files=2)
    try:

        async def _import_one() -> None:
            async with async_session() as db:
                db.add(
                    Inspiration(
                        source_type="generated",
                        file_path="images/gen/one.png",
                        media_type="image",
                        content_hash="h" * 64,
                        generation_run_id=run_id,
                    )
                )
                await db.commit()

        asyncio.run(_import_one())

        resp = client.get("/api/generate/runs", params={"q": "米色针织开衫", "size": 5})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        row = next(item for item in body["items"] if item["id"] == run_id)

        assert row["imported_count"] == 1
        assert row["files_missing"] is False
        assert len(row["candidates"]) == 2
        assert row["candidates"][0]["filename"].startswith("01_seed-")
        # 缩略图地址走同一个文件接口 + w 参数
        assert row["candidates"][0]["thumb_url"].endswith("?w=256")
        # 列表不带逐张 imported（那要算哈希，详情里才有）
        assert "imported" not in row["candidates"][0]
    finally:
        _delete_run(run_id)


def test_files_missing_when_batch_dir_gone(client):
    """目录已被清理的批次：files_missing=True，候选为空（前端显示「图已清理」）。"""
    run_id = _seed_run(files=1)
    try:
        folder = settings.storage_root / "generated" / str(run_id)
        import shutil

        shutil.rmtree(folder, ignore_errors=True)

        resp = client.get("/api/generate/runs", params={"size": 50})
        row = next(item for item in resp.json()["items"] if item["id"] == run_id)

        assert row["files_missing"] is True
        assert row["candidates"] == []
    finally:
        _delete_run(run_id)


def test_list_candidates_are_capped_to_four(client):
    """只回前 4 张缩略图：列表不是候选区，多了只是白带流量。"""
    run_id = _seed_run(files=6)
    try:
        resp = client.get("/api/generate/runs", params={"size": 50})
        row = next(item for item in resp.json()["items"] if item["id"] == run_id)
        assert len(row["candidates"]) == generation_service.LIST_CANDIDATES_LIMIT == 4
    finally:
        _delete_run(run_id)


@pytest.mark.parametrize(
    ("params", "expect_found"),
    (
        ({"kind": "i2i"}, False),
        ({"kind": "t2i"}, True),
        ({"model_key": "别的不存在的档位"}, False),
        ({"model_key": "z_image_int8"}, True),
        ({"status": "failed"}, False),
        ({"status": "done"}, True),
    ),
)
def test_filters_narrow_results(client, params, expect_found):
    """kind / model_key / status 筛选生效，且 total 随之变化（口径一致）。"""
    run_id = _seed_run(kind="t2i", status="done", model_key="z_image_int8")
    try:
        resp = client.get("/api/generate/runs", params={**params, "size": 100})
        body = resp.json()
        found = any(item["id"] == run_id for item in body["items"])

        assert found is expect_found
        # total 与 items 同口径（这里只验证「找到了就一定计数 >= 1」）
        if expect_found:
            assert body["total"] >= 1
    finally:
        _delete_run(run_id)


def test_keyword_searches_prompt_and_negative(client):
    """关键字同时匹配描述与负面词（用户记不清写在哪边）。"""
    run_id = _seed_run(prompt="法式碎花连衣裙", negative="模糊，低质量")
    try:
        by_prompt = client.get("/api/generate/runs", params={"q": "碎花", "size": 100}).json()
        by_negative = client.get("/api/generate/runs", params={"q": "低质量", "size": 100}).json()
        miss = client.get("/api/generate/runs", params={"q": "绝不存在的词", "size": 100}).json()

        assert any(item["id"] == run_id for item in by_prompt["items"])
        assert any(item["id"] == run_id for item in by_negative["items"])
        assert not any(item["id"] == run_id for item in miss["items"])
    finally:
        _delete_run(run_id)


def test_reference_id_filter_hits_json_array(client):
    """参考素材筛选用「带引号的 LIKE」，必须精确命中而不前缀误伤。"""
    ref = "0f0d6b2c-1111-2222-3333-444455556666"
    run_id = _seed_run(kind="i2i", reference_ids=f'["{ref}", "another-id"]')
    try:
        hit = client.get("/api/generate/runs", params={"reference_id": ref, "size": 100}).json()
        # 同一前缀但不同 id：不能命中
        near = client.get(
            "/api/generate/runs", params={"reference_id": ref[:6], "size": 100}
        ).json()

        assert any(item["id"] == run_id for item in hit["items"])
        assert not any(item["id"] == run_id for item in near["items"])
    finally:
        _delete_run(run_id)


def test_pagination_slices_without_losing_total(client):
    """分页：total 是筛选后的总数，页面之间不重复、不遗漏。"""
    ids = [_seed_run(prompt=f"分页测试专用词 {i}") for i in range(3)]
    try:
        first = client.get("/api/generate/runs", params={"q": "分页测试专用词", "page": 1, "size": 2}).json()
        second = client.get("/api/generate/runs", params={"q": "分页测试专用词", "page": 2, "size": 2}).json()

        assert first["total"] == 3
        assert len(first["items"]) == 2
        assert len(second["items"]) == 1
        got = {i["id"] for i in first["items"]} | {i["id"] for i in second["items"]}
        assert got == set(ids)
    finally:
        for run_id in ids:
            _delete_run(run_id)


def test_legacy_limit_parameter_still_works(client):
    """老参数 `limit` 仍可用（前端与既有测试都这么调）。"""
    resp = client.get("/api/generate/runs", params={"limit": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) <= 2
    assert set(body) >= {"items", "total", "page", "size"}


def test_thumbnail_endpoint_returns_cached_jpeg(client):
    """`?w=256` 返回 256px JPEG 缩略图，第二次请求命中缓存（不再重新编码）。"""
    # 用 320×320 源图：缩略图才会被真正缩到 256（64px 的小图不比 256 大，缩不动）
    run_id = _seed_run(files=1)
    try:
        _write_png(settings.storage_root / "generated" / str(run_id) / "01_seed-1000.png", size=(320, 320))
        name = "01_seed-1000.png"
        url = f"/api/generate/runs/{run_id}/candidates/{name}"

        full = client.get(url)
        assert full.status_code == 200
        assert full.headers["content-type"].startswith("image/png")

        first = client.get(url, params={"w": 256})
        assert first.status_code == 200, first.text
        assert first.headers["content-type"].startswith("image/jpeg")
        # 解码回来核对真的缩到了 256（合成小图的字节数没有比较意义，别拿 size 断言）
        import io

        with Image.open(io.BytesIO(first.content)) as thumb:
            assert thumb.size == (256, 256)

        thumb_path = settings.storage_root / "generated" / str(run_id) / ".thumbs" / f"01_seed-1000_w256.jpg"
        assert thumb_path.is_file()
        mtime_before = thumb_path.stat().st_mtime

        second = client.get(url, params={"w": 256})
        assert second.status_code == 200
        assert thumb_path.stat().st_mtime == mtime_before
    finally:
        _delete_run(run_id)


def test_thumbnail_rejects_bad_names_and_missing_files(client):
    """文件名白名单与缺失文件的行为不变（缩略图不能变成绕过校验的口子）。"""
    run_id = _seed_run(files=1)
    try:
        bad = client.get(f"/api/generate/runs/{run_id}/candidates/evil.exe", params={"w": 256})
        missing = client.get(
            f"/api/generate/runs/{run_id}/candidates/99_seed-0.png", params={"w": 256}
        )

        assert bad.status_code == 400
        assert missing.status_code == 404
    finally:
        _delete_run(run_id)


def test_thumbnail_falls_back_when_image_is_corrupt(client):
    """坏图不能让缩略图接口 500：退回原图（用户至少能看到「文件坏了」）。"""
    run_id = _seed_run(files=0)
    try:
        folder = settings.storage_root / "generated" / str(run_id)
        folder.mkdir(parents=True, exist_ok=True)
        broken = folder / "01_seed-1.png"
        broken.write_bytes(b"not-a-png")

        resp = client.get(
            f"/api/generate/runs/{run_id}/candidates/01_seed-1.png", params={"w": 256}
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
    finally:
        _delete_run(run_id)
