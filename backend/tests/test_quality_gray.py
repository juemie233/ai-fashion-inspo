"""灰区优先人工复审（审查 P1）。

「灰区」= 初筛器的垃圾概率落在 0.5 ~ 阈值之间：模型差一点就判垃圾，人工看一眼最值。
随机抽样抽到的是「模型很确定」的样本，信息量低。

注意：默认阈值见 settings.quality_classifier_threshold（当前 0.85）。
"""

import asyncio

from app.config import settings
from app.database import async_session
from app.models.inspiration import AIAnalysisLog, AIQualityReview


def _seed(contest_id: str, confidence: float) -> None:
    async def _do():
        async with async_session() as db:
            log = AIAnalysisLog(
                inspiration_id=contest_id,
                model_name=settings.ollama_vision_model,
                log_type="quality_check",
            )
            db.add(log)
            await db.flush()
            db.add(
                AIQualityReview(
                    log_id=log.id,
                    result="rejected",
                    reason="测试用",
                    confidence=confidence,
                )
            )
            await db.commit()

    asyncio.run(_do())


def test_gray_lists_only_uncertain_samples(client, upload):
    """灰区（0.7）出现，高置信（0.99）与低置信（0.1）都不出现。"""
    gray = upload().json()["id"]
    sure = upload().json()["id"]
    _seed(gray, 0.7)
    _seed(sure, 0.99)

    body = client.get("/api/ai/quality-gray", params={"limit": 50}).json()
    ids = {row["id"] for row in body["items"]}
    assert gray in ids, body
    assert sure not in ids, body
    assert body["threshold"] == settings.quality_classifier_threshold
    # 按置信度降序：越接近阈值越靠前
    confs = [row["confidence"] for row in body["items"]]
    assert confs == sorted(confs, reverse=True)


def test_gray_returns_empty_when_nothing_uncertain(client):
    body = client.get("/api/ai/quality-gray").json()
    assert body["items"] == []
    assert body["count"] == 0
