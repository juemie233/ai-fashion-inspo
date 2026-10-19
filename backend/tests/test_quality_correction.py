"""质量审核误判反馈闭环（审查 P0）。

「AI 判错了」要能落成结构化记录（原判定 / 纠正结果 / 错在哪类 / 备注），
并顺手把素材状态改正、置 human —— 随机复审从此不会覆盖人工纠正。
"""

import asyncio

from app.database import async_session
from app.models.inspiration import Inspiration


def _reviewed_by(insp_id: str) -> str | None:
    """读库拿判定来源（详情接口未暴露该字段，属正常：它只用于后端保护逻辑）。"""

    async def _do():
        async with async_session() as db:
            row = await db.get(Inspiration, insp_id)
            return row.quality_reviewed_by if row else None

    return asyncio.run(_do())


def _submit(client, insp_id: str, corrected: str, **extra):
    return client.post(
        "/api/ai/quality-corrections",
        json={"inspiration_id": insp_id, "corrected_status": corrected, **extra},
    )


def test_correction_flips_status_and_records(client, upload):
    """提交「应是 approved」→ 状态改正 + 记 human + 记录可查。"""
    insp = upload().json()
    # 先让它处于 rejected（模拟 AI 判错）
    client.patch(f"/api/inspirations/{insp['id']}", json={"quality_status": "rejected"})

    r = _submit(client, insp["id"], "approved", reason_code="not_outfit", note="其实是穿搭")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["original_status"] == "rejected"
    assert body["corrected_status"] == "approved"

    detail = client.get(f"/api/inspirations/{insp['id']}").json()
    assert detail["quality_status"] == "approved"
    # 判定来源只在库里（后端保护逻辑用），通过 ORM 校验
    assert _reviewed_by(insp["id"]) == "human"

    listing = client.get("/api/ai/quality-corrections").json()
    assert listing["total"] >= 1
    assert listing["items"][0]["inspiration_id"] == insp["id"]


def test_correction_rejects_invalid_target(client, upload):
    insp = upload().json()
    r = _submit(client, insp["id"], "maybe")
    assert r.status_code == 400


def test_correction_404_for_unknown(client):
    r = _submit(client, "no-such-id", "approved")
    assert r.status_code == 404


def test_correction_stats_shape(client, upload):
    insp = upload().json()
    client.patch(f"/api/inspirations/{insp['id']}", json={"quality_status": "rejected"})
    _submit(client, insp["id"], "approved", reason_code="partial")
    stats = client.get("/api/ai/quality-corrections/stats").json()
    assert stats["total"] >= 1
    assert stats["by_reason_code"].get("partial", 0) >= 1
    assert stats["by_direction"].get("rejected->approved", 0) >= 1
