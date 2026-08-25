"""标签健康度扫描：孤儿/低频/低质命名/疑似重复检查项与健康评分。

规则常量集中于此，供扫描任务（task_runners/tag_health.py）、
路由层明细接口与测试复用。
"""

from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.utils.tag_normalizer import validate_tag_name

# 健康评分扣分规则：问题键 → (每百分点扣分, 扣分上限)
# 基数 100 分，按「问题标签占比（百分点）」扣分，每项有上限避免单项主导。
SCORE_RULES: dict[str, tuple[float, float]] = {
    "orphan": (0.5, 25.0),
    "low_frequency": (0.3, 15.0),
    "low_quality_name": (0.4, 20.0),
    "duplicate": (0.4, 20.0),
}

ISSUE_TYPES = ("orphan", "low_frequency", "low_quality_name", "duplicate")


async def _usage_counts(
    db: AsyncSession, tag_ids: list[int] | None = None
) -> dict[int, int]:
    """标签使用次数（口径与 tag_query 一致：仅统计未删除素材的关联）。

    参数:
        tag_ids: 为 None 时统计全部标签；否则只统计指定标签。
    """
    query = (
        select(Tag.id, func.count(Inspiration.id))
        .outerjoin(InspirationTag, Tag.id == InspirationTag.tag_id)
        .outerjoin(
            Inspiration,
            and_(
                InspirationTag.inspiration_id == Inspiration.id,
                Inspiration.deleted_at.is_(None),
            ),
        )
    )
    if tag_ids:
        query = query.where(Tag.id.in_(tag_ids))
    result = await db.execute(query.group_by(Tag.id))
    return {row[0]: row[1] for row in result.all()}


async def scan_tag_health(
    db: AsyncSession, duplicate_threshold: float = 0.75
) -> dict:
    """执行标签健康度扫描，返回评分与各问题标签明细。

    返回结构（写回任务 result，供 ``GET /api/tags/health/{issue_type}`` 分页读取）::

        {
            "total": 标签总数,
            "score": 健康评分(0-100),
            "duplicate_threshold": 重复相似度阈值,
            "issues": {
                "orphan":           {"count": n, "tag_ids": [...]},
                "low_frequency":    {"count": n, "tag_ids": [...]},
                "low_quality_name": {"count": n, "tag_ids": [...]},
                "duplicate":        {"count": n, "pairs": [{"tag_a_id", "tag_b_id", "similarity"}]},
            },
            "scanned_at": ISO8601,
        }

    结果只存紧凑 ID 列表（几千个标签量级仅几十 KB），明细由路由层按页
    关联标签表与使用次数返回，避免任务结果过大。
    """
    tags_result = await db.execute(select(Tag))
    tags = tags_result.scalars().all()
    total = len(tags)
    now = datetime.now(timezone.utc).isoformat()

    empty_issues: dict = {
        "orphan": {"count": 0, "tag_ids": []},
        "low_frequency": {"count": 0, "tag_ids": []},
        "low_quality_name": {"count": 0, "tag_ids": []},
        "duplicate": {"count": 0, "pairs": []},
    }
    if total == 0:
        return {
            "total": 0,
            "score": 100.0,
            "duplicate_threshold": duplicate_threshold,
            "issues": empty_issues,
            "scanned_at": now,
        }

    usage = await _usage_counts(db)

    # 孤儿：0 次关联；低频：恰好 1 次关联（与标签管理页 usage_count 口径一致）
    orphan_ids = [t.id for t in tags if usage.get(t.id, 0) == 0]
    low_frequency_ids = [t.id for t in tags if usage.get(t.id, 0) == 1]
    # 低质命名：复用创建时的校验规则（纯英文/过长/标点/描述句/hex 色等）
    low_quality_ids = [t.id for t in tags if not validate_tag_name(t.name)[0]]

    # 疑似重复：复用现有相似度扫描（类别分组 + 首字预过滤 + 线程池）
    from app.services.tag_query import find_duplicate_tag_pairs

    pairs, _ = await find_duplicate_tag_pairs(db, duplicate_threshold)
    duplicate_involved_ids = sorted(
        {p["tag_a"]["id"] for p in pairs} | {p["tag_b"]["id"] for p in pairs}
    )
    duplicate_pairs = [
        {
            "tag_a_id": p["tag_a"]["id"],
            "tag_b_id": p["tag_b"]["id"],
            "similarity": p["similarity"],
        }
        for p in pairs
    ]

    # 健康评分：100 - 各问题占比扣分（占比 = 问题标签数 / 总数 × 100 百分点）
    score = 100.0
    counts = {
        "orphan": len(orphan_ids),
        "low_frequency": len(low_frequency_ids),
        "low_quality_name": len(low_quality_ids),
        "duplicate": len(duplicate_involved_ids),
    }
    for key, (rate, cap) in SCORE_RULES.items():
        percent = counts[key] / total * 100.0
        score -= min(percent * rate, cap)
    score = max(0.0, round(score, 1))

    return {
        "total": total,
        "score": score,
        "duplicate_threshold": duplicate_threshold,
        "issues": {
            "orphan": {"count": len(orphan_ids), "tag_ids": orphan_ids},
            "low_frequency": {"count": len(low_frequency_ids), "tag_ids": low_frequency_ids},
            "low_quality_name": {"count": len(low_quality_ids), "tag_ids": low_quality_ids},
            "duplicate": {"count": len(duplicate_pairs), "pairs": duplicate_pairs},
        },
        "scanned_at": now,
    }


# ============ 明细读取（路由层调用） ============


async def _fetch_tag_items(
    db: AsyncSession, tag_ids: list[int]
) -> dict[int, dict]:
    """按 ID 批量取标签基础信息 + 使用次数，返回 {tag_id: item}。"""
    ids = list(tag_ids)
    if not ids:
        return {}
    result = await db.execute(select(Tag).where(Tag.id.in_(ids)))
    tags = {t.id: t for t in result.scalars().all()}
    usage = await _usage_counts(db, ids)
    return {
        tid: {
            "id": tid,
            "name": t.name,
            "category": t.category,
            "source": t.source,
            "parent_id": t.parent_id,
            "usage_count": usage.get(tid, 0),
        }
        for tid, t in tags.items()
    }


async def get_health_issue_detail(
    db: AsyncSession,
    result: dict,
    issue_type: str,
    page: int = 1,
    size: int = 50,
) -> dict:
    """从健康度扫描结果读取某问题类型的分页明细。

    参数:
        result: scan_tag_health 的返回结构（即任务 result）
        issue_type: orphan / low_frequency / low_quality_name / duplicate

    返回:
        {"issue_type", "total", "page", "size", "items"}
    """
    issues = result.get("issues", {})
    if issue_type not in issues:
        raise ValueError(f"未知问题类型: {issue_type}")
    issue = issues[issue_type]

    if issue_type == "duplicate":
        pairs = issue.get("pairs", [])
        total = len(pairs)
        start = (page - 1) * size
        page_pairs = pairs[start : start + size]
        ids = sorted(
            {p["tag_a_id"] for p in page_pairs} | {p["tag_b_id"] for p in page_pairs}
        )
        tag_items = await _fetch_tag_items(db, ids)
        return {
            "issue_type": issue_type,
            "total": total,
            "page": page,
            "size": size,
            "items": [
                {
                    "tag_a": tag_items.get(p["tag_a_id"]),
                    "tag_b": tag_items.get(p["tag_b_id"]),
                    "similarity": p["similarity"],
                }
                for p in page_pairs
            ],
        }

    tag_ids = issue.get("tag_ids", [])

    # 低质命名：扫描结果可能早于标签/规则的最新状态（已改名、阈值调整等）。
    # 这里实时复校，过滤掉当前已合法的过期 ID，避免合法标签仍挂在问题列表里；
    # 原因取实时校验结果，不再用含糊的「标签名不规范」兜底。
    if issue_type == "low_quality_name":
        tag_items_all = await _fetch_tag_items(db, tag_ids)
        items_all = []
        for tid in tag_ids:
            item = tag_items_all.get(tid)
            if item is None:
                continue
            ok, reason = validate_tag_name(item["name"])
            if not ok:
                item["reason"] = reason
                items_all.append(item)
        total = len(items_all)
        start = (page - 1) * size
        items = items_all[start : start + size]
        return {
            "issue_type": issue_type,
            "total": total,
            "page": page,
            "size": size,
            "items": items,
        }

    total = len(tag_ids)
    start = (page - 1) * size
    page_ids = tag_ids[start : start + size]
    tag_items = await _fetch_tag_items(db, page_ids)
    items = []
    for tid in page_ids:
        item = tag_items.get(tid)
        if item is None:
            continue
        items.append(item)
    return {
        "issue_type": issue_type,
        "total": total,
        "page": page,
        "size": size,
        "items": items,
    }
