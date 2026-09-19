"""采集 ROI 漏斗：按关键词 / 博主拆开看「采集量 → 入库量 → 质量合格率」。

两个维度的取数口径不同，但都**不猜、不摊派**：

- **关键词 / CDP 按博主采集**：走 `scraper_tasks` 的任务级 `items_found` / `items_added`；
  合格数来自入库时写了 `inspirations.scraper_task_id` 的素材，按任务精确归属。
- **f2 通道**：素材没有 `scraper_task_id`（该列外键指向 `scraper_tasks`，塞不下任务队列
  id），但每条素材都带 `source_author`，于是按来源作者直接聚合「入库 / 合格」，不借用
  任务级数字摊派。

诚实边界（随返回值一起给前端展示，避免把空样本读成 0%）：

- 关键词维度只统计**单关键词**任务：多关键词任务的 found/added 是任务级汇总，按词拆分
  只能编造，故整体排除并计入 `notes`；
- 合格率 = approved / (approved + rejected)——pending 是「还没审」，不能算不合格；
  只算仍在库（未进垃圾桶）且有归属的素材，样本为 0 时返回 ``None``（前端显示「—」）。
"""

from __future__ import annotations

import json
import logging

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import NOT_DELETED, Inspiration
from app.models.person import Blogger
from app.models.scraper import ScraperTask
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

"""天数上限：再长也只是把「全部历史」算进来，防止前端传离谱数字做无意义扫表。"""
_MAX_DAYS = 3650

"""素材质量状态：只认这三档，未知值按 pending 计（宁可显示为待审，不当作不合格）。"""
_QUALITY_KEYS = ("approved", "rejected", "pending")

"""单次 IN (...) 查询的 ID 分片大小（SQLite 变量上限 999，留余量）。"""
_ID_CHUNK = 800


def _parse_config(raw: str | None) -> dict:
    """解析 `scraper_tasks.config`（JSON 字符串）；坏数据当作空配置，不让整页 500。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("采集任务 config 不是合法 JSON，已按空配置处理")
        return {}
    return data if isinstance(data, dict) else {}


def _rate(numerator: int, denominator: int) -> float | None:
    """百分比（保留 1 位）；分母为 0 返回 None（前端显示「—」，不用 0% 冒充结论）。"""
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 1)


def _empty_bucket() -> dict:
    """一个维度的累计容器（tasks/found/added 为任务级，task_ids 供素材归属）。"""
    return {"tasks": 0, "found": 0, "added": 0, "task_ids": []}


def _tally_quality(counts: dict[str, int]) -> dict:
    """把 {quality: 数量} 归一到 approved / rejected / pending 三档。"""
    out = dict.fromkeys(_QUALITY_KEYS, 0)
    for quality, n in counts.items():
        key = quality if quality in out else "pending"
        out[key] += int(n)
    return out


def _quality_counts(material_counts: dict[str, int]) -> dict:
    """素材状态计数 → 合格率与三档明细（样本口径：approve + reject）。"""
    tally = _tally_quality(material_counts)
    return {
        "approved": tally["approved"],
        "rejected": tally["rejected"],
        "pending": tally["pending"],
        "approved_rate": _rate(tally["approved"], tally["approved"] + tally["rejected"]),
    }


async def get_collection_roi(
    db: AsyncSession, days: int = 180, limit: int = 20
) -> dict:
    """按关键词与博主聚合采集 ROI 漏斗。

    Args:
        db: 数据库会话。
        days: 统计窗口（天）；越界的值收敛到 1~3650。
        limit: 每个维度最多返回多少行（按入库量降序）。

    Returns:
        {
          "days": int,
          "by_keyword": [{keyword, tasks, found, added, add_rate,
                          attributed, approved, rejected, pending, approved_rate}],
          "by_author": [{name, channel, platform, tasks, found, added, add_rate,
                         imported, approved, rejected, pending, approved_rate}],
          "coverage": {tasks_scanned, multi_keyword_tasks, keyword_attributed,
                       f2_materials, notes: [...]},
        }
        ``channel`` ∈ ``cdp``（按博主采集任务）/ ``f2``（抖音增量下载）；
        f2 行没有任务级口径，``tasks`` / ``found`` / ``added`` / ``add_rate`` 为
        ``None``，只有 ``imported`` 与质量三档。
    """
    days = max(1, min(int(days), _MAX_DAYS))
    limit = max(1, min(int(limit), 200))
    since = utcnow() - timedelta(days=days)

    task_rows = (
        await db.execute(
            select(
                ScraperTask.id,
                ScraperTask.platform,
                ScraperTask.config,
                ScraperTask.items_found,
                ScraperTask.items_added,
            ).where(ScraperTask.created_at >= since)
        )
    ).all()

    keyword_buckets: dict[str, dict] = {}
    author_buckets: dict[str, dict] = {}
    attributed_task_ids: list[int] = []
    blogger_ids: set[int] = set()
    multi_keyword_tasks = 0

    for task_id, platform, raw_config, found, added in task_rows:
        config = _parse_config(raw_config)
        keywords = [str(k).strip() for k in (config.get("keywords") or []) if str(k).strip()]

        if len(keywords) == 1:
            bucket = keyword_buckets.setdefault(keywords[0], _empty_bucket())
            bucket["tasks"] += 1
            bucket["found"] += int(found or 0)
            bucket["added"] += int(added or 0)
            bucket["task_ids"].append(task_id)
            attributed_task_ids.append(task_id)
        elif len(keywords) > 1:
            # 任务级汇总无法按词拆分：排除而不是摊派（见模块 docstring）
            multi_keyword_tasks += 1

        blogger_id = config.get("blogger_id")
        if config.get("collect_mode") == "user" and blogger_id:
            try:
                blogger_id = int(blogger_id)
            except (TypeError, ValueError):
                blogger_id = 0
            if blogger_id:
                key = f"cdp-{blogger_id}"
                bucket = author_buckets.setdefault(
                    key,
                    {
                        **_empty_bucket(),
                        "channel": "cdp",
                        "blogger_id": blogger_id,
                        "platform": platform,
                    },
                )
                bucket["tasks"] += 1
                bucket["found"] += int(found or 0)
                bucket["added"] += int(added or 0)
                bucket["task_ids"].append(task_id)
                attributed_task_ids.append(task_id)
                blogger_ids.add(blogger_id)

    # ── 素材归属：一次查出「任务 → 质量状态计数」（只算未进垃圾桶的素材）──
    material_by_task: dict[int, dict[str, int]] = {}
    for start in range(0, len(set(attributed_task_ids)), _ID_CHUNK):
        chunk = sorted(set(attributed_task_ids))[start : start + _ID_CHUNK]
        rows = (
            await db.execute(
                select(
                    Inspiration.scraper_task_id,
                    Inspiration.quality_status,
                    func.count(Inspiration.id),
                )
                .where(Inspiration.scraper_task_id.in_(chunk), NOT_DELETED)
                .group_by(Inspiration.scraper_task_id, Inspiration.quality_status)
            )
        ).all()
        for task_id, quality, count in rows:
            bucket = material_by_task.setdefault(int(task_id), {})
            bucket[str(quality or "pending")] = int(count or 0)

    # 博主名（CDP 行）：素材侧没有名字，只能回表取
    blogger_names: dict[int, str] = {}
    if blogger_ids:
        name_rows = (
            await db.execute(
                select(Blogger.id, Blogger.name).where(Blogger.id.in_(sorted(blogger_ids)))
            )
        ).all()
        blogger_names = {int(bid): name for bid, name in name_rows}

    by_keyword = []
    for keyword, bucket in keyword_buckets.items():
        counts: dict[str, int] = {}
        for task_id in bucket["task_ids"]:
            for quality, n in material_by_task.get(int(task_id), {}).items():
                counts[quality] = counts.get(quality, 0) + n
        quality = _quality_counts(counts)
        by_keyword.append(
            {
                "keyword": keyword,
                "tasks": bucket["tasks"],
                "found": bucket["found"],
                "added": bucket["added"],
                "add_rate": _rate(bucket["added"], bucket["found"]),
                "attributed": quality["approved"] + quality["rejected"] + quality["pending"],
                **quality,
            }
        )

    by_author = []
    for bucket in author_buckets.values():
        counts = {}
        for task_id in bucket["task_ids"]:
            for quality, n in material_by_task.get(int(task_id), {}).items():
                counts[quality] = counts.get(quality, 0) + n
        quality = _quality_counts(counts)
        by_author.append(
            {
                "name": blogger_names.get(bucket["blogger_id"], f"# {bucket['blogger_id']}"),
                "channel": "cdp",
                "platform": bucket["platform"],
                "tasks": bucket["tasks"],
                "found": bucket["found"],
                "added": bucket["added"],
                "add_rate": _rate(bucket["added"], bucket["found"]),
                "imported": bucket["added"],
                **quality,
            }
        )

    # ── f2 通道：没有任务级 found/added，按来源作者聚合入库与质量 ──
    f2_rows = (
        await db.execute(
            select(
                Inspiration.source_author,
                Inspiration.quality_status,
                func.count(Inspiration.id),
            )
            .where(
                NOT_DELETED,
                Inspiration.source_type == "douyin",
                Inspiration.source_author.isnot(None),
                Inspiration.source_author != "",
                Inspiration.created_at >= since,
            )
            .group_by(Inspiration.source_author, Inspiration.quality_status)
        )
    ).all()

    f2_authors: dict[str, dict[str, int]] = {}
    for author, quality, count in f2_rows:
        counts = f2_authors.setdefault(str(author), {})
        counts[str(quality or "pending")] = counts.get(str(quality or "pending"), 0) + int(
            count or 0
        )

    f2_materials = 0
    for author, counts in f2_authors.items():
        quality = _quality_counts(counts)
        imported = quality["approved"] + quality["rejected"] + quality["pending"]
        f2_materials += imported
        by_author.append(
            {
                "name": author,
                "channel": "f2",
                "platform": "douyin",
                "tasks": None,
                "found": None,
                "added": None,
                "add_rate": None,
                "imported": imported,
                **quality,
            }
        )

    # 排序：CDP 行按入库量（任务口径）降序，f2 行按素材数降序；两组各自截断后合并
    def _sort_key(row: dict) -> tuple[int, int, str]:
        primary = row["imported"] if row["channel"] == "f2" else row["added"]
        return (-int(primary or 0), -int(row["imported"] or 0), str(row["name"]))

    by_author.sort(key=_sort_key)
    by_keyword.sort(key=lambda r: (-int(r["added"] or 0), str(r["keyword"])))

    keyword_attributed = sum(int(r["attributed"]) for r in by_keyword)
    notes: list[str] = []
    if multi_keyword_tasks:
        notes.append(
            f"{multi_keyword_tasks} 个多关键词任务的采集量未计入关键词维度"
            "（任务级汇总无法按词拆分，不做摊派）"
        )
    if keyword_attributed == 0 and keyword_buckets:
        notes.append(
            "关键词的合格率暂无样本：只有入库时写入 scraper_task_id 的素材能按任务归属，"
            "历史素材未带该关联（合格率对之后的新采集才有效）"
        )
    if not f2_authors:
        notes.append("窗口内没有 f2 导入的抖音素材，博主维度只有 CDP 采集任务")

    return {
        "days": days,
        "by_keyword": by_keyword[:limit],
        "by_author": by_author[:limit],
        "coverage": {
            "tasks_scanned": len(task_rows),
            "multi_keyword_tasks": multi_keyword_tasks,
            "keyword_attributed": keyword_attributed,
            "f2_materials": f2_materials,
            "notes": notes,
        },
    }
