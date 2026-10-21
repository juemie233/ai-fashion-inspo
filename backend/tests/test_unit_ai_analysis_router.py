"""AI 分析路由的测试（单张 / 批量 / 组合 / 重试 / 历史导出 / 按标签重打）。

**为什么直调 handler**：本项目 TestClient 的请求跑在 anyio portal 线程，不被 coverage
追踪；HTTP 测 router 永远只有「模块级 def 被覆盖」。直接 `await handler(...)` 才是真覆盖。

重点覆盖「不重复调度」与「离线不阻断入队」两条主链路：
- 同一素材被多个入口（单张 / 批量 / 一键重试）反复提交时，必须有去重口径，
  否则同一张图会被并发分析多次、白烧 GPU、日志也互相覆盖；
- Ollama 离线属**可恢复**错误：任务照常入队、由 worker 退避重试，
  不能像早期那样直接拒绝（用户点了按钮却什么都没发生）。
"""

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.models.task import TaskQueue
from app.routers import ai_analysis as router
from app.schemas.ai_analysis import BatchAnalyzeRequest


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    """默认：无进行中素材、Ollama 在线、任务创建走替身（不真正建 worker 任务）。"""

    async def _no_inflight(db):
        return set()

    monkeypatch.setattr(router.ai_svc, "get_inflight_analysis_ids", _no_inflight)
    monkeypatch.setattr(router, "get_in_memory_queue_ids", lambda: set())

    async def _ollama_ok():
        return True, None

    monkeypatch.setattr(router, "_check_ollama_before_analysis", _ollama_ok)

    class _Task:
        id = 777

    async def _create_batch(db, ids, *args, **kwargs):
        return _Task()

    async def _create_multi(db, ids, combos, *args, **kwargs):
        return _Task()

    monkeypatch.setattr(router, "create_batch_analyze_task", _create_batch)
    monkeypatch.setattr(router, "create_multi_analyze_task", _create_multi)

    # 单张分析会 create_task 跑真实分析：替换成空操作
    async def _noop_run(insp_id, file_path=None):
        return None

    monkeypatch.setattr(router, "_run_analysis", _noop_run)


async def _insp(
    insp_id: str,
    *,
    media_type: str = "image",
    deleted: bool = False,
    file_path: str | None = None,
) -> str:
    from datetime import datetime, timezone

    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=file_path or f"images/x/{insp_id}.jpg",
                media_type=media_type,
                quality_status="approved",
                deleted_at=datetime.now(timezone.utc) if deleted else None,
            )
        )
        await db.commit()
    return insp_id


# ── 单张分析 ──


async def test_analyze_single_queues_background_task(client):
    await _insp("a1")
    async with async_session() as db:
        result = await router.analyze_inspiration("a1", db=db)

    assert result["status"] == "analyzing"
    assert result["ollama_will_start"] is False


async def test_analyze_single_404_and_400(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.analyze_inspiration("不存在", db=db)
        assert exc.value.status_code == 404

        await _insp("a2", media_type="document")
        with pytest.raises(HTTPException) as exc:
            await router.analyze_inspiration("a2", db=db)
        assert exc.value.status_code == 400


async def test_analyze_single_skips_inflight(client, monkeypatch):
    """已在队列中的素材不重复调度（否则同一张图会被分析两次）。"""
    await _insp("a3")
    monkeypatch.setattr(router, "get_in_memory_queue_ids", lambda: {"a3"})

    async with async_session() as db:
        result = await router.analyze_inspiration("a3", db=db)

    assert "已在分析队列中" in result["message"]


async def test_analyze_single_when_ollama_offline(client, monkeypatch):
    """Ollama 离线也照常入队（可恢复错误），但要提示用户会自动重试。"""
    await _insp("a4")

    async def _offline():
        return False, "Ollama 未启动，已尝试自动启动"

    monkeypatch.setattr(router, "_check_ollama_before_analysis", _offline)

    async with async_session() as db:
        result = await router.analyze_inspiration("a4", db=db)

    assert result["ollama_will_start"] is True
    assert "Ollama 未启动" in result["message"]


async def test_analyze_video_passes_none_file_path(client, monkeypatch):
    """视频不传路径：由后台任务懒解析首帧（ffmpeg 提取耗时不阻塞请求）。"""
    await _insp("a5", media_type="video")
    seen: list[tuple] = []

    async def _capture(insp_id, file_path=None):
        seen.append((insp_id, file_path))

    monkeypatch.setattr(router, "_run_analysis", _capture)

    async with async_session() as db:
        await router.analyze_inspiration("a5", db=db)
        import asyncio

        await asyncio.sleep(0)  # 让 create_task 跑起来

    assert seen == [("a5", None)]


# ── 批量 / 组合 ──


async def test_batch_analyze_legacy_list_format(client):
    await _insp("b1")
    await _insp("b2")

    async with async_session() as db:
        result = await router.batch_analyze(["b1", "b2"], db=db)

    assert result["count"] == 2
    assert result["task_id"] == 777
    assert "批量分析任务" in result["message"]


async def test_batch_analyze_filters_invalid_and_reports_skipped(client):
    await _insp("b3")
    await _insp("b4", deleted=True)   # 垃圾桶
    await _insp("b5", media_type="document")

    async with async_session() as db:
        result = await router.batch_analyze(["b3", "b4", "b5", "不存在"], db=db)

    assert result["count"] == 1
    assert result["skipped"] == 3


async def test_batch_analyze_rejects_when_all_inflight(client, monkeypatch):
    await _insp("b6")

    async def _all_inflight(db):
        return {"b6"}

    monkeypatch.setattr(router.ai_svc, "get_inflight_analysis_ids", _all_inflight)

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.batch_analyze(["b6"], db=db)

    assert exc.value.status_code == 400
    assert "均已在分析队列中" in str(exc.value.detail)


async def test_batch_analyze_404_when_nothing_valid(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.batch_analyze(["不存在"], db=db)
    assert exc.value.status_code == 404


async def test_batch_analyze_multi_model_combinations(client):
    """多模型 × 多提示词组合：任务里要带上组合数与 apply_tags 语义。"""
    await _insp("b7")

    async with async_session() as db:
        payload = BatchAnalyzeRequest(
            inspiration_ids=["b7"],
            models=["model-a", "model-b", "model-b"],  # 重复模型要能被去重
            prompt_ids=[0],                            # 0 = 当前默认提示词
            apply_tags=False,
        )
        result = await router.batch_analyze(payload, db=db)

    # 2 个模型（去重后）× 1 个提示词 = 2
    assert result["combinations"] == 2
    assert result["apply_tags"] is False
    assert "组合分析任务" in result["message"]


async def test_batch_analyze_rejects_too_many_combinations(client, monkeypatch):
    await _insp("b8")
    monkeypatch.setattr(router, "MAX_MULTI_COMBINATIONS", 1)

    async with async_session() as db:
        payload = BatchAnalyzeRequest(
            inspiration_ids=["b8"], models=["m1", "m2"], prompt_ids=[0]
        )
        with pytest.raises(HTTPException) as exc:
            await router.batch_analyze(payload, db=db)

    assert exc.value.status_code == 400
    assert "组合数过多" in str(exc.value.detail)


async def test_batch_analyze_rejects_invalid_prompt_version(client):
    """无效的提示词版本 ID 要 400（不能让任务跑起来才发现选错了版本）。"""
    await _insp("b9")

    async with async_session() as db:
        payload = BatchAnalyzeRequest(
            inspiration_ids=["b9"], models=["m1"], prompt_ids=[99]
        )
        with pytest.raises(HTTPException) as exc:
            await router.batch_analyze(payload, db=db)

    assert exc.value.status_code == 400
    assert "无效的提示词版本" in str(exc.value.detail)


def test_resolve_prompt_combinations_defaults():
    """不给模型/提示词时退化成「当前模型 × 当前提示词」一组，而不是空组合。"""
    combos = router._resolve_prompt_combinations(None, None)
    assert len(combos) == 1


# ── 队列 / 未分析 / 历史 ──


async def test_queue_and_unanalyzed_endpoints(client):
    await _insp("q1")
    async with async_session() as db:
        queue = await router.analysis_queue(db=db)
        unanalyzed = await router.unanalyzed_ids(db=db)

    assert set(queue) >= {"total", "analyzed", "unanalyzed", "failed"}
    assert "q1" in unanalyzed["ids"]
    assert unanalyzed["count"] == len(unanalyzed["ids"])


async def test_history_endpoint_and_bad_date(client):
    await _insp("h1")
    async with async_session() as db:
        data = await router.analysis_history(
            page=1,
            size=20,
            status=None,
            model_name=None,
            prompt_version=None,
            inspiration_id="h1",
            start_date=None,
            end_date=None,
            sort_by=None,
            db=db,
        )
        assert "items" in data and "total" in data

        with pytest.raises(HTTPException) as exc:
            await router.analysis_history(
                page=1,
                size=20,
                status=None,
                model_name=None,
                prompt_version=None,
                inspiration_id=None,
                start_date="不是日期",
                end_date=None,
                sort_by=None,
                db=db,
            )
        assert exc.value.status_code == 400
        assert "时间参数" in str(exc.value.detail)


async def test_history_model_names_and_prompt_versions(client):
    await _insp("h2")
    async with async_session() as db:
        db.add(AIAnalysisLog(inspiration_id="h2", model_name="m-x", log_type="analysis"))
        await db.commit()

        names = await router.get_history_model_names(db=db)
        versions = await router.get_history_prompt_versions(db=db)

    assert "m-x" in names["models"]
    assert "versions" in versions


async def test_history_export_csv_returns_streaming_response(client):
    await _insp("h3")
    async with async_session() as db:
        log = AIAnalysisLog(
            inspiration_id="h3",
            model_name="m-y",
            log_type="analysis",
            raw_response='{"features": ["针织开衫"]}',
            error=None,
        )
        db.add(log)
        await db.flush()
        # CSV 的标签列取「结构化快照」，不是实时解析 raw_response
        from app.models.inspiration import AIAnalysisTag
        from app.models.tag import Tag

        tag = Tag(name="针织开衫", category="item", source="ai_generated")
        db.add(tag)
        await db.flush()
        db.add(AIAnalysisTag(log_id=log.id, tag_id=tag.id, confidence=0.9))
        await db.commit()

    async with async_session() as db:
        response = await router.export_analysis_history_csv(
            status=None,
            model_name=None,
            inspiration_id=None,
            start_date=None,
            end_date=None,
            prompt_version=None,
            db=db,
        )

    text = response.body.decode("utf-8-sig")  # 同步 Response，直接拿 content
    assert "h3" in text
    assert "模型" in text  # 表头（中文列名）
    assert "针织开衫" in text  # 标签列拼进 CSV


# ── 重试 ──


async def test_retry_single_queues_task(client):
    await _insp("r1")
    async with async_session() as db:
        result = await router.retry_analysis("r1", db=db)

    assert result["task_id"] == 777
    assert "已重新加入分析队列" in result["message"]


async def test_retry_single_404_and_400(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.retry_analysis("不存在", db=db)
        assert exc.value.status_code == 404

        await _insp("r2", media_type="document")
        with pytest.raises(HTTPException) as exc:
            await router.retry_analysis("r2", db=db)
        assert exc.value.status_code == 400


async def test_retry_single_when_inflight(client, monkeypatch):
    await _insp("r3")

    async def _exclude(db, ids):
        return [], {"r3"}

    monkeypatch.setattr(router, "_exclude_inflight_ids", _exclude)

    async with async_session() as db:
        result = await router.retry_analysis("r3", db=db)

    assert result["task_id"] is None
    assert "已在分析队列中" in result["message"]


async def test_retry_all_failed_creates_task(client):
    await _insp("rf1")
    async with async_session() as db:
        db.add(
            AIAnalysisLog(
                inspiration_id="rf1", model_name="m", log_type="analysis", error="超时"
            )
        )
        await db.commit()

    async with async_session() as db:
        result = await router.retry_all_failed(db=db)

    assert result["count"] >= 1
    assert result["task_id"] == 777


async def test_retry_all_failed_when_none(client):
    async with async_session() as db:
        result = await router.retry_all_failed(db=db)
    assert result["count"] == 0
    assert "没有失败" in result["message"]


async def test_retry_all_failed_when_all_inflight(client, monkeypatch):
    await _insp("rf2")
    async with async_session() as db:
        db.add(
            AIAnalysisLog(
                inspiration_id="rf2", model_name="m", log_type="analysis", error="超时"
            )
        )
        await db.commit()

    async def _exclude(db, ids):
        return [], set(ids)

    monkeypatch.setattr(router, "_exclude_inflight_ids", _exclude)

    async with async_session() as db:
        result = await router.retry_all_failed(db=db)

    assert result["count"] == 0
    assert "均已在分析队列中" in result["message"]


# ── 历史批量操作 ──


async def test_batch_delete_logs(client):
    await _insp("bd1")
    async with async_session() as db:
        log = AIAnalysisLog(inspiration_id="bd1", model_name="m", log_type="analysis")
        db.add(log)
        await db.commit()
        log_id = log.id

    async with async_session() as db:
        result = await router.batch_delete_logs({"ids": [log_id]}, db=db)
        assert result["deleted"] == 1

        with pytest.raises(HTTPException) as exc:
            await router.batch_delete_logs({"ids": []}, db=db)
        assert exc.value.status_code == 400


async def test_batch_retry_logs(client):
    await _insp("br1")
    async with async_session() as db:
        log = AIAnalysisLog(
            inspiration_id="br1", model_name="m", log_type="analysis", error="超时"
        )
        db.add(log)
        await db.commit()
        log_id = log.id

    async with async_session() as db:
        result = await router.batch_retry_logs({"ids": [log_id]}, db=db)
        assert result["count"] == 1

        empty = await router.batch_retry_logs({"ids": [999999]}, db=db)
        assert empty["count"] == 0

        with pytest.raises(HTTPException) as exc:
            await router.batch_retry_logs({"ids": "不是列表"}, db=db)
        assert exc.value.status_code == 400


# ── 按标签重打（标签高级管理入口）──


async def _link_tag(insp_id: str, tag_name: str, *, source: str = "ai_generated") -> int:
    from app.models.tag import InspirationTag, Tag

    async with async_session() as db:
        tag = (
            await db.execute(__import__("sqlalchemy").select(Tag).where(Tag.name == tag_name))
        ).scalar_one_or_none()
        if tag is None:
            tag = Tag(name=tag_name, category="style", source="seed")
            db.add(tag)
            await db.flush()
        db.add(
            InspirationTag(
                inspiration_id=insp_id, tag_id=tag.id, confidence=0.9, source=source
            )
        )
        await db.commit()
        return tag.id


async def test_retag_only_touches_ai_tagged_material(client):
    """只重跑 AI 打过这个标签的素材：手动打标不受影响（重跑会清 AI 标签）。"""
    t1 = await _insp("rt1")
    t2 = await _insp("rt2")
    tag_id = await _link_tag("rt1", "学院风", source="ai_generated")
    await _link_tag("rt2", "学院风", source="manual")

    async with async_session() as db:
        result = await router.retag_analysis_by_tag(tag_id, exclude_tag_id=None, db=db)

    assert result["count"] == 1
    assert result["task_id"] == 777
    assert t1 != t2


async def test_retag_excludes_tagged_material(client):
    """带排除标签的素材不纳入（「有学院风但没有 JK 制服」这种组合条件）。"""
    await _insp("rt3")
    await _insp("rt4")
    tag_id = await _link_tag("rt3", "学院风")
    await _link_tag("rt4", "学院风")
    exclude_id = await _link_tag("rt4", "JK制服")

    async with async_session() as db:
        result = await router.retag_analysis_by_tag(tag_id, exclude_tag_id=exclude_id, db=db)

    assert result["count"] == 1


async def test_retag_returns_none_when_no_material(client):
    await _insp("rt5")
    # 手动来源的关联不算「AI 打过」，因此没有可重跑的素材
    tag_id = await _link_tag("rt5", "无人标签", source="manual")

    async with async_session() as db:
        result = await router.retag_analysis_by_tag(tag_id, exclude_tag_id=None, db=db)

    assert result["task_id"] is None
    assert result["status"] == "none"
    assert "没有由 AI 打标" in result["message"]


async def test_retag_404_for_unknown_tags(client):
    await _insp("rt6")
    tag_id = await _link_tag("rt6", "已知标签")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.retag_analysis_by_tag(999999, exclude_tag_id=None, db=db)
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            await router.retag_analysis_by_tag(tag_id, exclude_tag_id=999999, db=db)
        assert exc.value.status_code == 404


async def test_retag_when_all_inflight(client, monkeypatch):
    await _insp("rt7")
    tag_id = await _link_tag("rt7", "在跑标签")

    async def _exclude(db, ids):
        return [], set(ids)

    monkeypatch.setattr(router, "_exclude_inflight_ids", _exclude)

    async with async_session() as db:
        result = await router.retag_analysis_by_tag(tag_id, exclude_tag_id=None, db=db)

    assert result["task_id"] is None
    assert "均已在分析队列中" in result["message"]


async def test_prompt_options_shape(client):
    data = await router.get_prompt_options()
    assert "options" in data
    assert data["options"][0]["id"] == 0  # 0 = 当前默认提示词
