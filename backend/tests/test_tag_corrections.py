"""AI 打标纠错反馈（tag_corrections）集成测试。

覆盖「标错了」反馈的四类原因：
- multi（AI 多标）→ 删除该素材上该标签的关联；
- missing（AI 漏标）→ 补建关联；
- wrong_category / bad_name → 仅记录，不改动关联；
以及素材不存在 404、非法原因 422、列表与统计端点。
"""

from sqlalchemy import select

from app.database import async_session
from app.models.tag_correction import TagCorrection


async def _link_tag(client, inspiration_id: str, name: str, category: str = "item_type") -> None:
    """给素材关联一个标签（测试前置）。"""
    r = client.post(
        f"/api/inspirations/{inspiration_id}/tags",
        json={"names": [name], "category": category},
    )
    assert r.status_code == 200, r.text


def _tag_names(client, inspiration_id: str) -> set[str]:
    """读取素材当前标签名集合。"""
    detail = client.get(f"/api/inspirations/{inspiration_id}").json()
    return {t["tag"]["name"] for t in detail["tags"]}


async def test_multi_correction_removes_link(client, upload):
    """reason=multi：记录反馈并删除该素材上该标签的关联。"""
    insp_id = upload().json()["id"]
    await _link_tag(client, insp_id, "黑色过膝袜")
    assert "黑色过膝袜" in _tag_names(client, insp_id)

    r = client.post(
        f"/api/inspirations/{insp_id}/tag-corrections",
        json={"tag_name": "黑色过膝袜", "reason": "multi", "note": "图里是长筒袜"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["action"] == "removed"
    assert data["applied"] is True
    assert data["tag_name"] == "黑色过膝袜"

    # 关联已被删除，记录已落库
    assert "黑色过膝袜" not in _tag_names(client, insp_id)
    async with async_session() as db:
        rows = (await db.execute(select(TagCorrection))).scalars().all()
        assert len(rows) == 1
        assert rows[0].reason == "multi"
        assert rows[0].action == "removed"
        assert rows[0].note == "图里是长筒袜"


async def test_missing_correction_adds_link(client, upload):
    """reason=missing：记录反馈并补建标签关联。"""
    insp_id = upload().json()["id"]
    r = client.post(
        f"/api/inspirations/{insp_id}/tag-corrections",
        json={"tag_name": "黑色连裤袜", "reason": "missing", "category": "item_type"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["action"] == "added"
    assert data["applied"] is True
    assert data["category"] == "item_type"

    assert "黑色连裤袜" in _tag_names(client, insp_id)


async def test_missing_correction_is_idempotent(client, upload):
    """漏标反馈对已存在的关联只记录、不重复插入。"""
    insp_id = upload().json()["id"]
    await _link_tag(client, insp_id, "黑色连裤袜")
    r = client.post(
        f"/api/inspirations/{insp_id}/tag-corrections",
        json={"tag_name": "黑色连裤袜", "reason": "missing"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is False

    async with async_session() as db:
        rows = (await db.execute(select(TagCorrection))).scalars().all()
        assert len(rows) == 1  # 反馈仍记录


async def test_wrong_category_only_records(client, upload):
    """reason=wrong_category：仅记录，不改动关联。"""
    insp_id = upload().json()["id"]
    await _link_tag(client, insp_id, "黑色过膝袜")

    r = client.post(
        f"/api/inspirations/{insp_id}/tag-corrections",
        json={"tag_name": "黑色过膝袜", "reason": "wrong_category"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["action"] == "noted"
    assert data["applied"] is False

    # 关联保持不变
    assert "黑色过膝袜" in _tag_names(client, insp_id)


async def test_correction_missing_inspiration_404(client):
    """素材不存在 → 404。"""
    r = client.post(
        "/api/inspirations/not-exist-id/tag-corrections",
        json={"tag_name": "黑色过膝袜", "reason": "multi"},
    )
    assert r.status_code == 404, r.text


async def test_correction_invalid_reason_422(client, upload):
    """非法原因被 Pydantic 拒绝（422）。"""
    insp_id = upload().json()["id"]
    r = client.post(
        f"/api/inspirations/{insp_id}/tag-corrections",
        json={"tag_name": "黑色过膝袜", "reason": "not-a-reason"},
    )
    assert r.status_code == 422, r.text


async def test_correction_list_and_stats(client, upload):
    """列表端点分页 + 统计端点按原因聚合。"""
    insp_id = upload().json()["id"]
    await _link_tag(client, insp_id, "黑色过膝袜")
    for reason, name in (("multi", "黑色过膝袜"), ("missing", "黑色连裤袜"), ("bad_name", "黑色丝袜")):
        r = client.post(
            f"/api/inspirations/{insp_id}/tag-corrections",
            json={"tag_name": name, "reason": reason},
        )
        assert r.status_code == 200, r.text

    listing = client.get("/api/ai/tag-corrections", params={"size": 2}).json()
    assert listing["total"] == 3
    assert len(listing["items"]) == 2
    assert listing["items"][0]["reason_label"]  # 中文标签已填充

    filtered = client.get("/api/ai/tag-corrections", params={"reason": "multi"}).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["tag_name"] == "黑色过膝袜"

    stats = client.get("/api/ai/tag-corrections/stats", params={"days": 30}).json()
    assert stats["total"] == 3
    reasons = {item["reason"]: item["count"] for item in stats["by_reason"]}
    assert reasons == {"multi": 1, "missing": 1, "bad_name": 1}
    actions = {item["action"]: item["count"] for item in stats["by_action"]}
    assert actions.get("removed") == 1 and actions.get("added") == 1 and actions.get("noted") == 1


async def test_correction_records_prompt_version(client, upload):
    """反馈冗余当次分析的 prompt 版本（供看板按版本聚合）。"""
    insp_id = upload().json()["id"]

    # 写入一条带 prompt_version 的分析日志（模拟已分析素材）
    async with async_session() as db:
        from app.models.inspiration import AIAnalysisLog

        db.add(
            AIAnalysisLog(
                inspiration_id=insp_id,
                model_name="qwen3-vl:8b-instruct",
                log_type="analysis",
                raw_response='{"items": []}',
                prompt_version="dd4462cb",
            )
        )
        await db.commit()

    r = client.post(
        f"/api/inspirations/{insp_id}/tag-corrections",
        json={"tag_name": "黑色过膝袜", "reason": "multi"},
    )
    assert r.status_code == 200, r.text

    async with async_session() as db:
        row = (await db.execute(select(TagCorrection))).scalars().one()
        assert row.prompt_version == "dd4462cb"
        assert row.model_name == "qwen3-vl:8b-instruct"
        assert row.log_id is not None
