"""提示词版本质量对比（/api/ai/prompt-quality）集成测试。

覆盖：按版本聚合成功率 / 平均标签数 / 纠错率，以及可选裸词率（采样重放
原始响应统计命中命名口径的裸词占比）。
"""

from app.database import async_session
from app.models.inspiration import AIAnalysisLog, AIAnalysisTag
from app.models.tag import Tag
from app.models.tag_correction import TagCorrection


async def _seed(insp_id: str) -> None:
    """构造两个提示词版本的日志、快照与纠错记录。"""
    async with async_session() as db:
        tag_a = Tag(name="黑色连裤袜", category="item_type", source="ai_generated")
        tag_b = Tag(name="黑色过膝袜", category="item_type", source="ai_generated")
        db.add_all([tag_a, tag_b])
        await db.flush()

        # v1：2 条成功日志（其中 1 条带 2 个标签快照，另 1 条 1 个）
        log1 = AIAnalysisLog(
            inspiration_id=insp_id,
            model_name="m1",
            log_type="analysis",
            raw_response='{"items": [{"type": "黑色连裤袜", "color": "黑色", "features": []}]}',
            prompt_version="testver1",
        )
        log2 = AIAnalysisLog(
            inspiration_id=insp_id,
            model_name="m1",
            log_type="analysis",
            raw_response='{"items": [{"type": "黑色过膝袜", "color": "黑色", "features": []}]}',
            prompt_version="testver1",
        )
        # v2：1 条成功日志，原始响应含裸词「丝袜」
        log3 = AIAnalysisLog(
            inspiration_id=insp_id,
            model_name="m1",
            log_type="analysis",
            raw_response='{"items": [{"type": "丝袜", "color": "黑色", "features": []}]}',
            prompt_version="testver2",
        )
        db.add_all([log1, log2, log3])
        await db.flush()

        db.add(AIAnalysisTag(log_id=log1.id, tag_id=tag_a.id, confidence=0.8))
        db.add(AIAnalysisTag(log_id=log1.id, tag_id=tag_b.id, confidence=0.8))
        db.add(AIAnalysisTag(log_id=log2.id, tag_id=tag_a.id, confidence=0.8))

        # v1 有一条人工纠错反馈
        db.add(
            TagCorrection(
                inspiration_id=insp_id,
                tag_id=tag_b.id,
                tag_name="黑色过膝袜",
                category="item_type",
                reason="multi",
                action="removed",
                prompt_version="testver1",
                model_name="m1",
            )
        )
        await db.commit()


async def test_prompt_quality_aggregation(client, upload):
    """按版本聚合分析次数/成功率/平均标签数/纠错率。"""
    insp_id = upload().json()["id"]
    await _seed(insp_id)

    r = client.get("/api/ai/prompt-quality", params={"days": 30})
    assert r.status_code == 200, r.text
    data = r.json()
    by_version = {item["prompt_version"]: item for item in data["items"]}

    v1 = by_version["testver1"]
    assert v1["analyses"] == 2
    assert v1["successes"] == 2
    assert v1["success_rate"] == 100.0
    assert v1["avg_tags"] == 1.5  # (2 + 1) / 2 次成功
    assert v1["corrections"] == 1
    assert v1["correction_rate"] == 50.0  # 1 / 2 次分析 × 100

    v2 = by_version["testver2"]
    assert v2["analyses"] == 1
    assert v2["corrections"] == 0
    assert v2["correction_rate"] == 0.0
    # 未在版本库登记的哈希：回退显示可辨识标签（不显示裸「未知版本」）
    assert "testver2" in v2["version_label"]


async def test_prompt_quality_bare_rate(client, upload):
    """include_bare_rate=true 时采样重放原始响应，计算裸词率。"""
    insp_id = upload().json()["id"]
    await _seed(insp_id)

    r = client.get(
        "/api/ai/prompt-quality", params={"days": 30, "include_bare_rate": True}
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["include_bare_rate"] is True
    by_version = {item["prompt_version"]: item for item in data["items"]}
    # v1 的原始响应命名合规 → 0%；v2 输出裸词「丝袜」→ 100%
    assert by_version["testver1"]["bare_rate"] == 0.0
    assert by_version["testver2"]["bare_rate"] == 100.0


async def test_prompt_quality_empty_window(client):
    """窗口内无分析记录时返回空列表（不报错）。"""
    r = client.get("/api/ai/prompt-quality", params={"days": 1})
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []
