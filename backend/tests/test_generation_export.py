"""P2-C2：生成历史导出（CSV / JSON）。

导出最容易出的两类问题都锁在这里：
1. **筛出来的和导出的不一致**（复用列表的 `_apply_run_filters`，用 q 验证）；
2. **CSV 中文乱码**（路由加 UTF-8 BOM；Excel 直接打开不乱码）。
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.database import async_session
from app.models.generation import GenerationRun


def _seed_run(prompt: str, *, status: str = "done") -> int:
    async def _do() -> int:
        async with async_session() as db:
            run = GenerationRun(
                kind="i2i",
                status=status,
                prompt=prompt,
                params_json="{}",
                model_key="qwen_2_1_uc_i2i",
                workflow_name="qwen_image_i2i",
                total=2,
                done=2,
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)
            return run.id

    return asyncio.run(_do())


def _cleanup(run_ids: list[int]) -> None:
    async def _do() -> None:
        async with async_session() as db:
            for rid in run_ids:
                row = await db.get(GenerationRun, rid)
                if row is not None:
                    await db.delete(row)
            await db.commit()

    asyncio.run(_do())


def test_export_csv_has_bom_header_and_filename(client):
    rid = _seed_run("导出用的米色针织开衫")
    try:
        resp = client.get(
            "/api/generate/runs/export", params={"fmt": "csv", "q": "导出用的米色针织开衫"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/csv")
        assert "generate-history-" in resp.headers["content-disposition"]

        # 路由统一加 BOM：Excel 打开中文不乱码
        assert resp.content.startswith("\ufeff".encode())
        text = resp.content.decode("utf-8-sig")

        header = text.splitlines()[0]
        assert header.split(",")[0] == "id"
        assert "prompt" in header and "imported_count" in header
        assert "导出用的米色针织开衫" in text
        assert str(rid) in text
    finally:
        _cleanup([rid])


def test_export_json_is_parseable_and_filtered(client):
    keep = _seed_run("JSON 导出的通勤衬衫")
    other = _seed_run("别的描述不该出现")
    try:
        resp = client.get(
            "/api/generate/runs/export",
            params={"fmt": "json", "q": "JSON 导出的通勤衬衫"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

        import json

        rows = json.loads(resp.text)
        ids = {row["id"] for row in rows}
        assert keep in ids
        assert other not in ids
        # 导出字段：不含候选文件（1.6 MB/张不适合塞导出）
        assert "candidates" not in rows[0]
        assert set(rows[0]) >= {"id", "created_at", "status", "prompt", "imported_count"}
    finally:
        _cleanup([keep, other])


def test_export_rejects_unknown_format(client):
    resp = client.get("/api/generate/runs/export", params={"fmt": "xlsx"})
    assert resp.status_code == 422


@pytest.mark.parametrize("fav", [True, False])
def test_export_respects_favorite_filter(client, fav):
    fav_run = _seed_run("收藏筛选测试 A")
    plain = _seed_run("收藏筛选测试 B")

    async def _mark() -> None:
        async with async_session() as db:
            row = await db.get(GenerationRun, fav_run)
            row.favorite = True
            await db.commit()

    asyncio.run(_mark())
    try:
        resp = client.get(
            "/api/generate/runs/export",
            params={"fmt": "json", "favorite": str(fav).lower(), "q": "收藏筛选测试"},
        )
        rows = __import__("json").loads(resp.text)
        ids = {row["id"] for row in rows}
        if fav:
            assert fav_run in ids and plain not in ids
        else:
            assert plain in ids and fav_run not in ids
    finally:
        _cleanup([fav_run, plain])


def test_export_empty_result_still_returns_header(client):
    """没有匹配行时也要给出表头（不要让用户拿到 0 字节空文件）。"""
    resp = client.get(
        "/api/generate/runs/export", params={"fmt": "csv", "q": "绝不存在的关键字-xyz"}
    )
    assert resp.status_code == 200
    text = resp.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("id,")
    assert len(text.splitlines()) == 1


def test_export_cap_is_5000():
    from app.services.generation_service import EXPORT_MAX_ROWS

    assert EXPORT_MAX_ROWS == 5000


def test_export_uses_settings_storage_for_imported_counts(client):
    """导出里的 imported_count 与列表同一口径（按 generation_run_id 聚合）。"""
    from app.models.inspiration import Inspiration

    rid = _seed_run("导入计数导出测试")

    async def _add_material() -> None:
        async with async_session() as db:
            db.add(
                Inspiration(
                    source_type="generated",
                    file_path="images/gen/exp.png",
                    media_type="image",
                    content_hash="e" * 64,
                    generation_run_id=rid,
                )
            )
            await db.commit()

    asyncio.run(_add_material())
    try:
        resp = client.get(
            "/api/generate/runs/export",
            params={"fmt": "json", "q": "导入计数导出测试"},
        )
        rows = __import__("json").loads(resp.text)
        assert rows[0]["imported_count"] == 1
    finally:
        _cleanup([rid])
    assert settings.storage_root  # 仅确保 settings 已加载（避免误用真实路径的回归）
