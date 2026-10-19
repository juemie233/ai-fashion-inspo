"""按 prompt 指纹对比审核结果（审查 P2）。

有了 `prompt_hash`（老 #5）才能回答「换 prompt 后判定是不是变了」：
同 hash 一组内比较通过率，unknown 组单独标出（历史数据不可比）。
"""

import asyncio

from app.config import settings
from app.database import async_session
from app.models.inspiration import AIAnalysisLog, AIQualityReview


def _seed(insp_id: str, prompt_hash: str | None, result: str) -> None:
    async def _do():
        async with async_session() as db:
            log = AIAnalysisLog(
                inspiration_id=insp_id,
                model_name=settings.ollama_vision_model,
                log_type="quality_check",
            )
            db.add(log)
            await db.flush()
            db.add(
                AIQualityReview(
                    log_id=log.id,
                    result=result,
                    reason="测试用",
                    prompt_hash=prompt_hash,
                )
            )
            await db.commit()

    asyncio.run(_do())


def test_prompt_ab_groups_by_hash(client, upload):
    """两个 prompt 各自成组，通过率分开算；历史 NULL 归入 unknown 且标记不可比。"""
    a1, a2, b1, c1 = (upload().json()["id"] for _ in range(4))
    _seed(a1, "a" * 64, "approved")
    _seed(a2, "a" * 64, "rejected")
    _seed(b1, "b" * 64, "approved")
    _seed(c1, None, "rejected")

    body = client.get("/api/ai/quality-prompt-ab").json()
    by_hash = {item["prompt_hash"]: item for item in body["items"]}

    assert body["count"] == 3, body
    # 同 hash 的两次审核合并成一组（通过率 50%）
    assert by_hash["aaaaaaaa"]["total"] == 2
    assert by_hash["aaaaaaaa"]["pass_rate"] == 50.0
    assert by_hash["bbbbbbbb"]["pass_rate"] == 100.0
    # 历史无 hash：单独一组且 known=False
    assert by_hash["unknown"]["known"] is False
    assert by_hash["unknown"]["rejected"] == 1


def test_prompt_ab_empty(client):
    body = client.get("/api/ai/quality-prompt-ab").json()
    assert body["items"] == []
    assert body["count"] == 0
