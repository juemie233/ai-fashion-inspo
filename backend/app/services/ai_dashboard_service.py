"""分析质量仪表盘服务：每日趋势、问题素材与覆盖率聚合。

此前该逻辑（100 行）整体写在 routers/ai_dashboard.py，按「路由薄、
业务在 services」约定下沉到本模块，并按指标维度拆成小函数。
"""

from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import AIAnalysisLog, Inspiration, analysis_log_filter
from app.models.tag import InspirationTag
from app.utils.time import format_utc, utcnow


async def _daily_trends(db: AsyncSession) -> list[dict]:
    """最近 30 天的每日分析统计（总量与成功量）。

    created_at 由 SQLite CURRENT_TIMESTAMP（UTC）生成，截止时间须用 UTC
    计算，避免非 UTC 时区下统计窗口偏移。
    """
    thirty_days_ago = utcnow() - timedelta(days=30)
    result = await db.execute(
        select(
            func.date(AIAnalysisLog.created_at).label("day"),
            func.count().label("total"),
            func.sum(case((AIAnalysisLog.error.is_(None), 1), else_=0)).label("success"),
        )
        .where(analysis_log_filter(), AIAnalysisLog.created_at >= thirty_days_ago)
        .group_by("day")
        .order_by("day")
    )
    return [
        {"day": row[0], "total": row[1], "success": row[2] or 0}
        for row in result.all()
    ]


async def _overview(db: AsyncSession) -> dict:
    """覆盖率概览：素材总数、已分析数、平均标签数与平均耗时（排除垃圾桶）。"""
    total_insp = (await db.execute(
        select(func.count(Inspiration.id)).where(Inspiration.deleted_at.is_(None))
    )).scalar() or 0

    # 已分析素材只统计未删除素材的日志，与 total_insp 口径一致，
    # 否则「已分析数 > 素材总数」导致未分析数为负
    analyzed_ids = (
        select(AIAnalysisLog.inspiration_id)
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(analysis_log_filter(), Inspiration.deleted_at.is_(None))
        .distinct()
    )
    analyzed_count = (await db.execute(
        select(func.count()).select_from(analyzed_ids.subquery())
    )).scalar() or 0

    avg_tags = 0
    if analyzed_count > 0:
        # 分子与分母口径一致：仅统计未删除素材的标签关联
        # （此前全表计数会把垃圾桶素材的标签计入，虚增平均数）
        tag_total = (await db.execute(
            select(func.count()).select_from(InspirationTag)
            .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
            .where(Inspiration.deleted_at.is_(None))
        )).scalar() or 0
        avg_tags = round(tag_total / analyzed_count, 1)

    avg_time = (await db.execute(
        select(func.avg(AIAnalysisLog.processing_time_ms))
        .where(analysis_log_filter(), AIAnalysisLog.error.is_(None))
    )).scalar() or 0

    return {
        "total_inspirations": total_insp,
        "analyzed_count": analyzed_count,
        "unanalyzed_count": max(0, total_insp - analyzed_count),
        "coverage_percent": round(analyzed_count / total_insp * 100, 1) if total_insp > 0 else 0,
        "avg_tags_per_image": avg_tags,
        "avg_time_ms": round(avg_time),
    }


async def _problem_items(db: AsyncSession) -> dict:
    """问题素材：多次失败（≥3 次）数量与零标签输出数量（排除垃圾桶）。"""
    fail_count_sub = (
        select(AIAnalysisLog.inspiration_id, func.count().label("fc"))
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(
            analysis_log_filter(),
            AIAnalysisLog.error.isnot(None),
            Inspiration.deleted_at.is_(None),
        )
        .group_by(AIAnalysisLog.inspiration_id)
        .having(func.count() >= 3)
        .subquery()
    )
    multi_fail = (await db.execute(
        select(func.count()).select_from(fail_count_sub)
    )).scalar() or 0

    zero_tag_result = await db.execute(
        select(func.count())
        .select_from(AIAnalysisLog)
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(
            analysis_log_filter(),
            AIAnalysisLog.error.is_(None),
            Inspiration.deleted_at.is_(None),
            ~AIAnalysisLog.inspiration_id.in_(
                select(InspirationTag.inspiration_id).distinct()
            ),
        )
    )
    zero_tag_count = zero_tag_result.scalar() or 0

    return {
        "multi_fail_count": multi_fail,
        "zero_tag_count": zero_tag_count,
    }


async def _model_comparison(db: AsyncSession) -> list[dict]:
    """按模型聚合的成功率对比（仅标签分析，排除质量审核日志与垃圾桶素材）。"""
    result = await db.execute(
        select(
            AIAnalysisLog.model_name,
            func.count().label("total"),
            func.sum(case((AIAnalysisLog.error.is_(None), 1), else_=0)).label("success"),
        )
        .where(analysis_log_filter())
        .group_by(AIAnalysisLog.model_name)
        .order_by(func.count().desc())
    )
    return [
        {
            "model_name": row[0],
            "total": row[1],
            "success": row[2] or 0,
            "success_rate": round((row[2] or 0) / row[1] * 100, 1) if row[1] else 0,
        }
        for row in result.all()
    ]


def _classify_error(error: str) -> str:
    """按关键词归类失败原因，供错误分布统计使用。"""
    if not error:
        return "未知"
    low = error.lower()
    if "timeout" in low or "超时" in error:
        return "超时"
    if "connect" in low or "connection" in low or "refused" in low or "连接" in error:
        return "连接失败"
    if "http" in low or "status" in low:
        return "HTTP 错误"
    if "parse" in low or "json" in low or "解析" in error or "格式" in error:
        return "解析失败"
    if "context" in low or "token" in low or "截断" in error:
        return "上下文/截断"
    return "其他"


async def _error_distribution(db: AsyncSession) -> list[dict]:
    """失败日志的错误原因分布（按关键词归类后聚合）。"""
    result = await db.execute(
        select(AIAnalysisLog.error)
        .where(analysis_log_filter(), AIAnalysisLog.error.isnot(None))
    )
    counter: dict[str, int] = {}
    for (error,) in result.all():
        category = _classify_error(error or "")
        counter[category] = counter.get(category, 0) + 1
    return [
        {"category": category, "count": count}
        for category, count in sorted(counter.items(), key=lambda x: -x[1])
    ]


async def _failed_items(db: AsyncSession, limit: int = 20) -> list[dict]:
    """最近失败的素材列表（供前端直达跳转），每个素材取最新一条失败日志。"""
    latest_sub = (
        select(AIAnalysisLog.inspiration_id, func.max(AIAnalysisLog.id).label("max_id"))
        .where(analysis_log_filter(), AIAnalysisLog.error.isnot(None))
        .group_by(AIAnalysisLog.inspiration_id)
        .subquery()
    )
    result = await db.execute(
        select(
            AIAnalysisLog.inspiration_id,
            AIAnalysisLog.model_name,
            AIAnalysisLog.error,
            AIAnalysisLog.created_at,
            Inspiration.thumbnail_path,
        )
        .join(latest_sub, AIAnalysisLog.id == latest_sub.c.max_id)
        .join(Inspiration, AIAnalysisLog.inspiration_id == Inspiration.id)
        .where(Inspiration.deleted_at.is_(None))
        .order_by(AIAnalysisLog.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "inspiration_id": row[0],
            "model_name": row[1],
            "error": row[2],
            "created_at": format_utc(row[3]),
            "thumbnail_path": row[4],
        }
        for row in result.all()
    ]


async def collect_quality_dashboard(db: AsyncSession) -> dict:
    """汇总分析质量仪表盘数据（原 routers/ai_dashboard.py 的 quality_dashboard 逻辑）。"""
    return {
        "daily_trends": await _daily_trends(db),
        "overview": await _overview(db),
        "problem_items": await _problem_items(db),
        "model_comparison": await _model_comparison(db),
        "error_distribution": await _error_distribution(db),
        "failed_items": await _failed_items(db),
    }


# ============ 提示词版本质量对比（AI 打标质量闭环 P2） ============


def _prompt_hash(prompt: str) -> str:
    """Prompt 内容哈希前 8 位（与 ai_analysis_log.prompt_version 写入口径一致）。"""
    import hashlib

    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]


def _version_labels() -> dict[str, str]:
    """构建 prompt_version 哈希 → 可读标签的映射。

    来源：prompt_configs.json 的当前提示词（标记「当前」）与
    prompt_versions.json 的历史版本（按模型内序号标记），让看板不必显示裸哈希。
    """
    import json
    from pathlib import Path

    from app.services.model_prompt import get_all_model_prompts

    labels: dict[str, str] = {}
    for model, prompt in get_all_model_prompts().items():
        if prompt:
            labels.setdefault(_prompt_hash(prompt), f"当前提示词（{model}）")

    versions_file = Path(__file__).resolve().parent.parent.parent / "prompt_versions.json"
    if versions_file.exists():
        try:
            data = json.loads(versions_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            for model, versions in data.items():
                if not isinstance(versions, list):
                    continue
                for idx, item in enumerate(versions, 1):
                    prompt = item.get("prompt") if isinstance(item, dict) else None
                    if prompt:
                        labels.setdefault(_prompt_hash(prompt), f"版本 #{idx}（{model}）")
    return labels


def _collect_raw_names(tags_data: dict) -> list[str]:
    """收集模型原始输出中的全部名称候选（含会被合规规则丢弃的裸词）。

    用于计算「裸词率」——快照是过滤后的结果，看不到模型原始的命名质量，
    必须回到 raw_response 重放。
    """
    from app.services.ai_parser import extract_tag_names

    names: list[str] = []
    items = tags_data.get("items") or []
    if not isinstance(items, list):
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_type = item.get("type", "")
        if isinstance(raw_type, list):
            raw_type = raw_type[0] if raw_type else ""
        if str(raw_type).strip():
            names.append(str(raw_type).strip())
        features = item.get("features", [])
        if isinstance(features, str):
            features = [
                p.strip()
                for p in features.replace("，", ",").replace("、", ",").split(",")
                if p.strip()
            ]
        for feat in features if isinstance(features, list) else []:
            names.extend(n for n in extract_tag_names(feat) if n)
    for key in (
        "style", "fit", "design_detail", "material", "attributes",
        "atmosphere", "expression", "leg_posture",
    ):
        values = tags_data.get(key) or []
        if not isinstance(values, list):
            values = [values] if values else []
        for value in values:
            names.extend(n for n in extract_tag_names(value) if n)
    return names


async def _bare_word_rate(
    db: AsyncSession,
    prompt_version: str | None,
    model_name: str | None,
    since: datetime,
    sample: int = 100,
) -> float:
    """采样重放该版本最近的原始响应，计算裸词率（%）＝命中合规规则的名称占比。

    仅取最近 sample 条成功日志，避免全量重放拖慢接口；样本为 0 时返回 0。
    """
    from app.services.ai_parser import parse_analysis_response
    from app.utils.tag_compliance import classify_noncompliant

    query = (
        select(AIAnalysisLog.raw_response)
        .where(
            analysis_log_filter(),
            AIAnalysisLog.created_at >= since,
            AIAnalysisLog.error.is_(None),
            AIAnalysisLog.raw_response.isnot(None),
        )
        .order_by(AIAnalysisLog.id.desc())
        .limit(sample)
    )
    if prompt_version is None:
        query = query.where(AIAnalysisLog.prompt_version.is_(None))
    else:
        query = query.where(AIAnalysisLog.prompt_version == prompt_version)
    if model_name is not None:
        query = query.where(AIAnalysisLog.model_name == model_name)

    rows = (await db.execute(query)).scalars().all()
    total = 0
    bare = 0
    for raw in rows:
        tags_data = parse_analysis_response(raw or "")
        if not tags_data:
            continue
        for name in _collect_raw_names(tags_data):
            total += 1
            if classify_noncompliant(name):
                bare += 1
    return round(bare / total * 100, 2) if total else 0.0


async def collect_prompt_quality(
    db: AsyncSession,
    days: int = 30,
    include_bare_rate: bool = False,
) -> dict:
    """按「提示词版本 × 模型」聚合打标质量指标（数据洞察页消费）。

    指标口径：
    - analyses / successes / success_rate：窗口内标签分析次数与成功率；
    - avg_tags：成功分析的**平均标签数**（基于 ai_extracted_tags 结构化快照）；
    - corrections / correction_rate：窗口内纠错反馈数与「每百次分析纠错数」
      （来自 tag_corrections，按 prompt_version 冗余聚合）；
    - bare_rate（可选）：采样重放 raw_response 计算的裸词率，反映模型原始命名质量。

    参数:
        days: 统计窗口天数
        include_bare_rate: 是否计算裸词率（需重放原始响应，较慢，默认关闭）
    """
    from app.models.inspiration import AIAnalysisTag
    from app.models.tag_correction import TagCorrection

    since = utcnow() - timedelta(days=days)

    rows = (
        await db.execute(
            select(
                AIAnalysisLog.prompt_version,
                AIAnalysisLog.model_name,
                func.count().label("total"),
                func.sum(case((AIAnalysisLog.error.is_(None), 1), else_=0)).label("success"),
                func.max(AIAnalysisLog.created_at).label("last_used"),
            )
            .where(analysis_log_filter(), AIAnalysisLog.created_at >= since)
            .group_by(AIAnalysisLog.prompt_version, AIAnalysisLog.model_name)
            .order_by(func.count().desc())
            .limit(50)
        )
    ).all()

    # 快照标签数（分母用成功次数，快照仅在成功且非空时写入）
    tag_rows = (
        await db.execute(
            select(
                AIAnalysisLog.prompt_version,
                AIAnalysisLog.model_name,
                func.count(AIAnalysisTag.id),
            )
            .join(AIAnalysisTag, AIAnalysisTag.log_id == AIAnalysisLog.id)
            .where(analysis_log_filter(), AIAnalysisLog.created_at >= since)
            .group_by(AIAnalysisLog.prompt_version, AIAnalysisLog.model_name)
        )
    ).all()
    tag_counts = {(r[0], r[1]): r[2] for r in tag_rows}

    # 纠错反馈数（按记录时冗余的 prompt_version / model_name 聚合）
    corr_rows = (
        await db.execute(
            select(
                TagCorrection.prompt_version,
                TagCorrection.model_name,
                func.count(),
            )
            .where(TagCorrection.created_at >= since)
            .group_by(TagCorrection.prompt_version, TagCorrection.model_name)
        )
    ).all()
    corr_counts = {(r[0], r[1]): r[2] for r in corr_rows}

    labels = _version_labels()
    items: list[dict] = []
    for version, model, total, success, last_used in rows:
        success = success or 0
        corrections = corr_counts.get((version, model), 0)
        tag_total = tag_counts.get((version, model), 0)
        item = {
            "prompt_version": version,
            # 历史日志的提示词可能已不在版本库中：回退显示哈希前缀，避免多行同名
            "version_label": labels.get(
                version or "", f"未登记版本（{version[:8] if version else '无'}）"
            ),
            "model_name": model,
            "analyses": total,
            "successes": success,
            "success_rate": round(success / total * 100, 1) if total else 0.0,
            "avg_tags": round(tag_total / success, 1) if success else 0.0,
            "corrections": corrections,
            "correction_rate": round(corrections / total * 100, 2) if total else 0.0,
            "last_used_at": format_utc(last_used),
        }
        if include_bare_rate:
            item["bare_rate"] = await _bare_word_rate(db, version, model, since)
        items.append(item)

    return {
        "days": days,
        "total_versions": len(items),
        "include_bare_rate": include_bare_rate,
        "items": items,
    }
