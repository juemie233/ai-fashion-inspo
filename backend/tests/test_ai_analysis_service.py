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
    """分析队列统计：总数/已分析/失败/未分析。

    口径：unanalyzed = 无成功日志的素材（**含分析失败过的**，与批量分析
    的「已分析跳过」条件一致）；analyzed 为「有过日志」（attempted）。
    """
    a = upload().json()["id"]
    await _add_log(a, error=None)
    b = upload().json()["id"]
    await _add_log(b, error="失败")

    data = client.get("/api/ai/queue").json()
    assert data["total"] == 2
    assert data["analyzed"] == 2
    assert data["failed"] == 1
    assert data["unanalyzed"] == 1  # 失败素材重新纳入未分析（可被批量重跑）


async def test_unanalyzed_ids_include_failed(client, upload):
    """未分析列表包含分析失败过的素材（修复：失败素材不再被批量分析永久排除）。"""
    a = upload().json()["id"]
    await _add_log(a, error=None)
    b = upload().json()["id"]
    await _add_log(b, error="解析失败")

    data = client.get("/api/ai/unanalyzed-ids").json()
    assert data["ids"] == [b]


async def test_analysis_status_latest_success_wins(client, upload):
    """最新一次标签分析成功时，素材状态应为 done（残留失败日志不覆盖）。"""
    insp_id = upload().json()["id"]
    await _add_log(insp_id, error="无法连接 Ollama 服务，请确认 Ollama 已启动")
    await _add_log(insp_id, error=None)

    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["analysis_status"] == "done"


async def test_history_failed_log_shows_no_tags(client, upload):
    """失败日志的标签列应为空，不应误展示素材当前（历史成功留下的）标签。"""
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["法式"]})
    failed_id = await _add_log(insp_id, error="无法连接 Ollama 服务，请确认 Ollama 已启动")

    data = client.get("/api/ai/history").json()
    failed_item = next(it for it in data["items"] if it["id"] == failed_id)
    assert failed_item["status"] == "error"
    assert failed_item["tags"] == []


async def test_history_success_log_uses_own_tags(client, upload):
    """成功日志的标签列展示「本次分析」提取的标签，而非素材当前全量标签。"""
    insp_id = upload().json()["id"]
    # 素材当前有另一条手动标签（不应出现在分析日志的标签列）
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["白色"]})
    # 成功分析提取了「法式」（无结构化快照，回退解析 raw_response）
    log_id = await _add_log(insp_id, raw='{"style": ["法式"]}')

    data = client.get("/api/ai/history").json()
    item = next(it for it in data["items"] if it["id"] == log_id)
    assert item["status"] == "success"
    assert {t["name"] for t in item["tags"]} == {"法式"}


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


def test_batch_analyze_creates_task(client, upload):
    """批量分析：创建任务记录，统计可分析数与跳过数。"""
    a = upload().json()["id"]
    b = upload().json()["id"]

    r = client.post("/api/ai/batch-analyze", json=[a, b, "no-such-id"])
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 2
    assert data["skipped"] == 1
    assert data["task_id"] is not None


async def test_history_batch_delete(client, upload):
    """批量删除分析历史。"""
    insp_id = upload().json()["id"]
    log_a = await _add_log(insp_id)
    log_b = await _add_log(insp_id)

    r = client.post("/api/ai/history/batch-delete", json={"ids": [log_a, log_b]})
    assert r.status_code == 200
    assert r.json()["deleted"] == 2
    assert client.get("/api/ai/history").json()["total"] == 0


async def test_delete_failed_logs(client, upload):
    """删除所有失败日志，保留成功日志。"""
    insp_id = upload().json()["id"]
    await _add_log(insp_id, error="解析失败")
    await _add_log(insp_id, error=None)

    r = client.delete("/api/ai/history/failed/all")
    assert r.status_code == 200
    assert r.json()["count"] == 1

    data = client.get("/api/ai/history").json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "success"


async def test_delete_failed_logs_preserves_quality_check(client, upload):
    """删除失败日志只清标签分析，保留质量审核（quality_check）失败日志。"""
    from sqlalchemy import func, select

    insp_id = upload().json()["id"]
    await _add_log(insp_id, error="无法连接 Ollama")  # 标签分析失败
    async with async_session() as db:
        db.add(
            AIAnalysisLog(
                inspiration_id=insp_id,
                model_name="qwen3-vl:8b-instruct",
                log_type="quality_check",
                error="审核失败",
            )
        )
        await db.commit()

    r = client.delete("/api/ai/history/failed/all")
    assert r.json()["count"] == 1  # 只删标签分析失败日志

    async with async_session() as db:
        qc = (
            await db.execute(
                select(func.count())
                .select_from(AIAnalysisLog)
                .where(AIAnalysisLog.log_type == "quality_check")
            )
        ).scalar()
    assert qc == 1  # 质量审核日志保留


async def test_analysis_status_filter_uses_latest_log(client, upload):
    """analysis_status 筛选基于最新日志：历史失败但最新成功的素材不属 error。"""
    insp_id = upload().json()["id"]
    await _add_log(insp_id, error="无法连接 Ollama")  # 历史失败
    await _add_log(insp_id, error=None)  # 最新成功

    err = client.get("/api/inspirations", params={"analysis_status": "error"}).json()
    assert err["total"] == 0
    done = client.get("/api/inspirations", params={"analysis_status": "done"}).json()
    assert done["total"] == 1
    assert done["items"][0]["id"] == insp_id


async def test_batch_delete_analysis_failed_uses_latest(client, upload):
    """「删除分析失败素材」按最新日志判定：最新已成功的素材不被误删。"""
    insp_id = upload().json()["id"]
    await _add_log(insp_id, error="无法连接 Ollama")  # 历史失败
    await _add_log(insp_id, error=None)  # 最新成功

    r = client.post("/api/admin/batch-delete", json={"condition": "analysis_failed"})
    assert r.status_code == 200
    assert r.json()["deleted_count"] == 0  # 最新成功，不应被选中


def test_queue_pause_resume(client):
    """分析队列暂停/恢复。"""
    assert client.post("/api/ai/queue/pause").json()["paused"] is True
    assert client.post("/api/ai/queue/resume").json()["paused"] is False


def test_pending_queue_empty(client):
    """空队列：排队预览返回空列表。"""
    data = client.get("/api/ai/queue/pending").json()
    assert data["items"] == []


async def test_quality_dashboard(client, upload):
    """分析质量仪表盘：概览覆盖率 + 每日趋势。"""
    insp_id = upload().json()["id"]
    await _add_log(insp_id, error=None, raw='{"style": ["法式"]}')

    data = client.get("/api/ai/quality-dashboard").json()
    assert data["overview"]["total_inspirations"] == 1
    assert data["overview"]["analyzed_count"] == 1
    assert data["overview"]["coverage_percent"] == 100.0
    assert len(data["daily_trends"]) >= 1


def test_parse_iso_dt_converts_timezone_to_utc():
    """带时区的时间应换算到 UTC 再剥离时区，避免筛选窗口偏移。"""
    from app.services.ai_analysis_service import _parse_iso_dt

    # 东八区 23:59 → UTC 15:59
    assert _parse_iso_dt("2026-01-01T23:59:00+08:00").hour == 15
    # Z 后缀（UTC）→ 原样
    assert _parse_iso_dt("2026-01-01T23:59:00Z").hour == 23
    # 无时区 → 原样
    assert _parse_iso_dt("2026-01-01T23:59:00").hour == 23
