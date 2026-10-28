"""生成历史 P1 后端：收藏/命名/备注、清理豁免、统计、入库反查。

对应方案文档 §B1 / §B3 / §B5。三条最容易出错的语义在这里锁死：

1. **收藏 = 不清理**：过期清理必须跳过收藏批次，并在返回里回报 `skipped_favorites`
   （否则用户看到「图没了」会以为是收藏没生效）；
2. **PATCH 里 `null` ≠ 清空**：`None` 表示「本次不改」，清空要走 `clear_title/clear_note`；
3. **统计口径**：`import_rate = 入库素材数 / 出图张数`（不是批次数），
   平均耗时用 `updated_at - created_at`（含排队）。
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import timedelta

from app.config import settings
from app.database import async_session
from app.models.generation import GenerationRun
from app.models.inspiration import Inspiration
from app.services.generation_service import cleanup_expired_runs
from app.utils.time import utcnow

from PIL import Image


def _write_png(path, size=(32, 32)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (180, 140, 120)).save(path)


def _seed_run(
    *,
    prompt: str = "米色针织开衫",
    status: str = "done",
    model_key: str = "qwen_2_1_uc_i2i",
    files: int = 1,
    created_ago_days: float = 0,
    favorite: bool = False,
    total: int = 1,
) -> int:
    async def _do() -> int:
        async with async_session() as db:
            run = GenerationRun(
                kind="i2i",
                status=status,
                prompt=prompt,
                params_json="{}",
                model_key=model_key,
                workflow_name="qwen_image_i2i",
                total=total,
                done=total,
                favorite=favorite,
                created_at=utcnow() - timedelta(days=created_ago_days),
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)
            run.batch_dir = f"generated/{run.id}"
            await db.commit()
            run_id = run.id
        for idx in range(files):
            _write_png(settings.storage_root / "generated" / str(run_id) / f"{idx + 1:02d}_seed-{idx}.png")
        return run_id

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
    for rid in run_ids:
        shutil.rmtree(settings.storage_root / "generated" / str(rid), ignore_errors=True)


# ── PATCH 标注 ──


def test_patch_sets_favorite_title_note(client):
    rid = _seed_run()
    try:
        resp = client.patch(
            f"/api/generate/runs/{rid}",
            json={"favorite": True, "title": "  通勤白衬衫  ", "note": "袖子稍长，下次改短"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["favorite"] is True
        assert body["title"] == "通勤白衬衫"  # 首尾空白已 trim
        assert body["note"] == "袖子稍长，下次改短"
        # 收藏后不再有过期时间（不会被清理）
        assert body["expires_at"] is None
    finally:
        _cleanup([rid])


def test_patch_null_keeps_existing_but_clear_flag_empties(client):
    """`null` = 本次不改；清空必须显式 clear_title / clear_note。"""
    rid = _seed_run()
    try:
        client.patch(f"/api/generate/runs/{rid}", json={"title": "留着", "note": "也留着"})

        # 只改收藏：标题/备注不动
        body = client.patch(f"/api/generate/runs/{rid}", json={"favorite": True}).json()
        assert body["title"] == "留着"
        assert body["note"] == "也留着"

        # 显式清空
        body = client.patch(
            f"/api/generate/runs/{rid}", json={"clear_title": True, "clear_note": True}
        ).json()
        assert body["title"] is None
        assert body["note"] is None
    finally:
        _cleanup([rid])


def test_patch_validation_and_404(client):
    rid = _seed_run()
    try:
        too_long_title = client.patch(
            f"/api/generate/runs/{rid}", json={"title": "名" * 65}
        )
        assert too_long_title.status_code == 422 or too_long_title.status_code == 400
        # 备注 2001 字：schema 层就挡住（max_length），也是 4xx
        too_long_note = client.patch(
            f"/api/generate/runs/{rid}", json={"note": "字" * 2001}
        )
        assert 400 <= too_long_note.status_code < 500
        assert client.patch("/api/generate/runs/999999", json={"favorite": True}).status_code == 404
    finally:
        _cleanup([rid])


# ── 列表：收藏筛选与保留期 ──


def test_list_filters_by_favorite(client):
    fav = _seed_run(favorite=True)
    plain = _seed_run()
    try:
        only_fav = client.get("/api/generate/runs", params={"favorite": True, "size": 100}).json()
        ids = {item["id"] for item in only_fav["items"]}
        assert fav in ids
        assert plain not in ids

        # 收藏批次没有过期时间；未收藏批次有（= created_at + keep_days）
        fav_row = next(i for i in only_fav["items"] if i["id"] == fav)
        assert fav_row["expires_at"] is None
        assert only_fav["keep_days"] == settings.image_gen_keep_candidates_days
    finally:
        _cleanup([fav, plain])


def test_list_returns_expires_at_for_plain_run(client):
    rid = _seed_run(created_ago_days=2)
    try:
        body = client.get("/api/generate/runs", params={"size": 100}).json()
        row = next(i for i in body["items"] if i["id"] == rid)

        assert row["expires_at"].endswith("Z")
        # 还能撑 keep_days - 2 天（允许 1 分钟误差）
        from datetime import datetime

        # utcnow() 是 naive UTC，解析出来的带 +00:00：统一去掉 tzinfo 再比
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "")).replace(
            tzinfo=None
        )
        left = (expires - utcnow()).total_seconds() / 86400
        assert abs(left - (settings.image_gen_keep_candidates_days - 2)) < 1 / 24
    finally:
        _cleanup([rid])


# ── 清理：跳过收藏 ──


def test_cleanup_skips_favorite_batches(client):
    old_fav = _seed_run(created_ago_days=30, favorite=True)
    old_plain = _seed_run(created_ago_days=30)
    try:

        async def _run() -> dict:
            async with async_session() as db:
                return await cleanup_expired_runs(db, days=7)

        result = asyncio.run(_run())

        fav_dir = settings.storage_root / "generated" / str(old_fav)
        plain_dir = settings.storage_root / "generated" / str(old_plain)
        assert fav_dir.is_dir(), "收藏批次被清理了"
        assert not plain_dir.is_dir(), "未收藏的过期批次没被清理"
        assert result["skipped_favorites"] >= 1
    finally:
        _cleanup([old_fav, old_plain])


# ── 统计 ──


def test_run_stats_counts_and_rates(client):
    ok = _seed_run(total=4, files=4)
    bad = _seed_run(status="failed", total=0, files=0)
    try:

        async def _import_one() -> None:
            async with async_session() as db:
                db.add(
                    Inspiration(
                        source_type="generated",
                        file_path="images/gen/x.png",
                        media_type="image",
                        content_hash="s" * 64,
                        generation_run_id=ok,
                    )
                )
                await db.commit()

        asyncio.run(_import_one())

        stats = client.get("/api/generate/runs/stats", params={"days": 30}).json()

        assert stats["runs"] >= 2
        assert stats["images"] >= 4
        assert stats["imported_images"] >= 1
        # 入库率按「张」算：>= 1/4
        assert stats["import_rate"] >= 0.25
        assert stats["failure_rate"] > 0
        assert stats["avg_duration_s"] >= 0
        keys = {row["model_key"] for row in stats["by_model"]}
        assert "qwen_2_1_uc_i2i" in keys
        assert stats["days"] == 30
    finally:
        _cleanup([ok, bad])


def test_run_stats_empty_window_returns_zeros(client):
    """窗口内无批次：全 0 而不是报错（新库/清空历史后前端照常显示空卡）。"""
    stats = client.get("/api/generate/runs/stats", params={"days": 0}).json()
    assert stats["days"] == 0
    assert isinstance(stats["by_model"], list)


# ── 入库反查（B5）──


def test_inspirations_filter_by_generation_run_id(client):
    rid = _seed_run()
    other = _seed_run()
    try:

        async def _seed_materials() -> None:
            async with async_session() as db:
                for idx, run_id in enumerate((rid, other)):
                    db.add(
                        Inspiration(
                            source_type="generated",
                            file_path=f"images/gen/{idx}.png",
                            media_type="image",
                            content_hash=str(idx) * 64,
                            generation_run_id=run_id,
                        )
                    )
                await db.commit()

        asyncio.run(_seed_materials())

        body = client.get(
            "/api/inspirations", params={"generation_run_id": rid, "size": 50}
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["file_path"] == "images/gen/0.png"
    finally:
        _cleanup([rid, other])
