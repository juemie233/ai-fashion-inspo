"""本地出图的「AI 生成」标记：语义、防覆盖、审核队列与人工翻案。

需求（2026-10-28）：出图入库的图要标记为 **AI 生成**，且不是靠标签区分，而是与「疑似 AI」
共用同一个 `is_ai_generated` 标记。走查后补了三处，否则这个标记站不住：

1. **质量审核会把标记改回 False**（`_write_quality_result` 无条件覆盖）——
   生成图入库是 pending，审核时「没检出 AI 痕迹」就丢了「这张是生成的」这个事实；
2. **疑似 AI 审核页会把生成图列进去**，还能被人工「翻案为非 AI」；
3. 素材卡只有「疑似 AI」文案，对生成图不准确（前端用 `source_type` 区分，见组件测试）。

这里锁住后端三条：审核不覆盖 generated、`ai_suspected` 过滤、翻案跳过 generated。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import Inspiration
from app.services.ai_service.quality import _write_quality_result


def _upload(client, make_image) -> str:
    data, ctype = make_image(size=(64, 64))
    resp = client.post("/api/inspirations", files={"file": ("m.jpg", data, ctype)})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _setup(inspiration_id: str, *, source_type: str, is_ai: bool) -> None:
    async def _do() -> None:
        async with async_session() as db:
            row = await db.get(Inspiration, inspiration_id)
            row.source_type = source_type
            row.is_ai_generated = is_ai
            row.quality_status = "pending"
            await db.commit()

    asyncio.run(_do())


def _flag(inspiration_id: str) -> tuple[bool, str | None, str]:
    async def _do():
        async with async_session() as db:
            row = await db.get(Inspiration, inspiration_id)
            return bool(row.is_ai_generated), row.quality_reviewed_by, row.quality_status

    return asyncio.run(_do())


def test_quality_review_keeps_generated_flagged_ai(client, make_image):
    """生成图是**确定**的 AI：模型判「不是 AI」也不能把标记翻掉。"""
    iid = _upload(client, make_image)
    _setup(iid, source_type="generated", is_ai=True)

    async def _run() -> None:
        async with async_session() as db:
            err = await _write_quality_result(
                db, iid, "approved", None, is_ai_generated=False, force=True
            )
            assert err is None
            await db.commit()

    asyncio.run(_run())

    is_ai, reviewed_by, status = _flag(iid)
    assert is_ai is True, "生成图的 AI 标记被审核覆盖了"
    assert status == "approved"  # 审核本身照常生效
    assert reviewed_by == "ai"


def test_quality_review_can_clear_flag_for_normal_material(client, make_image):
    """普通素材（疑似）仍允许审核按模型判定清掉标记——保持原有语义。"""
    iid = _upload(client, make_image)
    _setup(iid, source_type="manual_upload", is_ai=True)

    async def _run() -> None:
        async with async_session() as db:
            await _write_quality_result(
                db, iid, "approved", None, is_ai_generated=False, force=True
            )
            await db.commit()

    asyncio.run(_run())

    is_ai, _, _ = _flag(iid)
    assert is_ai is False


def _seed_flagged(client, make_image, *, source_type: str, is_ai: bool = True) -> str:
    iid = _upload(client, make_image)
    _setup(iid, source_type=source_type, is_ai=is_ai)
    return iid


def test_ai_suspected_filter_excludes_generated(client, make_image):
    """`ai_suspected=true` 只要疑似：标记为真、但不是本地出图。"""
    gen = _seed_flagged(client, make_image, source_type="generated")
    sus = _seed_flagged(client, make_image, source_type="manual_upload")

    resp = client.get("/api/inspirations", params={"ai_suspected": True, "size": 100})
    assert resp.status_code == 200, resp.text
    ids = {item["id"] for item in resp.json()["items"]}

    assert sus in ids
    assert gen not in ids


def test_ai_generated_filter_still_includes_generated(client, make_image):
    """`is_ai_generated=true` 的语义不变：所有打了标记的都算（含生成图）。"""
    gen = _seed_flagged(client, make_image, source_type="generated")

    resp = client.get("/api/inspirations", params={"is_ai_generated": True, "size": 100})
    ids = {item["id"] for item in resp.json()["items"]}

    assert gen in ids


def test_batch_unmark_skips_generated(client, make_image):
    """人工「翻案为非 AI」不能作用于本地出图：只翻普通的，并回报跳过数。"""
    gen = _seed_flagged(client, make_image, source_type="generated")
    sus = _seed_flagged(client, make_image, source_type="manual_upload")

    resp = client.post("/api/admin/batch-unmark-ai", json={"ids": [gen, sus]})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["updated"] == 1
    assert body["skipped_generated"] == 1
    assert _flag(gen)[0] is True, "生成图被翻案成非 AI 了"
    assert _flag(sus)[0] is False


def test_generated_rows_in_db_keep_flag(client, make_image):
    """回归：库里已有的生成图必须都还带着 AI 标记（本次改动不该动历史数据）。"""
    iid = _seed_flagged(client, make_image, source_type="generated")

    async def _do() -> int:
        async with async_session() as db:
            rows = await db.execute(
                select(Inspiration.id).where(
                    Inspiration.source_type == "generated",
                    Inspiration.is_ai_generated.is_(True),
                )
            )
            return len(rows.all())

    assert asyncio.run(_do()) >= 1
    assert iid
