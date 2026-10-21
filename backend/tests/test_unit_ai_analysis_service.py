"""分析历史服务的测试（队列统计 / 未分析口径 / 历史分页 / 日志删除）。

这些接口是「AI 分析」页面的数据源，最容易出的两类问题：

1. **口径不一致**：队列统计的「未分析」与批量任务的「已分析跳过」若口径不同，
   按钮会显示「还有 N 张未分析」但点了没有任何反应（或反过来误禁用）。
   这里的规则是「没有任何成功日志 = 未分析（含失败过的）」。
2. **把质量审核日志混进标签分析历史**：两类日志同表（`ai_analysis_log`），
   靠 `_analysis_log_filter()` 区分；漏一个统计就会虚高。
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.services import ai_analysis_service as svc


async def _insp(
    insp_id: str,
    *,
    media_type: str = "image",
    deleted: bool = False,
    created_at: datetime | None = None,
) -> str:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg" if media_type == "image" else f"videos/{insp_id}.mp4",
                media_type=media_type,
                quality_status="approved",
                deleted_at=datetime.now(timezone.utc) if deleted else None,
                created_at=created_at or datetime.now(timezone.utc),
            )
        )
        await db.commit()
    return insp_id


async def _log(
    insp_id: str,
    *,
    error: str | None = None,
    model: str = "qwen3-vl:8b-instruct",
    prompt_hash: str | None = "hash-a",
    log_type: str | None = None,
) -> int:
    async with async_session() as db:
        log = AIAnalysisLog(
            inspiration_id=insp_id,
            model_name=model,
            prompt_version=prompt_hash,
            error=error,
            raw_response='{"features": ["针织开衫"]}' if error is None else None,
            log_type=log_type or "analysis",
        )
        db.add(log)
        await db.commit()
        return log.id


# ── 队列统计 ──


async def test_queue_stats_counts_and_unanalyzed_rule(client):
    """未分析 = 没有成功日志（含失败过的素材），失败数只看每条素材的最新日志。"""
    await _insp("q1")
    await _insp("q2")
    await _insp("q3")
    await _log("q1")                      # 成功
    await _log("q2", error="超时")         # 失败（最新仍是失败）

    async with async_session() as db:
        stats = await svc.get_analysis_queue_stats(db)

    assert stats["total"] == 3
    assert stats["analyzed"] == 2   # 有过日志（attempted）
    assert stats["failed"] == 1     # 最新日志是失败
    assert stats["unanalyzed"] == 2  # q2（只有失败日志）+ q3（没日志）


async def test_queue_stats_failed_only_counts_latest(client):
    """先失败后成功：不算失败（否则「失败数」永远降不下来）。"""
    await _insp("f1")
    await _log("f1", error="第一次超时")
    await _log("f1")  # 重试成功

    async with async_session() as db:
        stats = await svc.get_analysis_queue_stats(db)

    assert stats["failed"] == 0
    assert stats["unanalyzed"] == 0


async def test_queue_stats_excludes_trash_and_excludes_quality_logs(client):
    """垃圾桶素材与质量审核日志都不进标签分析统计。"""
    await _insp("t1")
    await _log("t1")
    await _insp("t2", deleted=True)
    await _log("t2")
    await _insp("t3")
    await _log("t3", log_type="quality_check")  # 质量审核日志不算分析

    async with async_session() as db:
        stats = await svc.get_analysis_queue_stats(db)

    assert stats["total"] == 2      # t2 在垃圾桶
    assert stats["analyzed"] == 1   # 只有 t1 是标签分析
    assert stats["unanalyzed"] == 1  # t3 没有标签分析日志


# ── 未分析列表 ──


async def test_unanalyzed_ids_exclude_inflight(client, monkeypatch):
    """内存队列里的素材（正在分析）不能被再次列出，否则重复烧 GPU。"""
    await _insp("u1")
    await _insp("u2")

    async def _inflight(db):
        return {"u1"}

    monkeypatch.setattr(svc, "get_inflight_analysis_ids", _inflight)

    async with async_session() as db:
        ids = await svc.get_unanalyzed_ids(db)

    assert "u1" not in ids
    assert "u2" in ids


async def test_unanalyzed_ids_honours_skip_ids(client, monkeypatch):
    await _insp("s1")
    await _insp("s2")

    async def _none(db):
        return set()

    monkeypatch.setattr(svc, "get_inflight_analysis_ids", _none)

    async with async_session() as db:
        ids = await svc.get_unanalyzed_ids(db, skip_ids={"s1"})

    assert ids == ["s2"]


async def test_unanalyzed_ids_includes_video_and_skips_trash(client, monkeypatch):
    await _insp("v1", media_type="video")
    await _insp("v2", deleted=True)

    async def _none(db):
        return set()

    monkeypatch.setattr(svc, "get_inflight_analysis_ids", _none)

    async with async_session() as db:
        ids = await svc.get_unanalyzed_ids(db)

    assert "v1" in ids   # 视频也要参与分析（经首帧）
    assert "v2" not in ids


# ── 时间解析 ──


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-10-21T12:00:00", datetime(2026, 10, 21, 12)),
        ("2026-10-21T12:00:00Z", datetime(2026, 10, 21, 12)),
        # 东八区 20:00 = UTC 12:00：必须换算后剥离时区，否则筛选窗口偏移 8 小时
        ("2026-10-21T20:00:00+08:00", datetime(2026, 10, 21, 12)),
    ],
)
def test_parse_iso_dt_normalizes_to_utc_naive(text, expected):
    assert svc._parse_iso_dt(text) == expected


def test_parse_iso_dt_strips_whitespace():
    assert svc._parse_iso_dt("  2026-10-21T00:00:00Z ") == datetime(2026, 10, 21)


# ── 历史查询 ──


async def test_history_pagination_and_filters(client):
    await _insp("h1")
    await _insp("h2")
    await _log("h1", model="model-a")
    await _log("h2", model="model-b", error="失败")

    async with async_session() as db:
        page = await svc.get_analysis_history(
            db, page=1, size=1, status=None, model_name=None, inspiration_id=None
        )
        failed = await svc.get_analysis_history(
            db, page=1, size=10, status="error", model_name=None, inspiration_id=None
        )
        by_model = await svc.get_analysis_history(
            db, page=1, size=10, status=None, model_name="model-a", inspiration_id=None
        )

    assert page["total"] >= 2
    assert len(page["items"]) == 1  # size 生效
    assert failed["total"] >= 1
    assert all(item["error"] for item in failed["items"])
    assert [item["model_name"] for item in by_model["items"]] == ["model-a"]


async def test_history_filters_by_inspiration_and_date_range(client):
    await _insp("hd1")
    await _log("hd1")

    async with async_session() as db:
        by_insp = await svc.get_analysis_history(
            db, page=1, size=10, status=None, model_name=None, inspiration_id="hd1"
        )
        out_of_range = await svc.get_analysis_history(
            db,
            page=1,
            size=10,
            status=None,
            model_name=None,
            inspiration_id="hd1",
            start_date="2030-01-01T00:00:00",
        )

    assert by_insp["total"] == 1
    assert out_of_range["total"] == 0


async def test_model_names_and_prompt_versions(client):
    await _insp("m1")
    await _log("m1", model="model-b")
    await _log("m1", model="model-a")

    async with async_session() as db:
        names = await svc.get_history_model_names(db)
        versions = await svc.get_history_prompt_versions(db)

    assert names == sorted(set(names))
    assert "model-a" in names
    assert isinstance(versions, list)


# ── 删除类 ──


async def test_delete_logs_batch(client):
    await _insp("d1")
    first = await _log("d1")
    second = await _log("d1")

    async with async_session() as db:
        deleted = await svc.delete_analysis_logs_batch(db, [first, second])

    assert deleted == 2


async def test_delete_single_log(client):
    await _insp("d2")
    log_id = await _log("d2")

    async with async_session() as db:
        assert await svc.delete_analysis_log(db, log_id) is True
        assert await svc.delete_analysis_log(db, log_id) is False


async def test_delete_failed_logs_only_failed(client):
    await _insp("d3")
    await _log("d3")
    await _log("d3", error="失败")

    async with async_session() as db:
        deleted = await svc.delete_failed_logs(db)

    assert deleted == 1
    async with async_session() as db:
        remaining = await svc.get_analysis_history(
            db, page=1, size=10, status=None, model_name=None, inspiration_id="d3"
        )
    assert remaining["total"] == 1  # 成功那条还在


async def test_batch_retry_targets_returns_path_only_for_images(client):
    """视频重试返回路径 None（由执行器懒解析首帧），图片给真实路径。"""
    await _insp("r1")
    await _insp("r2", media_type="video")
    l1 = await _log("r1", error="失败")
    l2 = await _log("r2", error="失败")

    async with async_session() as db:
        targets = await svc.get_batch_retry_targets(db, [l1, l2])

    mapping = dict(targets)
    assert mapping["r1"] == "images/x/r1.jpg"
    assert mapping["r2"] is None


async def test_batch_retry_targets_skips_trash(client):
    await _insp("r3", deleted=True)
    log_id = await _log("r3", error="失败")

    async with async_session() as db:
        assert await svc.get_batch_retry_targets(db, [log_id]) == []


async def test_failed_analysis_targets(client):
    await _insp("t1")
    await _log("t1", error="失败一")
    await _insp("t2")
    await _log("t2")

    async with async_session() as db:
        targets = await svc.get_failed_analysis_targets(db)

    ids = [t[0] for t in targets]
    assert "t1" in ids
    assert "t2" not in ids


async def test_detail_returns_none_for_unknown(client):
    async with async_session() as db:
        assert await svc.get_analysis_detail(db, 999999) is None


async def test_detail_includes_parsed_and_material(client):
    await _insp("dt1")
    log_id = await _log("dt1")

    async with async_session() as db:
        detail = await svc.get_analysis_detail(db, log_id)

    assert detail["id"] == log_id
    assert detail["inspiration_id"] == "dt1"
    assert detail["status"] == "success"
    assert detail["file_path"] == "images/x/dt1.jpg"
    assert detail["parsed_response"] is not None  # raw_response 会被解析给前端


# ── 多记录对比（多模型 × 多提示词）──


async def _structured_tag(log_id: int, name: str, category: str = "item", conf: float = 0.9) -> None:
    from app.models.inspiration import AIAnalysisTag
    from app.models.tag import Tag

    async with async_session() as db:
        tag = (await db.execute(select(Tag).where(Tag.name == name))).scalar_one_or_none()
        if tag is None:
            tag = Tag(name=name, category=category, source="ai_generated")
            db.add(tag)
            await db.flush()
        db.add(AIAnalysisTag(log_id=log_id, tag_id=tag.id, confidence=conf))
        await db.commit()


async def test_compare_requires_at_least_two(client):
    await _insp("cmp0")
    log_id = await _log("cmp0")

    async with async_session() as db:
        with pytest.raises(ValueError):
            await svc.compare_logs_batch(db, [])
        with pytest.raises(ValueError):
            await svc.compare_logs_batch(db, [log_id])


async def test_compare_rejects_cross_material(client):
    """跨素材对比没有意义（连构图都不同），必须拒绝而不是给出误导性差异。"""
    await _insp("cmp1")
    await _insp("cmp2")
    l1 = await _log("cmp1")
    l2 = await _log("cmp2")

    async with async_session() as db:
        with pytest.raises(ValueError) as exc:
            await svc.compare_logs_batch(db, [l1, l2])
    assert "同一素材" in str(exc.value)


async def test_compare_raises_when_log_missing(client):
    from app.services.ai_analysis_service import AIAnalysisNotFoundError

    await _insp("cmp3")
    log_id = await _log("cmp3")

    async with async_session() as db:
        with pytest.raises(AIAnalysisNotFoundError):
            await svc.compare_logs_batch(db, [log_id, 999999])


async def test_compare_reports_common_and_differing_tags(client):
    """共有标签与差异标签要分开：差异项还要标明出现在哪几条记录（供高亮）。"""
    await _insp("cmp4")
    l1 = await _log("cmp4", model="model-a")
    l2 = await _log("cmp4", model="model-b")
    await _structured_tag(l1, "针织开衫")
    await _structured_tag(l1, "米色")
    await _structured_tag(l2, "针织开衫")
    await _structured_tag(l2, "黑色")

    async with async_session() as db:
        result = await svc.compare_logs_batch(db, [l1, l2])

    assert result["inspiration_id"] == "cmp4"
    assert result["analyses_count"] == 2
    assert result["tag_diff"]["common"] == ["针织开衫"]
    differing = {d["name"]: d["log_ids"] for d in result["tag_diff"]["differing"]}
    assert differing["米色"] == [l1]
    assert differing["黑色"] == [l2]
    # 并排顺序与传入一致
    assert [a["id"] for a in result["analyses"]] == [l1, l2]
    assert len(result["time_comparison"]) == 2


async def test_compare_with_failed_log_has_no_tag_diff(client):
    """有一条失败时不算差异（成功记录不足 2 条 → tag_diff 为 None）。"""
    await _insp("cmp5")
    l1 = await _log("cmp5")
    l2 = await _log("cmp5", error="失败")
    await _structured_tag(l1, "针织开衫")

    async with async_session() as db:
        result = await svc.compare_logs_batch(db, [l1, l2])

    assert result["tag_diff"] is None
    assert {a["status"] for a in result["analyses"]} == {"success", "error"}


# ── 把历史标签应用到素材 ──


async def test_apply_analysis_overwrites_ai_tags_keeps_manual(client):
    """应用 = 覆盖 AI 标签、保留手动标签（手动是人的判断，不能被模型结果冲掉）。"""
    from app.models.tag import InspirationTag, Tag

    await _insp("ap1")
    log_id = await _log("ap1")
    await _structured_tag(log_id, "旧AI标签")
    await _structured_tag(log_id, "针织开衫")

    async with async_session() as db:
        manual = Tag(name="人工标签", category="item", source="manual")
        db.add(manual)
        await db.flush()
        db.add(
            InspirationTag(
                inspiration_id="ap1", tag_id=manual.id, confidence=1.0, source="manual"
            )
        )
        stale = Tag(name="陈旧AI标签", category="item", source="ai_generated")
        db.add(stale)
        await db.flush()
        db.add(
            InspirationTag(
                inspiration_id="ap1", tag_id=stale.id, confidence=0.9, source="ai_generated"
            )
        )
        await db.commit()

    async with async_session() as db:
        result = await svc.apply_analysis_to_material(db, log_id)

    assert result["applied"] == 2
    async with async_session() as db:
        rows = (
            await db.execute(
                select(InspirationTag.source, Tag.name)
                .join(Tag, Tag.id == InspirationTag.tag_id)
                .where(InspirationTag.inspiration_id == "ap1")
            )
        ).all()
    names = {name for _src, name in rows}
    assert "人工标签" in names          # 手动保留
    assert "针织开衫" in names          # 快照写入
    assert "陈旧AI标签" not in names     # 旧 AI 标签被清掉


async def test_apply_analysis_rejects_failed_log(client):
    await _insp("ap2")
    log_id = await _log("ap2", error="失败")

    async with async_session() as db:
        with pytest.raises(ValueError) as exc:
            await svc.apply_analysis_to_material(db, log_id)
    assert "失败" in str(exc.value)


async def test_apply_analysis_rejects_quality_log(client):
    await _insp("ap3")
    log_id = await _log("ap3", log_type="quality_check")

    async with async_session() as db:
        with pytest.raises(ValueError) as exc:
            await svc.apply_analysis_to_material(db, log_id)
    assert "标签分析" in str(exc.value)


async def test_apply_analysis_rejects_trashed_material(client):
    await _insp("ap4", deleted=True)
    log_id = await _log("ap4")
    await _structured_tag(log_id, "标签X")

    async with async_session() as db:
        with pytest.raises(ValueError) as exc:
            await svc.apply_analysis_to_material(db, log_id)
    assert "垃圾桶" in str(exc.value)


async def test_apply_analysis_rejects_empty_tags(client):
    await _insp("ap5")
    async with async_session() as db:
        log = AIAnalysisLog(
            inspiration_id="ap5", model_name="m", log_type="analysis", raw_response=None
        )
        db.add(log)
        await db.commit()
        log_id = log.id

    async with async_session() as db:
        with pytest.raises(ValueError) as exc:
            await svc.apply_analysis_to_material(db, log_id)
    assert "没有提取到" in str(exc.value)


async def test_apply_analysis_missing_log(client):
    from app.services.ai_analysis_service import AIAnalysisNotFoundError

    async with async_session() as db:
        with pytest.raises(AIAnalysisNotFoundError):
            await svc.apply_analysis_to_material(db, 999999)


async def test_preview_map_batch(client):
    await _insp("pm1")
    async with async_session() as db:
        mapping = await svc.get_inspiration_preview_map(db, ["pm1", "不存在"])
    assert mapping["pm1"]["file_path"] == "images/x/pm1.jpg"
    assert "不存在" not in mapping
    async with async_session() as db:
        assert await svc.get_inspiration_preview_map(db, []) == {}
