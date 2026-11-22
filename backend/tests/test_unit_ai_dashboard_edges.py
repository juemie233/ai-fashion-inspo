"""分析质量仪表盘服务（ai_dashboard_service）的边界与聚合单测。

为什么直接调服务：TestClient 的请求跑在 anyio portal 线程，coverage 追踪不到，
且本模块的聚合口径（每日趋势成功判定、覆盖率、问题素材阈值、错误归类、
提示词版本 × 模型的质量对比、裸词率重放）需要按返回值精确断言。

覆盖：_daily_trends / _overview / _problem_items / _model_comparison /
_classify_error / _error_distribution / _failed_items / collect_quality_dashboard /
_prompt_hash / _version_labels（当前提示词 + 历史版本文件 + 坏数据）/
_collect_raw_names / _bare_word_rate / collect_prompt_quality（全字段与裸词率）。
"""

import hashlib
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import AIAnalysisLog, AIAnalysisTag, Inspiration
from app.models.tag import InspirationTag, Tag
from app.models.tag_correction import TagCorrection
from app.services import ai_dashboard_service as svc
from app.utils.time import utcnow


# ═══════════════════════════════════════════════════════════════
#  造数助手
# ═══════════════════════════════════════════════════════════════


def _insp(**overrides) -> Inspiration:
    data = {
        "id": str(uuid.uuid4()),
        "source_type": "manual_upload",
        "file_path": f"images/{uuid.uuid4().hex}.jpg",
        "media_type": "image",
        "quality_status": "pending",
    }
    data.update(overrides)
    return Inspiration(**data)


def _log(inspiration_id: str, **overrides) -> AIAnalysisLog:
    """构造一条标签分析日志（默认模型 m1、成功、无耗时）。"""
    data: dict = {"inspiration_id": inspiration_id, "model_name": "m1"}
    data.update(overrides)
    return AIAnalysisLog(**data)


def _tag(name: str = "白色") -> Tag:
    return Tag(name=name, category="color")


async def _add(*objects):
    async with async_session() as db:
        db.add_all(list(objects))
        await db.commit()
    return objects[0] if len(objects) == 1 else list(objects)


async def _add_tagged(tag: Tag, *inspirations: Inspiration) -> None:
    """建标签并为每张素材建立关联（批量）。"""
    await _add(tag)
    await _add(
        *[InspirationTag(inspiration_id=i.id, tag_id=tag.id) for i in inspirations]
    )


# ═══════════════════════════════════════════════════════════════
#  每日趋势 / 概览
# ═══════════════════════════════════════════════════════════════


async def test_daily_trends_counts_success_and_ignores_quality_logs(client):
    """按自然日聚合总量与成功量；质量审核日志不算标签分析；30 天外排除。"""
    now = utcnow()
    created = [_insp() for _ in range(5)]
    await _add(*created)
    await _add(
        _log(created[0].id, created_at=now - timedelta(days=2)),
        _log(created[1].id, created_at=now - timedelta(days=1)),
        _log(created[2].id, created_at=now - timedelta(days=1), error="boom"),
        _log(created[3].id, created_at=now - timedelta(days=40)),
        _log(
            created[4].id,
            log_type="quality_check",
            created_at=now - timedelta(hours=1),
        ),
    )

    async with async_session() as db:
        rows = await svc._daily_trends(db)

    assert rows == [
        {
            "day": (now - timedelta(days=2)).strftime("%Y-%m-%d"),
            "total": 1,
            "success": 1,
        },
        {
            "day": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
            "total": 2,
            "success": 1,
        },
    ]


async def test_overview_counts_coverage_and_averages(client):
    """覆盖率只算未删除素材；平均标签数分母/分子同口径；平均耗时只算成功日志。"""
    tag = _tag()
    active1 = _insp()
    active2 = _insp()
    active3 = _insp()
    deleted = _insp(
        deleted_at=utcnow(), trash_reason="其他", trash_source="manual"
    )
    await _add(active1, active2, active3, deleted)
    # active1 两张标签；deleted 的标签不计入平均标签数
    tag2 = _tag("JK")
    await _add(tag, tag2)
    await _add(
        InspirationTag(inspiration_id=active1.id, tag_id=tag.id),
        InspirationTag(inspiration_id=active1.id, tag_id=tag2.id),
        InspirationTag(inspiration_id=deleted.id, tag_id=tag.id),
    )
    await _add(
        _log(active1.id, processing_time_ms=100),
        _log(active2.id, error="timeout", processing_time_ms=250),
        _log(deleted.id),  # 已删除素材的日志不计入已分析数
    )

    async with async_session() as db:
        result = await svc._overview(db)

    assert result == {
        "total_inspirations": 3,
        "analyzed_count": 2,
        "unanalyzed_count": 1,
        "coverage_percent": 66.7,
        "avg_tags_per_image": 1.0,  # 2 个标签 / 2 个已分析素材
        "avg_time_ms": 100,  # 失败日志与已删除素材日志不参与
    }


async def test_overview_empty_library(client):
    async with async_session() as db:
        result = await svc._overview(db)

    assert result == {
        "total_inspirations": 0,
        "analyzed_count": 0,
        "unanalyzed_count": 0,
        "coverage_percent": 0,
        "avg_tags_per_image": 0,
        "avg_time_ms": 0,
    }


# ═══════════════════════════════════════════════════════════════
#  问题素材 / 模型对比
# ═══════════════════════════════════════════════════════════════


async def test_problem_items_multi_fail_threshold_and_zero_tags(client):
    """≥3 次失败才算「多次失败」；成功但零标签输出单独计数；垃圾桶排除。"""
    tag = _tag()
    many_fail = _insp()
    two_fail = _insp()
    zero_tag = _insp()
    tagged = _insp()
    deleted = _insp(
        deleted_at=utcnow(), trash_reason="其他", trash_source="manual"
    )
    await _add(many_fail, two_fail, zero_tag, tagged, deleted)
    await _add(tag)
    await _add(InspirationTag(inspiration_id=tagged.id, tag_id=tag.id))
    await _add(
        *[_log(many_fail.id, error=f"e{i}") for i in range(3)],
        *[_log(two_fail.id, error=f"e{i}") for i in range(2)],
        _log(zero_tag.id),
        _log(tagged.id),
        *[_log(deleted.id, error=f"e{i}") for i in range(3)],
        _log(deleted.id),  # 已删除：既不进 multi_fail 也不进 zero_tag
    )

    async with async_session() as db:
        result = await svc._problem_items(db)

    assert result == {"multi_fail_count": 1, "zero_tag_count": 1}


async def test_model_comparison_success_rate_and_order(client):
    """按模型聚合：成功率为 error IS NULL 占比，按调用次数降序；质量审核日志排除。"""
    insp1 = _insp()
    insp2 = _insp()
    await _add(insp1, insp2)
    await _add(
        _log(insp1.id, model_name="m1"),
        _log(insp2.id, model_name="m1"),
        _log(insp1.id, model_name="m1", error="x"),
        _log(insp1.id, model_name="m2", error="y"),
        _log(insp1.id, model_name="m3", log_type="quality_check", error="z"),
    )

    async with async_session() as db:
        rows = await svc._model_comparison(db)

    assert [row["model_name"] for row in rows] == ["m1", "m2"]
    assert rows[0] == {
        "model_name": "m1",
        "total": 3,
        "success": 2,
        "success_rate": 66.7,
    }
    assert rows[1]["success"] == 0 and rows[1]["success_rate"] == 0


# ═══════════════════════════════════════════════════════════════
#  错误归类与分布
# ═══════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "error,expected",
    [
        ("", "未知"),
        ("请求 timeout", "超时"),
        ("连接服务器超时", "超时"),  # 超时优先于「连接」
        ("connection refused", "连接失败"),
        ("无法连接服务器", "连接失败"),
        ("HTTP 502", "HTTP 错误"),
        ("status=500", "HTTP 错误"),
        ("JSON parse error", "解析失败"),
        ("解析失败", "解析失败"),
        ("超过 context 限制", "上下文/截断"),
        ("输出被截断", "上下文/截断"),
        ("unknown boom", "其他"),
    ],
)
def test_classify_error_branches(error, expected):
    """失败原因关键词归类（前端错误分布图直接展示这些分类）。"""
    assert svc._classify_error(error) == expected


async def test_error_distribution_sorted_by_count(client):
    """错误分布按数量降序；error 为 NULL 的日志不参与。"""
    insp = _insp()
    await _add(insp)
    await _add(
        *[_log(insp.id, error="timeout") for _ in range(4)],
        *[_log(insp.id, error="connection refused") for _ in range(3)],
        *[_log(insp.id, error="bad json") for _ in range(2)],
        _log(insp.id, error="weird boom"),
        _log(insp.id),  # 成功日志
    )

    async with async_session() as db:
        rows = await svc._error_distribution(db)

    assert rows == [
        {"category": "超时", "count": 4},
        {"category": "连接失败", "count": 3},
        {"category": "解析失败", "count": 2},
        {"category": "其他", "count": 1},
    ]


# ═══════════════════════════════════════════════════════════════
#  最近失败素材
# ═══════════════════════════════════════════════════════════════


async def test_failed_items_latest_log_per_inspiration(client):
    """每个素材只取最新一条失败日志；垃圾桶排除；按时间倒序；limit 生效。"""
    now = utcnow()
    insp_a = _insp(thumbnail_path="thumbnails/a.jpg")
    insp_b = _insp()
    deleted = _insp(
        deleted_at=now, trash_reason="其他", trash_source="manual"
    )
    await _add(insp_a, insp_b, deleted)
    await _add(
        _log(insp_a.id, error="旧错误", created_at=now - timedelta(minutes=5)),
        _log(insp_a.id, error="新错误", created_at=now - timedelta(minutes=1)),
        _log(insp_b.id, error="b 的错误", created_at=now - timedelta(minutes=2)),
        _log(deleted.id, error="已删除", created_at=now),
    )

    async with async_session() as db:
        rows = await svc._failed_items(db)
        limited = await svc._failed_items(db, limit=1)

    assert [row["inspiration_id"] for row in rows] == [insp_a.id, insp_b.id]
    assert rows[0]["error"] == "新错误"  # 同素材只取最新那条
    assert rows[0]["created_at"].endswith("Z")
    assert rows[1]["thumbnail_path"] is None
    assert len(limited) == 1 and limited[0]["error"] == "新错误"


async def test_collect_quality_dashboard_shape(client):
    """总览接口把六个维度组装齐全（键缺失前端会整块空白）。"""
    insp = _insp()
    await _add(insp, _log(insp.id, error="timeout"))

    async with async_session() as db:
        result = await svc.collect_quality_dashboard(db)

    assert set(result) == {
        "daily_trends",
        "overview",
        "problem_items",
        "model_comparison",
        "error_distribution",
        "failed_items",
    }
    assert isinstance(result["daily_trends"], list)
    assert result["overview"]["total_inspirations"] == 1
    assert result["error_distribution"] == [{"category": "超时", "count": 1}]


# ═══════════════════════════════════════════════════════════════
#  提示词版本标签
# ═══════════════════════════════════════════════════════════════


def test_prompt_hash_is_sha256_prefix():
    assert svc._prompt_hash("abc") == hashlib.sha256(b"abc").hexdigest()[:8]


def _patch_versions_file(monkeypatch, *, exists: bool, content: str = "{}") -> None:
    """选择性打桩 prompt_versions.json：只影响该文件名，其它文件读真实磁盘。"""
    real_exists = Path.exists
    real_read_text = Path.read_text

    def _fake_exists(self):
        if self.name == "prompt_versions.json":
            return exists
        return real_exists(self)

    def _fake_read_text(self, encoding=None, errors=None):
        if self.name == "prompt_versions.json":
            return content
        return real_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "exists", _fake_exists)
    monkeypatch.setattr(Path, "read_text", _fake_read_text)


def test_version_labels_current_prompts(monkeypatch):
    """当前提示词按内容哈希标成「当前提示词（模型）」；空 prompt 跳过。"""
    from app.services import model_prompt

    _patch_versions_file(monkeypatch, exists=False)
    monkeypatch.setattr(
        model_prompt,
        "get_all_model_prompts",
        lambda: {"m1": "prompt-A", "m2": "", "m3": "prompt-B"},
    )

    labels = svc._version_labels()

    assert labels == {
        svc._prompt_hash("prompt-A"): "当前提示词（m1）",
        svc._prompt_hash("prompt-B"): "当前提示词（m3）",
    }


def test_version_labels_history_file_labels(monkeypatch):
    """历史版本按模型内序号标「版本 #N」；坏条目/空 prompt/非列表跳过。"""
    from app.services import model_prompt

    _patch_versions_file(
        monkeypatch,
        exists=True,
        content='{"m1": [{"prompt": "hist-a"}, "junk", {"prompt": ""}], "m2": "not-list"}',
    )
    monkeypatch.setattr(model_prompt, "get_all_model_prompts", lambda: {})

    labels = svc._version_labels()

    assert labels == {svc._prompt_hash("hist-a"): "版本 #1（m1）"}


def test_version_labels_broken_json_tolerated(monkeypatch):
    """版本文件不是合法 JSON：降级为空映射，不让看板 500。"""
    from app.services import model_prompt

    _patch_versions_file(monkeypatch, exists=True, content="not-json")
    monkeypatch.setattr(model_prompt, "get_all_model_prompts", lambda: {})

    assert svc._version_labels() == {}


# ═══════════════════════════════════════════════════════════════
#  原始名称收集 / 裸词率
# ═══════════════════════════════════════════════════════════════


def test_collect_raw_names_variants():
    """原始响应重放：items/type/features 字符串与各种键都要收全（含将被合规丢弃的）。"""
    data = {
        "items": [
            {"type": ["白色"], "features": "宽松，修身"},
            "junk",  # 非 dict 跳过
            {"type": "  ", "features": ["纯棉"]},
        ],
        "style": "甜美",
        "fit": "修身",
        "design_detail": None,
        "attributes": [],
    }

    assert svc._collect_raw_names(data) == [
        "白色",
        "宽松",
        "修身",
        "纯棉",
        "甜美",
        "修身",
    ]


def test_collect_raw_names_tolerates_non_list_items():
    assert svc._collect_raw_names({"items": {"a": 1}}) == []
    assert svc._collect_raw_names({}) == []


def _patch_replay(monkeypatch, parsed: dict, bare_names: set[str]) -> None:
    """打桩重放链路：解析结果固定，合规判定只看给定集合。"""
    import app.services.ai_parser as ai_parser
    import app.utils.tag_compliance as compliance

    monkeypatch.setattr(ai_parser, "parse_analysis_response", lambda _raw: parsed)
    monkeypatch.setattr(
        compliance, "classify_noncompliant", lambda name: name in bare_names
    )


async def test_bare_word_rate_ratio_and_filters(client, monkeypatch):
    """裸词率 = 命中合规规则的名称占比；只统计指定版本×模型的成功日志。"""
    _patch_replay(
        monkeypatch,
        parsed={"items": [{"type": "白色", "features": ["宽松"]}]},
        bare_names={"宽松"},
    )
    insp = _insp()
    await _add(insp)
    since = utcnow() - timedelta(days=1)
    await _add(
        _log(insp.id, prompt_version="v1", raw_response="{}"),
        _log(insp.id, prompt_version="v1", raw_response="{}"),
        _log(insp.id, prompt_version="v2", raw_response="{}"),  # 版本不符
        _log(insp.id, prompt_version="v1", model_name="m2", raw_response="{}"),  # 模型不符
        _log(insp.id, prompt_version="v1", error="x", raw_response="{}"),  # 失败日志
        _log(insp.id, prompt_version="v1", raw_response=None),  # 无原文
    )

    async with async_session() as db:
        rate = await svc._bare_word_rate(db, "v1", "m1", since)

    # 2 条日志 × 2 个名称 = 4，其中「宽松」命中 2 次 → 50%
    assert rate == 50.0


async def test_bare_word_rate_none_version_and_no_sample(client, monkeypatch):
    """prompt_version=None 只匹配未记录版本的日志；样本为 0 返回 0.0。"""
    _patch_replay(monkeypatch, parsed={}, bare_names=set())
    insp = _insp()
    await _add(insp)
    since = utcnow() - timedelta(days=1)

    async with async_session() as db:
        assert await svc._bare_word_rate(db, "no-such-version", None, since) == 0.0

    await _add(_log(insp.id, prompt_version=None, raw_response="{}"))

    async with async_session() as db:
        # 解析结果为空 dict → 没有名称可统计 → 0.0（不是除零错误）
        assert await svc._bare_word_rate(db, None, None, since) == 0.0


# ═══════════════════════════════════════════════════════════════
#  提示词版本 × 模型质量对比
# ═══════════════════════════════════════════════════════════════


async def test_collect_prompt_quality_full_aggregation(client, monkeypatch):
    """按版本×模型聚合：成功率、平均标签数（结构化快照）、纠错率、最近使用时间。"""
    insp1 = _insp()
    insp2 = _insp()
    await _add(insp1, insp2)
    tag1 = Tag(name="白色", category="color")
    tag2 = Tag(name="JK", category="style")
    await _add(tag1, tag2)

    now = utcnow()
    async with async_session() as db:
        db.add_all(
            [
                # v1/m1：3 次分析（2 成功 1 失败）
                _log(insp1.id, prompt_version="v1", created_at=now - timedelta(hours=3)),
                _log(insp2.id, prompt_version="v1", created_at=now - timedelta(hours=2)),
                _log(
                    insp1.id,
                    prompt_version="v1",
                    error="x",
                    created_at=now - timedelta(hours=1),
                ),
                # v2/m2：1 次成功
                _log(insp2.id, prompt_version="v2", model_name="m2", created_at=now),
                # 窗口外与质量审核日志均不计
                _log(
                    insp1.id,
                    prompt_version="v1",
                    created_at=now - timedelta(days=60),
                ),
                _log(
                    insp1.id,
                    prompt_version="v1",
                    log_type="quality_check",
                    created_at=now,
                ),
            ]
        )
        await db.commit()
        log_rows = (
            await db.execute(select(AIAnalysisLog).order_by(AIAnalysisLog.id))
        ).scalars().all()
        v1_success = log_rows[0]  # 第 1 条成功日志
        v1_second = log_rows[1]  # 第 2 条成功日志
        v2_log = log_rows[3]
        # 成功日志的快照标签：v1 共 2 个标签 / 2 次成功 = 1.0；v2 2 个标签 / 1 次 = 2.0
        db.add_all(
            [
                AIAnalysisTag(log_id=v1_success.id, tag_id=tag1.id),
                AIAnalysisTag(log_id=v1_second.id, tag_id=tag1.id),
                AIAnalysisTag(log_id=v2_log.id, tag_id=tag1.id),
                AIAnalysisTag(log_id=v2_log.id, tag_id=tag2.id),
                TagCorrection(
                    inspiration_id=insp1.id,
                    tag_name="多标的",
                    reason="multi",
                    action="removed",
                    prompt_version="v1",
                    model_name="m1",
                    created_at=now,
                ),
                TagCorrection(
                    inspiration_id=insp1.id,
                    tag_name="漏标的",
                    reason="missing",
                    action="added",
                    prompt_version="v1",
                    model_name="m1",
                    created_at=now,
                ),
            ]
        )
        await db.commit()

    monkeypatch.setattr(svc, "_version_labels", lambda: {"v1": "版本 #1（m1）"})

    async with async_session() as db:
        result = await svc.collect_prompt_quality(db, days=30)

    assert result["days"] == 30
    assert result["total_versions"] == 2
    assert result["include_bare_rate"] is False

    first, second = result["items"]
    assert first["prompt_version"] == "v1"
    assert first["version_label"] == "版本 #1（m1）"
    assert first["model_name"] == "m1"
    assert first["analyses"] == 3
    assert first["successes"] == 2
    assert first["success_rate"] == 66.7
    assert first["avg_tags"] == 1.0  # 2 个快照标签 / 2 次成功
    assert first["corrections"] == 2
    assert first["correction_rate"] == 66.67  # 2 / 3 次分析
    assert first["last_used_at"].endswith("Z")
    assert "bare_rate" not in first

    assert second["prompt_version"] == "v2"
    assert second["version_label"] == "未登记版本（v2）"
    assert second["success_rate"] == 100.0
    assert second["avg_tags"] == 2.0
    assert second["corrections"] == 0


async def test_collect_prompt_quality_bare_rate_and_none_version(client, monkeypatch):
    """include_bare_rate=True 时逐版本算裸词率；None 版本显示「未登记版本（无）」。"""
    insp = _insp()
    await _add(insp)
    await _add(
        _log(insp.id, prompt_version=None),
        _log(insp.id, prompt_version="abcdef123456"),
    )
    calls: list[tuple] = []

    async def _fake_bare(_db, version, model, _since, sample=100):
        calls.append((version, model))
        return 12.5

    monkeypatch.setattr(svc, "_bare_word_rate", _fake_bare)
    monkeypatch.setattr(svc, "_version_labels", lambda: {})

    async with async_session() as db:
        result = await svc.collect_prompt_quality(db, days=7, include_bare_rate=True)

    assert result["days"] == 7
    assert result["include_bare_rate"] is True
    labels = {item["prompt_version"]: item["version_label"] for item in result["items"]}
    assert labels[None] == "未登记版本（无）"
    assert labels["abcdef123456"] == "未登记版本（abcdef12）"
    assert all(item["bare_rate"] == 12.5 for item in result["items"])
    assert set(calls) == {(None, "m1"), ("abcdef123456", "m1")}


async def test_collect_prompt_quality_empty(client):
    async with async_session() as db:
        result = await svc.collect_prompt_quality(db)

    assert result == {
        "days": 30,
        "total_versions": 0,
        "include_bare_rate": False,
        "items": [],
    }


# ═══════════════════════════════════════════════════════════════
#  统计口径：平均耗时与模型对比都必须排除垃圾桶素材（已修复）
# ═══════════════════════════════════════════════════════════════


async def test_overview_avg_time_should_exclude_trash(client):
    active = _insp()
    deleted = _insp(
        deleted_at=utcnow(), trash_reason="其他", trash_source="manual"
    )
    await _add(active, deleted)
    await _add(
        _log(active.id, processing_time_ms=100),
        _log(deleted.id, processing_time_ms=900),
    )

    async with async_session() as db:
        result = await svc._overview(db)

    assert result["avg_time_ms"] == 100


async def test_model_comparison_should_exclude_trash(client):
    active = _insp()
    deleted = _insp(
        deleted_at=utcnow(), trash_reason="其他", trash_source="manual"
    )
    await _add(active, deleted)
    await _add(
        _log(active.id),
        _log(deleted.id),
        _log(deleted.id, error="x"),
    )

    async with async_session() as db:
        rows = await svc._model_comparison(db)

    assert rows[0]["total"] == 1
    assert rows[0]["success"] == 1
