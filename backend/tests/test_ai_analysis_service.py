"""AI 分析业务逻辑（历史/队列/未分析/结果对比）集成测试，纯 DB、无 Ollama。"""

from app.database import async_session
from app.models.inspiration import AIAnalysisLog


async def _add_log(
    inspiration_id: str,
    *,
    model: str = "qwen3-vl:8b-instruct",
    error: str | None = None,
    raw: str | None = None,
) -> int:
    """直接插入一条标签分析日志，返回日志 ID。"""
    async with async_session() as db:
        log = AIAnalysisLog(
            inspiration_id=inspiration_id,
            model_name=model,
            log_type="analysis",
            error=error,
            raw_response=raw,
            processing_time_ms=100,
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return log.id


async def test_analysis_history_list(client, upload):
    """分析历史列表：返回成功/失败日志。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    await _add_log(a, error=None)
    await _add_log(b, error="解析失败")

    data = client.get("/api/ai/history").json()
    assert data["total"] == 2
    assert {it["status"] for it in data["items"]} == {"success", "error"}


async def test_analysis_history_filter_status(client, upload):
    """按状态筛选历史：只返回失败日志。"""
    a = upload().json()["id"]
    await _add_log(a, error=None)
    await _add_log(a, error="解析失败")

    data = client.get("/api/ai/history", params={"status": "error"}).json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "error"


async def test_analysis_history_detail(client, upload):
    """历史详情：解析 raw_response 供前端展示。"""
    insp_id = upload().json()["id"]
    log_id = await _add_log(insp_id, raw='{"style": ["法式"]}')

    data = client.get(f"/api/ai/history/{log_id}").json()
    assert data["id"] == log_id
    assert data["parsed_response"]["style"] == ["法式"]
    assert data["structured_tags"] == []


async def test_analysis_history_delete(client, upload):
    """删除历史日志后，详情返回 404。"""
    insp_id = upload().json()["id"]
    log_id = await _add_log(insp_id)

    assert client.delete(f"/api/ai/history/{log_id}").status_code == 200
    assert client.get(f"/api/ai/history/{log_id}").status_code == 404


async def test_history_model_names(client, upload):
    """历史模型名称列表（去重排序）。"""
    insp_id = upload().json()["id"]
    await _add_log(insp_id, model="qwen3-vl:8b-instruct")
    await _add_log(insp_id, model="llava:7b")

    data = client.get("/api/ai/history/model-names").json()
    assert set(data["models"]) == {"qwen3-vl:8b-instruct", "llava:7b"}


async def test_unanalyzed_ids(client, upload):
    """未分析素材 ID 列表：分析一条后从列表剔除。"""
    a = upload().json()["id"]
    b = upload().json()["id"]

    data = client.get("/api/ai/unanalyzed-ids").json()
    assert set(data["ids"]) == {a, b}

    await _add_log(a)
    data = client.get("/api/ai/unanalyzed-ids").json()
    assert data["ids"] == [b]


async def test_analysis_queue_stats(client, upload):
    """分析队列统计：总数/已分析/失败/未分析。"""
    a = upload().json()["id"]
    await _add_log(a, error=None)
    b = upload().json()["id"]
    await _add_log(b, error="失败")

    data = client.get("/api/ai/queue").json()
    assert data["total"] == 2
    assert data["analyzed"] == 2
    assert data["failed"] == 1
    assert data["unanalyzed"] == 0


async def test_analysis_comparison(client, upload):
    """结果对比：两次分析的标签差异（新增/共同）。"""
    insp_id = upload().json()["id"]
    await _add_log(insp_id, raw='{"style": ["法式"]}')
    await _add_log(insp_id, raw='{"style": ["法式", "日系"]}')

    data = client.get(f"/api/ai/compare/{insp_id}").json()
    assert data["analyses_count"] == 2
    assert data["tag_diff"]["added"] == ["日系"]
    assert data["tag_diff"]["common"] == ["法式"]


def test_analysis_comparison_not_found(client, upload):
    """无分析记录的素材，对比接口返回 404。"""
    insp_id = upload().json()["id"]
    assert client.get(f"/api/ai/compare/{insp_id}").status_code == 404


def test_analyze_not_found(client):
    """触发分析：素材不存在返回 404。"""
    assert client.post("/api/ai/analyze/no-such-id").status_code == 404
